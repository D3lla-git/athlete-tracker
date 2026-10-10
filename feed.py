"""
Highlights feed (Home | Highlights): approved sports photos and videos
(1 minute max) from verified athletes, with likes, reposts, shares,
comments and saved posts (bookmarks).

Only highlights that passed every approval are ever listed (status
'approved'); athletes that are suspended or not verified disappear from the
feed. Counters live on the highlight row and are changed with single
UPDATE statements, so busy posts never lose a like.
"""
import re
from datetime import datetime, timedelta

from sqlalchemy import case, func, literal, select, union_all, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import contains_eager

from models import (db, User, AthleteHighlight, HighlightLike, HighlightRepost,
                    HighlightSave, HighlightComment)

FEED_PAGE_SIZE = 8
FEED_MAX_PAGES = 60               # endless scrolling stops after ~480 posts
FEED_TABS = ('foryou', 'latest', 'saved')
FEED_SPORTS = ('Football', 'Basketball', 'Kickball')

COMMENT_MAX = 300
COMMENT_PAGE_SIZE = 30
COMMENT_RATE_LIMIT = 6            # comments per user ...
COMMENT_RATE_SECONDS = 60         # ... in this many seconds

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


def published_col():
    """When the post went live (older highlights: when they were reviewed / made)."""
    return func.coalesce(AthleteHighlight.published_at, AthleteHighlight.reviewed_at, AthleteHighlight.created_at)


def live_query():
    """Highlights everyone may see, with their athlete loaded in the same query."""
    return (
        AthleteHighlight.query
        .join(User, User.id == AthleteHighlight.user_id)
        .filter(
            AthleteHighlight.status == 'approved',
            User.role == 'Athlete',
            User.is_verified.is_(True),
            User.is_suspended.is_(False),
        )
        .options(contains_eager(AthleteHighlight.user))
    )


def get_live(highlight_id):
    return live_query().filter(AthleteHighlight.id == highlight_id).first()


def feed_query(tab, sport, viewer):
    query = live_query()
    if sport in FEED_SPORTS:
        query = query.filter(AthleteHighlight.sport == sport)

    published = published_col()
    if tab == 'saved':
        return (
            query.join(HighlightSave, HighlightSave.highlight_id == AthleteHighlight.id)
            .filter(HighlightSave.user_id == viewer.id)
            .order_by(HighlightSave.created_at.desc(), HighlightSave.id.desc())
        )
    if tab == 'latest':
        return query.order_by(published.desc(), AthleteHighlight.id.desc())

    # For you: newest days first; within a day, what people engage with most.
    engagement = (
        AthleteHighlight.like_count
        + 2 * AthleteHighlight.repost_count
        + 2 * AthleteHighlight.comment_count
        + AthleteHighlight.save_count
        + AthleteHighlight.share_count
    )
    return query.order_by(func.date(published).desc(), engagement.desc(), published.desc(), AthleteHighlight.id.desc())


def feed_page(query, page):
    """(posts, has_more) for page 1, 2, ..."""
    page = max(1, min(page, FEED_MAX_PAGES))
    rows = query.offset((page - 1) * FEED_PAGE_SIZE).limit(FEED_PAGE_SIZE + 1).all()
    return rows[:FEED_PAGE_SIZE], len(rows) > FEED_PAGE_SIZE and page < FEED_MAX_PAGES


def viewer_state(viewer, highlight_ids):
    """{'like': {ids}, 'repost': {ids}, 'save': {ids}} for the signed-in viewer (one query)."""
    state = {kind: set() for kind in REACTIONS}
    if not highlight_ids or not getattr(viewer, 'is_authenticated', False):
        return state
    ids = list(highlight_ids)
    parts = [
        select(literal(kind).label('kind'), model.highlight_id)
        .where(model.user_id == viewer.id, model.highlight_id.in_(ids))
        for kind, (model, _) in REACTIONS.items()
    ]
    for kind, hid in db.session.execute(union_all(*parts)).all():
        state[kind].add(hid)
    return state


def _bump(highlight_id, column, delta):
    col = getattr(AthleteHighlight, column)
    value = col + 1 if delta > 0 else case((col > 0, col - 1), else_=0)
    db.session.execute(update(AthleteHighlight).where(AthleteHighlight.id == highlight_id).values({column: value}))


def set_reaction(kind, highlight, user, on):
    """Like / repost / save on or off (repeating the same request changes nothing). Returns (on, count)."""
    model, column = REACTIONS[kind]
    existing = model.query.filter_by(highlight_id=highlight.id, user_id=user.id).first()

    if on and not existing:
        db.session.add(model(highlight_id=highlight.id, user_id=user.id))
        _bump(highlight.id, column, +1)
    elif not on and existing:
        db.session.delete(existing)
        _bump(highlight.id, column, -1)

    try:
        db.session.commit()
    except IntegrityError:            # the same tap arrived twice at once
        db.session.rollback()

    count = db.session.execute(select(getattr(AthleteHighlight, column)).where(AthleteHighlight.id == highlight.id)).scalar() or 0
    active = model.query.filter_by(highlight_id=highlight.id, user_id=user.id).first() is not None
    return active, count


def count_share(highlight):
    _bump(highlight.id, 'share_count', +1)
    db.session.commit()
    return db.session.execute(select(AthleteHighlight.share_count).where(AthleteHighlight.id == highlight.id)).scalar() or 0


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


def comment_rate_limited(user):
    since = datetime.utcnow() - timedelta(seconds=COMMENT_RATE_SECONDS)
    recent = HighlightComment.query.filter(
        HighlightComment.user_id == user.id, HighlightComment.created_at >= since
    ).count()
    return recent >= COMMENT_RATE_LIMIT


def add_comment(highlight, user, body):
    comment = HighlightComment(highlight_id=highlight.id, user_id=user.id, body=body)
    db.session.add(comment)
    _bump(highlight.id, 'comment_count', +1)
    db.session.commit()
    return comment


def delete_comment(comment):
    highlight_id = comment.highlight_id
    db.session.delete(comment)
    _bump(highlight_id, 'comment_count', -1)
    db.session.commit()


def comments_for(highlight_id, before_id=None, limit=COMMENT_PAGE_SIZE):
    """Newest first; (comments, has_more). Commenters that are suspended are hidden."""
    query = (
        HighlightComment.query
        .join(User, User.id == HighlightComment.user_id)
        .filter(HighlightComment.highlight_id == highlight_id, User.is_suspended.is_(False))
        .options(contains_eager(HighlightComment.user))
    )
    if before_id:
        query = query.filter(HighlightComment.id < before_id)
    rows = query.order_by(HighlightComment.id.desc()).limit(limit + 1).all()
    return rows[:limit], len(rows) > limit


def delete_children(highlight_id):
    """Remove a highlight's likes, reposts, saves and comments (before deleting it)."""
    for model in (HighlightLike, HighlightRepost, HighlightSave, HighlightComment):
        model.query.filter_by(highlight_id=highlight_id).delete(synchronize_session=False)


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
