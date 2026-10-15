"""
Highlights feed (Home | Highlights): approved sports photos and videos
(1 minute max) from verified athletes, with likes, reposts, shares,
comments and saved posts (bookmarks).

Only highlights that passed every approval are ever listed (status
'approved'); athletes that are suspended or not verified disappear from the
feed. Counters live on the highlight row and are changed with single
UPDATE statements, so busy posts never lose a like.

Everyone can react. A reaction belongs to an Actor: a verified member
(user_id) or an anonymous visitor (visitor_key from a cookie). The Super
Admin's analysis (engagement_breakdown) tells the two apart.
"""
import re
from datetime import datetime, timedelta

from sqlalchemy import and_, case, exists, func, literal, or_, select, union_all, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, contains_eager

from models import (db, User, AthleteHighlight, HighlightLike, HighlightRepost,
                    HighlightSave, HighlightShare, HighlightComment)

FEED_PAGE_SIZE = 8
FEED_MAX_PAGES = 60               # endless scrolling stops after ~480 posts
FEED_TABS = ('foryou', 'latest', 'saved')
FEED_SPORTS = ('Football', 'Basketball', 'Kickball')

COMMENT_MAX = 300
COMMENT_PAGE_SIZE = 30
COMMENT_RATE_LIMIT = 6            # comments per member / visitor ...
COMMENT_RATE_SECONDS = 60         # ... in this many seconds
VISITOR_COMMENTS_PER_HOUR = 20    # per visitor network (IP), on top of the above
VISITOR_REACTIONS_PER_HOUR = 150  # likes + reposts + saves per visitor network (IP)
VISITOR_NAME_MAX = 40

# kind -> (model, counter column name)
REACTIONS = {
    'like': (HighlightLike, 'like_count'),
    'repost': (HighlightRepost, 'repost_count'),
    'save': (HighlightSave, 'save_count'),
}

# Links in comments are refused (spam, scams, off-topic content).
_LINK_RE = re.compile(
    r'(https?:|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|ly|me|co|info|xyz|link|site|online|app|gg|tv|lr)\b)',
    re.IGNORECASE,
)
# Visitors may not pose as D.A.R.T. staff or official accounts.
_RESERVED_NAME_RE = re.compile(r'(admin|d\.?a\.?r\.?t|moderat|official|support|staff|verified)', re.IGNORECASE)


class Actor:
    """Who is reacting: a verified member, or an anonymous visitor."""
    __slots__ = ('user_id', 'visitor_key', 'ip_hash')

    def __init__(self, user_id=None, visitor_key=None, ip_hash=None):
        self.user_id = user_id
        self.visitor_key = visitor_key
        self.ip_hash = ip_hash

    @property
    def is_member(self):
        return self.user_id is not None

    def owns(self, model):
        """SQL condition: rows of `model` made by this member / visitor."""
        if self.is_member:
            return model.user_id == self.user_id
        return and_(model.user_id.is_(None), model.visitor_key == self.visitor_key)

    def fields(self):
        if self.is_member:
            return {'user_id': self.user_id}
        return {'visitor_key': self.visitor_key, 'ip_hash': self.ip_hash}


def published_col():
    """When the post went live (older highlights: when they were reviewed / made)."""
    return func.coalesce(AthleteHighlight.published_at, AthleteHighlight.reviewed_at, AthleteHighlight.created_at)


def _post_lead_conditions():
    """
    A post with several files is ONE feed item, led by its first approved
    file (likes, comments... belong to it), and shows only once every file
    in it has been reviewed (none still pending).
    """
    sibling = aliased(AthleteHighlight)
    first_approved = (
        select(func.min(sibling.id))
        .where(sibling.post_key == AthleteHighlight.post_key, sibling.status == 'approved')
        .scalar_subquery()
    )
    still_pending = exists().where(sibling.post_key == AthleteHighlight.post_key, sibling.status == 'pending')
    return or_(
        AthleteHighlight.post_key.is_(None),
        and_(AthleteHighlight.id == first_approved, ~still_pending),
    )


def live_query():
    """Feed posts everyone may see, with their athlete loaded in the same query."""
    return (
        AthleteHighlight.query
        .join(User, User.id == AthleteHighlight.user_id)
        .filter(
            AthleteHighlight.status == 'approved',
            User.role == 'Athlete',
            User.is_verified.is_(True),
            User.is_suspended.is_(False),
            _post_lead_conditions(),
        )
        .options(contains_eager(AthleteHighlight.user))
    )


def get_live(highlight_id):
    return live_query().filter(AthleteHighlight.id == highlight_id).first()


def lead_of(highlight_id):
    """The feed post a file belongs to (its lead), or None if not live."""
    item = db.session.get(AthleteHighlight, highlight_id)
    if not item or item.status != 'approved':
        return None
    if not item.post_key:
        return get_live(item.id)
    return live_query().filter(AthleteHighlight.post_key == item.post_key).first()


def post_slides(posts):
    """{post_key: [approved files in order]} for the multi-file posts on a page (one query)."""
    keys = {p.post_key for p in posts if p.post_key}
    if not keys:
        return {}
    slides = {}
    rows = (
        AthleteHighlight.query
        .filter(AthleteHighlight.post_key.in_(keys), AthleteHighlight.status == 'approved')
        .order_by(AthleteHighlight.post_position, AthleteHighlight.id)
        .all()
    )
    for row in rows:
        slides.setdefault(row.post_key, []).append(row)
    return slides


def hand_over_post(highlight):
    """
    The lead file of a multi-file post is being deleted: give its likes,
    reposts, saves, shares, comments and counters to the next approved file.
    Returns True if they were handed over. Caller deletes + commits.
    """
    if not highlight.post_key or highlight.status != 'approved':
        return False
    approved = (
        AthleteHighlight.query
        .filter(AthleteHighlight.post_key == highlight.post_key, AthleteHighlight.status == 'approved')
        .order_by(AthleteHighlight.id)
        .all()
    )
    if not approved or approved[0].id != highlight.id or len(approved) < 2:
        return False
    heir = approved[1]
    for model in (HighlightLike, HighlightRepost, HighlightSave, HighlightShare, HighlightComment):
        model.query.filter_by(highlight_id=highlight.id).update({'highlight_id': heir.id}, synchronize_session=False)
    for column in ('like_count', 'repost_count', 'comment_count', 'save_count', 'share_count'):
        setattr(heir, column, (getattr(heir, column) or 0) + (getattr(highlight, column) or 0))
    heir.published_at = highlight.published_at or heir.published_at
    return True


def feed_query(tab, sport, actor):
    query = live_query()
    if sport in FEED_SPORTS:
        query = query.filter(AthleteHighlight.sport == sport)

    if tab == 'saved':
        if actor is None:
            return query.filter(literal(False))
        return (
            query.join(HighlightSave, HighlightSave.highlight_id == AthleteHighlight.id)
            .filter(actor.owns(HighlightSave))
            .order_by(HighlightSave.created_at.desc(), HighlightSave.id.desc())
        )
    # For you and Latest: the newest post first, by when it was posted.
    return query.order_by(AthleteHighlight.created_at.desc(), AthleteHighlight.id.desc())


def feed_page(query, page):
    """(posts, has_more) for page 1, 2, ..."""
    page = max(1, min(page, FEED_MAX_PAGES))
    rows = query.offset((page - 1) * FEED_PAGE_SIZE).limit(FEED_PAGE_SIZE + 1).all()
    return rows[:FEED_PAGE_SIZE], len(rows) > FEED_PAGE_SIZE and page < FEED_MAX_PAGES


def viewer_state(actor, highlight_ids):
    """{'like': {ids}, 'repost': {ids}, 'save': {ids}} for this member / visitor (one query)."""
    state = {kind: set() for kind in REACTIONS}
    if not highlight_ids or actor is None:
        return state
    ids = list(highlight_ids)
    parts = [
        select(literal(kind).label('kind'), model.highlight_id)
        .where(actor.owns(model), model.highlight_id.in_(ids))
        for kind, (model, _) in REACTIONS.items()
    ]
    for kind, hid in db.session.execute(union_all(*parts)).all():
        state[kind].add(hid)
    return state


def _bump(highlight_id, column, delta):
    col = getattr(AthleteHighlight, column)
    value = col + 1 if delta > 0 else case((col > 0, col - 1), else_=0)
    db.session.execute(update(AthleteHighlight).where(AthleteHighlight.id == highlight_id).values({column: value}))


def _counter(highlight_id, column):
    return db.session.execute(select(getattr(AthleteHighlight, column)).where(AthleteHighlight.id == highlight_id)).scalar() or 0


def set_reaction(kind, highlight, actor, on):
    """Like / repost / save on or off (repeating the same request changes nothing). Returns (on, count)."""
    model, column = REACTIONS[kind]
    existing = model.query.filter(model.highlight_id == highlight.id, actor.owns(model)).first()

    if on and not existing:
        db.session.add(model(highlight_id=highlight.id, **actor.fields()))
        _bump(highlight.id, column, +1)
    elif not on and existing:
        db.session.delete(existing)
        _bump(highlight.id, column, -1)

    try:
        db.session.commit()
    except IntegrityError:            # the same tap arrived twice at once
        db.session.rollback()

    active = model.query.filter(model.highlight_id == highlight.id, actor.owns(model)).first() is not None
    return active, _counter(highlight.id, column)


def count_share(highlight, actor):
    """Count a share once per member / visitor. Returns the share count."""
    if not HighlightShare.query.filter(HighlightShare.highlight_id == highlight.id, actor.owns(HighlightShare)).first():
        db.session.add(HighlightShare(highlight_id=highlight.id, **actor.fields()))
        _bump(highlight.id, 'share_count', +1)
        db.session.commit()
    return _counter(highlight.id, 'share_count')


def visitor_reactions_limited(actor):
    """Visitors: too many likes / reposts / saves from one network in the last hour."""
    if actor.is_member or not actor.ip_hash:
        return False
    since = datetime.utcnow() - timedelta(hours=1)
    total = sum(
        model.query.filter(model.ip_hash == actor.ip_hash, model.created_at >= since).count()
        for model, _ in REACTIONS.values()
    )
    return total >= VISITOR_REACTIONS_PER_HOUR


# ---------- Comments ----------
def clean_comment(text):
    """(clean text, error message or None)."""
    body = ' '.join((text or '').split())
    if not body:
        return '', 'Write a comment first.'
    if len(body) > COMMENT_MAX:
        return '', f'Comments can be up to {COMMENT_MAX} characters.'
    if _LINK_RE.search(body):
        return '', 'Links are not allowed in comments.'
    return body, None


def clean_visitor_name(text):
    """(name, error or None). Empty -> 'Visitor'."""
    name = ' '.join((text or '').split())[:VISITOR_NAME_MAX]
    if not name:
        return 'Visitor', None
    if _LINK_RE.search(name) or _RESERVED_NAME_RE.search(name) or not re.search(r'[A-Za-z]', name):
        return '', 'Please use your real first name (no links, no "Admin" or "D.A.R.T.").'
    return name, None


def comment_rate_limited(actor):
    now = datetime.utcnow()
    since = now - timedelta(seconds=COMMENT_RATE_SECONDS)
    if actor.is_member:
        mine = HighlightComment.user_id == actor.user_id
    else:
        # A visitor: their cookie, or anyone on the same network (new cookies don't help).
        mine = and_(HighlightComment.user_id.is_(None),
                    or_(HighlightComment.visitor_key == actor.visitor_key, HighlightComment.ip_hash == actor.ip_hash))
    if HighlightComment.query.filter(mine, HighlightComment.created_at >= since).count() >= COMMENT_RATE_LIMIT:
        return True
    if not actor.is_member and actor.ip_hash:
        hour = HighlightComment.query.filter(
            HighlightComment.user_id.is_(None), HighlightComment.ip_hash == actor.ip_hash,
            HighlightComment.created_at >= now - timedelta(hours=1),
        ).count()
        return hour >= VISITOR_COMMENTS_PER_HOUR
    return False


def add_comment(highlight, actor, body, visitor_name=None, visitor_device=None):
    comment = HighlightComment(highlight_id=highlight.id, body=body, **actor.fields(),
                               visitor_name=None if actor.is_member else (visitor_name or 'Visitor'),
                               visitor_device=None if actor.is_member else (visitor_device or None))
    db.session.add(comment)
    _bump(highlight.id, 'comment_count', +1)
    db.session.commit()
    return comment


def edit_comment(comment, body):
    comment.body = body
    comment.edited_at = datetime.utcnow()
    db.session.commit()
    return comment


def delete_comment(comment):
    highlight_id = comment.highlight_id
    db.session.delete(comment)
    _bump(highlight_id, 'comment_count', -1)
    db.session.commit()


def comments_for(highlight_id, before_id=None, limit=COMMENT_PAGE_SIZE):
    """Newest first; (comments, has_more). Comments of suspended members are hidden."""
    query = (
        HighlightComment.query
        .outerjoin(User, User.id == HighlightComment.user_id)
        .filter(HighlightComment.highlight_id == highlight_id,
                or_(HighlightComment.user_id.is_(None), User.is_suspended.is_(False)))
        .options(contains_eager(HighlightComment.user))
    )
    if before_id:
        query = query.filter(HighlightComment.id < before_id)
    rows = query.order_by(HighlightComment.id.desc()).limit(limit + 1).all()
    return rows[:limit], len(rows) > limit


def delete_children(highlight_id):
    """Remove a highlight's likes, reposts, saves, shares and comments (before deleting it)."""
    for model in (HighlightLike, HighlightRepost, HighlightSave, HighlightShare, HighlightComment):
        model.query.filter_by(highlight_id=highlight_id).delete(synchronize_session=False)


# ---------- D.A.R.T Admin: engagement analysis ----------
ENGAGEMENT_KINDS = (
    ('like', HighlightLike), ('comment', HighlightComment), ('save', HighlightSave),
    ('share', HighlightShare), ('repost', HighlightRepost),
)


def engagement_breakdown(since=None, highlight_ids=None):
    """
    {highlight id: {kind: [members, visitors]}} for likes, comments, saves
    (bookmarks), shares and reposts, counted from the rows themselves.
    since: only activity from then on. One grouped query per kind.
    """
    result = {}
    for kind, model in ENGAGEMENT_KINDS:
        query = db.session.query(
            model.highlight_id,
            func.sum(case((model.user_id.isnot(None), 1), else_=0)),
            func.sum(case((model.user_id.is_(None), 1), else_=0)),
        )
        if since is not None:
            query = query.filter(model.created_at >= since)
        if highlight_ids is not None:
            query = query.filter(model.highlight_id.in_(list(highlight_ids)))
        for hid, members, visitors in query.group_by(model.highlight_id).all():
            row = result.setdefault(hid, {k: [0, 0] for k, _ in ENGAGEMENT_KINDS})
            row[kind] = [int(members or 0), int(visitors or 0)]
    return result


def empty_engagement():
    return {k: [0, 0] for k, _ in ENGAGEMENT_KINDS}


def engagement_totals(breakdown):
    totals = empty_engagement()
    for row in breakdown.values():
        for kind, (members, visitors) in row.items():
            totals[kind][0] += members
            totals[kind][1] += visitors
    return totals


def total_reactions(row):
    """Every reaction counted once: likes + comments + saves + shares + reposts (members + visitors)."""
    return sum(members + visitors for members, visitors in row.values())


def engagement_score(row):
    """Same weights as "For you": comments and reposts count double."""
    def total(kind):
        return sum(row[kind])
    return total('like') + 2 * total('comment') + 2 * total('repost') + total('save') + total('share')


# ---------- display helpers ----------
def time_ago(moment, now=None):
    """'now', '5m', '3h', '2d', 'Mar 4' (same style as X)."""
    if not moment:
        return ''
    now = now or datetime.utcnow()
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return 'now'
    if seconds < 3600:
        return f'{seconds // 60}m'
    if seconds < 86400:
        return f'{seconds // 3600}h'
    if seconds < 7 * 86400:
        return f'{seconds // 86400}d'
    return moment.strftime('%b %d' if moment.year == now.year else '%b %d, %Y').replace(' 0', ' ')


def compact_count(n):
    """1234 -> '1.2K' (like X)."""
    n = int(n or 0)
    if n < 1000:
        return str(n) if n else ''
    if n < 1_000_000:
        return f'{n / 1000:.1f}'.rstrip('0').rstrip('.') + 'K'
    return f'{n / 1_000_000:.1f}'.rstrip('0').rstrip('.') + 'M'
