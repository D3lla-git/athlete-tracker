"""
The bell: in-app notifications about the user's own approvals, plus new
awards (Highlight / Goal / Assist of the Week / Month) for every athlete.

  Athletes       - profile approved (live), each record approved
  Coaches        - coach account approved
  Scouts         - scout account approved
  Organizations  - organization verified

They are created automatically whenever an account's is_verified turns on
or a record's status becomes 'approved', whatever page did it (coach,
organization, D.A.R.T Admin, counter-signature, match report), so no
approval can be missed.
"""
from datetime import datetime

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from models import db, User, SportRecord, Notification

LIST_LIMIT = 50

ACCOUNT_MESSAGES = {
    'Athlete': ('profile_approved', 'Your athlete profile is approved and live on D.A.R.T. Scouts can now find you.', '/profile'),
    'Coach': ('account_approved', 'Your coach account is approved. You can now review your athletes and their records.', '/admin'),
    'Scout': ('account_approved', 'Your scout account is verified. You can now search and contact athletes.', '/scout'),
    'Organization': ('account_approved', 'Your organization is verified by D.A.R.T. Your roster is now live.', '/organization'),
}


def _turned_on(obj, attr):
    hist = inspect(obj).attrs[attr].history
    return bool(hist.added) and hist.added[0] is True and not (hist.deleted and hist.deleted[0] is True)


def _became_approved(record):
    hist = inspect(record).attrs.status.history
    return bool(hist.added) and hist.added[0] == 'approved' and not (hist.deleted and hist.deleted[0] == 'approved')


def _record_message(record):
    when = record.game_date.strftime('%d %b %Y') if record.game_date else str(record.year or '')
    opponent = f' vs {record.team_played_against}' if record.team_played_against else ''
    return f'Your {record.sport} record{opponent} ({when}) is approved and now on your profile.'


@event.listens_for(Session, 'before_flush')
def _create_notifications(session, flush_context, instances):
    for obj in list(session.dirty):
        if isinstance(obj, User) and obj.id and _turned_on(obj, 'is_verified'):
            entry = ACCOUNT_MESSAGES.get(obj.role)
            if entry:
                kind, message, link = entry
                session.add(Notification(user_id=obj.id, kind=kind, message=message, link=link))
        elif isinstance(obj, SportRecord) and obj.id and obj.user_id and _became_approved(obj):
            session.add(Notification(user_id=obj.user_id, kind='record_approved', message=_record_message(obj)[:300],
                                     link='/student#my-records'))


def notify(user_id, kind, message, link=None):
    """One bell notification (e.g. a seller's D.A.R.T Market$ listing approved). Caller commits."""
    db.session.add(Notification(user_id=user_id, kind=kind, message=(message or '')[:300], link=link))


def notify_new_award(contest):
    """
    A new award (e.g. Goal of the Week) reaches every athlete on D.A.R.T.
    (team and individual): one bell notification each, inserted in one go.
    Returns how many were sent. Caller commits.
    """
    athlete_ids = [uid for (uid,) in db.session.query(User.id).filter(
        User.role == 'Athlete', User.is_verified.is_(True), User.is_suspended.is_(False))]
    return _insert_award_notices(contest, athlete_ids)


def award_message(contest):
    deadline = contest.entry_deadline.strftime('%d %b, %H:%M')
    return (f'The D.A.R.T Admin posted a new award: {contest.title}! Enter your best approved highlight by '
            f'{deadline} UTC, then ask your supporters, family and fans to vote for you.')[:300]


def _award_link(contest):
    return f'/awards#contest{contest.id}'


def _insert_award_notices(contest, user_ids):
    from sqlalchemy import insert
    if not user_ids:
        return 0
    now = datetime.utcnow()
    db.session.execute(insert(Notification), [
        {'user_id': uid, 'kind': 'award_new', 'message': award_message(contest), 'link': _award_link(contest), 'created_at': now}
        for uid in user_ids])
    return len(user_ids)


def catch_up_award_notices(user):
    """
    Athletes who haven't entered an award that is still taking entries, and
    were never told about it (awards created before the bell, or athletes who
    joined / went live later), get its notification now. Returns how many.
    Caller commits.
    """
    if getattr(user, 'role', None) != 'Athlete' or not user.is_verified or user.is_suspended:
        return 0
    from models import AwardContest, AwardEntry
    now = datetime.utcnow()
    open_contests = AwardContest.query.filter(AwardContest.opens_at <= now, AwardContest.entry_deadline > now).all()
    if not open_contests:
        return 0
    entered = {cid for (cid,) in db.session.query(AwardEntry.contest_id).filter(
        AwardEntry.athlete_id == user.id, AwardEntry.contest_id.in_([c.id for c in open_contests]))}
    told = {link for (link,) in db.session.query(Notification.link).filter(
        Notification.user_id == user.id, Notification.kind == 'award_new')}
    sent = 0
    for c in open_contests:
        if c.id not in entered and _award_link(c) not in told:
            sent += _insert_award_notices(c, [user.id])
    return sent


def unread_count(user):
    if not getattr(user, 'is_authenticated', False):
        return 0
    return Notification.query.filter(Notification.user_id == user.id, Notification.read_at.is_(None)).count()


def latest(user, limit=LIST_LIMIT):
    return (Notification.query.filter_by(user_id=user.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all())


def mark_all_read(user):
    """Caller commits."""
    Notification.query.filter(Notification.user_id == user.id, Notification.read_at.is_(None)).update(
        {'read_at': datetime.utcnow()}, synchronize_session=False)
