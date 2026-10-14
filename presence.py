"""
Live users on D.A.R.T.: members AND visitors without an account.

Every browser session gets a random id (kept in the signed session cookie).
Opening a page, or the page's quiet 2-minute ping while it stays open,
updates one row (at most once a minute). Someone is "live" when seen in the
last LIVE_MINUTES. Bots / link previews are not counted. No personal data is
stored: only the random id, the member's user id (if signed in) and times.
"""
import random
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func

from models import db, SitePresence

LIVE_MINUTES = 5
TOUCH_EVERY_SECONDS = 60
KEEP_DAYS = 2
_BOT_RE = re.compile(r'bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram|discord|slack|curl|wget|python|'
                     r'headless|lighthouse|monitor|pingdom|uptime', re.I)


def is_bot(user_agent):
    return not user_agent or bool(_BOT_RE.search(user_agent))


def session_id(session):
    sid = session.get('_pid')
    if not sid or not isinstance(sid, str) or len(sid) != 32:
        sid = secrets.token_hex(16)
        session['_pid'] = sid
    return sid


def touch(session, user_id, now_ts, force=False):
    """Mark this browser session as seen (throttled). Returns True if written."""
    last = session.get('_pt') or 0
    if not force and now_ts - float(last) < TOUCH_EVERY_SECONDS:
        return False
    sid = session_id(session)
    now = datetime.utcnow()
    row = db.session.get(SitePresence, sid)
    if row is None:
        db.session.add(SitePresence(sid=sid, user_id=user_id, first_seen=now, last_seen=now))
    else:
        row.last_seen = now
        row.user_id = user_id   # signed in / out on the same browser: counted as what they are now
    # Now and then, forget sessions not seen for KEEP_DAYS.
    if random.random() < 0.02:
        SitePresence.query.filter(SitePresence.last_seen < now - timedelta(days=KEEP_DAYS)).delete(synchronize_session=False)
    db.session.commit()
    session['_pt'] = now_ts
    return True


def live_counts(minutes=LIVE_MINUTES):
    """{'members', 'visitors', 'total', 'visitors_24h', 'members_24h'}: live now and in the last 24 hours."""
    now = datetime.utcnow()
    since = now - timedelta(minutes=minutes)
    day = now - timedelta(hours=24)
    members = (db.session.query(func.count(func.distinct(SitePresence.user_id)))
               .filter(SitePresence.last_seen >= since, SitePresence.user_id.isnot(None)).scalar() or 0)
    visitors = SitePresence.query.filter(SitePresence.last_seen >= since, SitePresence.user_id.is_(None)).count()
    members_day = (db.session.query(func.count(func.distinct(SitePresence.user_id)))
                   .filter(SitePresence.last_seen >= day, SitePresence.user_id.isnot(None)).scalar() or 0)
    visitors_day = SitePresence.query.filter(SitePresence.last_seen >= day, SitePresence.user_id.is_(None)).count()
    return {'members': members, 'visitors': visitors, 'total': members + visitors,
            'members_24h': members_day, 'visitors_24h': visitors_day}
