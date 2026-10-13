"""
D.A.R.T Predictions: a FREE game. No money is ever involved.

The D.A.R.T Admin posts questions such as "Will <athlete> score at least 2
goals vs <team> on <date>?" about ADULT (18+) PREMIUM athletes only.
Non-athlete members (coaches, scouts, organizations, vendors) answer Yes /
No before the question closes. Once the game's numbers are verified (an
approved record, or the D.A.R.T Admin's result), correct picks earn points
and a leaderboard ranks the best predictors.

Athletes and match officials never take part (they could influence or
verify the numbers), and athletes under 18 are never the subject.
"""
from datetime import datetime

from sqlalchemy import func

from models import db, User, SportRecord, PredictionQuestion, PredictionPick

ADULT_AGE = 18
POINTS_CORRECT = 10
PREDICTOR_ROLES = ('Coach', 'Scout', 'Organization', 'Vendor')
STATS = {
    'Football': {'goals': 'goals', 'assists': 'assists', 'saves': 'saves'},
    'Basketball': {'points': 'points', 'assists': 'assists', 'rebounds': 'rebounds'},
    'Kickball': {'home_runs': 'home runs'},
}


def stat_label(stat):
    return stat.replace('_', ' ')


def athlete_error(athlete, age, is_premium):
    """Why this athlete can't be the subject of a question (None = OK)."""
    if not athlete or athlete.role != 'Athlete' or not athlete.is_verified or athlete.is_suspended:
        return 'Choose a verified athlete.'
    if age is None or age < ADULT_AGE:
        return 'Predictions are only about athletes aged 18 or over.'
    if not is_premium:
        return 'Predictions are only about premium athletes.'
    return None


def predictor_error(user):
    if not getattr(user, 'is_authenticated', False):
        return 'Log in with a D.A.R.T. account to play (it is free).'
    if user.is_suspended:
        return 'Your account cannot play.'
    if user.role not in PREDICTOR_ROLES:
        return 'Athletes and match officials do not take part in predictions.'
    return None


def is_open(q, now=None):
    return q.status == 'open' and (now or datetime.utcnow()) < q.closes_at


def record_value(q):
    """The athlete's verified number for that game (approved record on the match date), or None."""
    rec = (SportRecord.query.filter_by(user_id=q.athlete_id, sport=q.sport, game_date=q.match_date, status='approved')
           .order_by(SportRecord.id.desc()).first())
    if not rec:
        return None
    if q.stat == 'rebounds':
        return (rec.offensive_rebounds or 0) + (rec.defensive_rebounds or 0)
    return int(getattr(rec, q.stat, 0) or 0)


def settle(q, value):
    """Record the result and give points to correct picks. Caller commits."""
    q.result_value = int(value)
    q.status = 'settled'
    q.settled_at = datetime.utcnow()
    reached = q.result_value >= q.line
    for p in q.picks:
        p.points = POINTS_CORRECT if (p.pick == 'yes') == reached else 0


def void(q):
    q.status = 'void'
    q.settled_at = datetime.utcnow()
    for p in q.picks:
        p.points = 0


def leaderboard(limit=20):
    rows = (db.session.query(User, func.coalesce(func.sum(PredictionPick.points), 0).label('pts'),
                             func.count(PredictionPick.id).label('n'))
            .join(PredictionPick, PredictionPick.user_id == User.id)
            .group_by(User.id).order_by(func.coalesce(func.sum(PredictionPick.points), 0).desc(), User.id)
            .limit(limit).all())
    return [(u, int(pts), int(n)) for u, pts, n in rows if pts or n]


def split(q):
    yes = sum(1 for p in q.picks if p.pick == 'yes')
    return yes, len(q.picks) - yes
