"""
Record integrity: stopping coach-athlete bribery for fake records.

No record reaches an athlete's public CV on one person's word:

1. Two independent sign-offs. The athlete's coach approves, then someone
   with no stake in that team counter-signs: the OPPOSING team's coach (when
   they are on D.A.R.T. and don't also coach the athlete's team), otherwise a
   verified MATCH OFFICIAL (journalist, league secretary, school sports
   coordinator, referee), otherwise the D.A.R.T Admin. If the counter-signer
   says the numbers are false, the record goes to the Dispute Queue.
2. Match reports. A match official's report of the same game is compared
   with the record: same numbers -> counter-signed automatically; different
   numbers -> disputed automatically.
3. Permanent history. Every step is written to record_verification_event,
   hash-chained per record (any change breaks the chain) and protected from
   UPDATE / DELETE in the database. Coaches are tied to a Liberian mobile
   number; two fraud findings revoke their D.A.R.T. licence for good.
4. Anomaly detection. A game far above the athlete's own average (or
   extreme for any single game) is held until a match sheet or a highlight
   video is attached as proof.
"""
import hashlib
import json
import re
from datetime import datetime

from sqlalchemy import func

from models import db, User, SportRecord, RecordVerificationEvent, MatchReport, MatchReportLine, CoachTeam
import coaching

# ---------- settings ----------
FRAUD_FLAG_LIMIT = 2                 # upheld fraud findings before a coach loses the licence
COUNTERSIGN_ESCALATE_DAYS = 7        # unanswered requests are highlighted for the D.A.R.T Admin
OFFICIAL_TYPES = (
    'Sports journalist', 'League secretary', 'School sports coordinator',
    'Community youth leader', 'Referee / match official',
    'Linesman / assistant referee', 'Spectator (verified fan)',
)
FEDERATION = {'Football': 'Liberia Football Association (LFA)', 'Basketball': 'Liberia Basketball Association (LBA)',
              'Kickball': 'Liberia National Kickball Federation'}

# Liberian mobile numbers: Orange (077...) and Lonestar Cell MTN (088..., 055...).
_PHONE_RE = re.compile(r'^(?:\+?231|0)?\s*(77|88|55)\s*(\d{3})\s*(\d{4})$')

# The numbers compared / watched per sport: (label, record attribute or callable, match-report field)
KEY_STATS = {
    'Football': [('goals', 'goals', 'goals'), ('assists', 'assists', 'assists')],
    'Basketball': [('points', 'points', 'points'), ('rebounds', lambda r: (r.offensive_rebounds or 0) + (r.defensive_rebounds or 0), 'rebounds'),
                   ('blocks', 'blocks', 'blocks')],
    'Kickball': [('home runs', 'home_runs', 'home_runs')],
}
# Anomaly rules per game: extreme for anyone (cap), or a jump vs the athlete's own
# average (needs 3+ earlier approved games): at least `mult` x the average AND
# `plus` above it. e.g. basketball 4 -> 45 points is held.
ANOMALY_RULES = {
    'Football': {'goals': {'cap': 4, 'mult': 3, 'plus': 3}, 'assists': {'cap': 5, 'mult': 3, 'plus': 3}},
    'Basketball': {'points': {'cap': 40, 'mult': 3, 'plus': 20}, 'rebounds': {'cap': 22, 'mult': 3, 'plus': 12},
                   'blocks': {'cap': 9, 'mult': 3, 'plus': 6}},
    'Kickball': {'home runs': {'cap': 4, 'mult': 3, 'plus': 3}},
}
HISTORY_MIN_GAMES = 3


def _value(record, getter):
    v = getter(record) if callable(getter) else getattr(record, getter, 0)
    return int(v or 0)


def key_numbers(record):
    """{'goals': 2, 'assists': 1} for the record's sport."""
    return {label: _value(record, getter) for label, getter, _ in KEY_STATS.get(record.sport, [])}


# ---------- 0. phone numbers ----------
def clean_phone(raw):
    """'0770 123 456' -> '+231770123456'; None if it is not an Orange / Lonestar mobile number."""
    text = re.sub(r'[\s\-().]', '', raw or '')
    m = _PHONE_RE.match(text)
    if not m:
        return None
    return '+231' + ''.join(m.groups())


def phone_taken(phone, exclude_user_id=None):
    q = User.query.filter(User.phone == phone, User.role.in_(('Coach', 'Statistician')))
    if exclude_user_id:
        q = q.filter(User.id != exclude_user_id)
    return db.session.query(q.exists()).scalar()


# ---------- 4. anomaly detection ----------
def anomaly_flags(record):
    """Human-readable reasons this record looks suspicious (empty = fine)."""
    rules = ANOMALY_RULES.get(record.sport)
    if not rules:
        return []
    games = max(1, int(record.games_played or 1))
    now = key_numbers(record)
    history = SportRecord.query.filter(
        SportRecord.user_id == record.user_id, SportRecord.sport == record.sport,
        SportRecord.status == 'approved', SportRecord.id != (record.id or 0),
    ).all()
    flags = []
    for label, rule in rules.items():
        per_game = now.get(label, 0) / games
        if per_game >= rule['cap']:
            flags.append(f'{now[label]} {label} in {games} game{"s" if games != 1 else ""} is extreme for one game')
            continue
        if len(history) >= HISTORY_MIN_GAMES:
            total_games = sum(max(1, int(h.games_played or 1)) for h in history)
            avg = sum(key_numbers(h).get(label, 0) for h in history) / total_games
            if per_game >= max(rule['mult'] * avg, avg + rule['plus']):
                flags.append(f'{per_game:g} {label} per game vs the usual {avg:.1f}')
    return flags


# ---------- 1. who counter-signs ----------
def _active_coaches_of(team_name):
    key = coaching._key(team_name)
    if not key:
        return []
    candidates = User.query.filter(
        User.role == 'Coach', User.is_verified.is_(True), User.is_suspended.is_(False), User.license_revoked.is_(False),
        db.or_(func.lower(func.trim(User.school)) == key,
               User.id.in_(db.session.query(CoachTeam.coach_id).filter(
                   CoachTeam.status == 'approved', CoachTeam.end_date.is_(None),
                   func.lower(func.trim(CoachTeam.team_name)) == key))),
    ).all()
    return [c for c in candidates if key in coaching.team_keys(c)]


def opposing_coaches(record, approving_coach_id=None):
    """Coaches of the opponent who have no stake in the athlete's team."""
    own_key = coaching._key(record.team)
    # Assistant coaches only attest their own team's records, never the opponent's.
    return [c for c in _active_coaches_of(record.team_played_against)
            if c.id != approving_coach_id and own_key not in coaching.team_keys(c) and not coaching.is_assistant(c)]


def official_independent(official, record):
    """A verified match official with no tie to either team of the record."""
    if not official or official.role != 'Statistician' or not official.is_verified or official.is_suspended:
        return False
    if official.id in (record.user_id, record.coach_approved_by):
        return False
    tied = coaching._key(official.school)
    return not tied or tied not in (coaching._key(record.team), coaching._key(record.team_played_against))


def can_countersign(user, record, via_report=False):
    """May `user` counter-sign / dispute this record now?"""
    if record.status != 'verifying' or not getattr(user, 'is_authenticated', False):
        return False
    if user.id in (record.user_id, record.coach_approved_by):
        return False
    if user.role == 'System':
        return True
    if user.role == 'Coach' and record.countersign_target == 'opposing_coach':
        return any(c.id == user.id for c in opposing_coaches(record, record.coach_approved_by))
    if user.role == 'Statistician':
        # Officials sign records routed to officials (or any record through
        # their match report), never for a team they are tied to.
        return (via_report or record.countersign_target == 'official') and official_independent(user, record)
    return False


# ---------- 2. match reports ----------
def _same_match(report, record):
    teams = {coaching._key(report.home_team), coaching._key(report.away_team)}
    return (report.sport == record.sport and record.game_date == report.match_date
            and {coaching._key(record.team), coaching._key(record.team_played_against)} == teams)


def report_line_for(record):
    """(report, line) of a match official's report covering this athlete in this game, or (None, None)."""
    if not record.game_date:
        return None, None
    reports = MatchReport.query.filter(MatchReport.sport == record.sport, MatchReport.match_date == record.game_date).all()
    for report in reports:
        if not _same_match(report, record):
            continue
        if not official_independent(report.official, record):
            continue
        for line in report.lines:
            if line.athlete_id == record.user_id:
                return report, line
    return None, None


def compare_with_report(record, line):
    """List of differences between the record and an official's report line."""
    diffs = []
    for label, getter, field in KEY_STATS.get(record.sport, []):
        mine, theirs = _value(record, getter), int(getattr(line, field) or 0)
        if mine != theirs:
            diffs.append(f'{label}: record {mine}, match report {theirs}')
    return diffs


# ---------- 3. permanent, chained history ----------
def _stats_hash(record):
    snapshot = {k: getattr(record, k, None) for k in (
        'user_id', 'sport', 'year', 'team', 'team_played_against', 'competition_category', 'games_played',
        'goals', 'assists', 'yellow_cards', 'red_cards', 'clean_sheets', 'saves', 'points', 'blocks',
        'offensive_rebounds', 'defensive_rebounds', 'home_runs', 'cut_base', 'man_of_the_match', 'mvp')}
    snapshot['game_date'] = record.game_date.isoformat() if record.game_date else None
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


def log_event(record, actor, action, detail=''):
    """Append one step to the record's chained history. Caller commits."""
    db.session.flush()
    last = (RecordVerificationEvent.query.filter_by(record_id=record.id)
            .order_by(RecordVerificationEvent.id.desc()).first())
    prev = last.event_hash if last else None
    stats_hash = _stats_hash(record)
    now = datetime.utcnow()
    actor_id = getattr(actor, 'id', None)
    actor_role = getattr(actor, 'role', None) or ('system' if actor is None else None)
    body = json.dumps([record.id, actor_id, actor_role, action, (detail or '')[:400], stats_hash, prev, now.isoformat()])
    event = RecordVerificationEvent(
        record_id=record.id, actor_id=actor_id, actor_role=actor_role, action=action, detail=(detail or '')[:400],
        stats_hash=stats_hash, prev_hash=prev, event_hash=hashlib.sha256(body.encode()).hexdigest(), created_at=now)
    db.session.add(event)
    return event


def history(record_id):
    return (RecordVerificationEvent.query.filter_by(record_id=record_id)
            .order_by(RecordVerificationEvent.id.asc()).all())


def chain_intact(events):
    """True if every event points at the one before it (nothing removed or reordered)."""
    prev = None
    for e in events:
        if e.prev_hash != prev:
            return False
        prev = e.event_hash
    return True


# ---------- the flow ----------
def on_submit(record, actor, check_anomalies=True):
    """New or edited record: reset sign-offs, check for anomalies, log. Caller commits."""
    record.status = 'pending'
    for field in ('coach_approved_by', 'coach_approved_at', 'countersign_target', 'countersigned_by', 'countersigned_at',
                  'countersign_role', 'dispute_reason', 'disputed_by', 'disputed_at'):
        setattr(record, field, None)
    flags = anomaly_flags(record) if check_anomalies else []
    record.anomaly_flags = '; '.join(flags)[:300] or None
    record.proof_required = bool(flags)
    if not flags:
        record.proof_path = record.proof_highlight_id = record.proof_uploaded_at = None
    db.session.flush()
    log_event(record, actor, 'submitted', ('Held for proof: ' + record.anomaly_flags) if flags else '')
    return flags


def on_coach_approve(record, coach):
    """
    The athlete's coach approved. Decide who counter-signs (or settle it at
    once with a match official's report). Returns the new status. Caller commits.
    """
    record.coach_approved_by = coach.id
    record.coach_approved_at = datetime.utcnow()
    log_event(record, coach, 'coach_approved', record.anomaly_flags and 'Proof attached for: ' + record.anomaly_flags or '')

    report, line = report_line_for(record)
    if line is not None:
        diffs = compare_with_report(record, line)
        if diffs:
            _dispute(record, report.official, 'Match report differs: ' + '; '.join(diffs), auto=True)
        else:
            _countersign(record, report.official, 'official', 'Matches the match report filed by a match official')
        return record.status

    record.status = 'verifying'
    record.countersign_target = 'opposing_coach' if opposing_coaches(record, coach.id) else 'official'
    return record.status


def _countersign(record, signer, role, detail=''):
    record.countersigned_by = getattr(signer, 'id', None)
    record.countersigned_at = datetime.utcnow()
    record.countersign_role = role
    record.status = 'approved'
    log_event(record, signer, 'countersigned', detail or f'Counter-signed by {role.replace("_", " ")}')


def _dispute(record, by, reason, auto=False):
    record.status = 'disputed'
    record.dispute_reason = (reason or '')[:300]
    record.disputed_by = getattr(by, 'id', None)
    record.disputed_at = datetime.utcnow()
    log_event(record, by, 'disputed_auto' if auto else 'disputed', record.dispute_reason)


def countersign(record, signer):
    role = {'System': 'admin', 'Coach': 'opposing_coach', 'Statistician': 'official'}[signer.role]
    _countersign(record, signer, role)


def dispute(record, by, reason):
    _dispute(record, by, reason)


def resolve_dispute(record, admin, decision, note=''):
    """
    D.A.R.T Admin decision on a disputed record:
      'true'        -> the numbers are right: approved
      'inaccurate'  -> honest mistake: rejected (athlete asked to correct), no strike
      'fraud'       -> fake: rejected; the approving coach gets a fraud flag
                       (FRAUD_FLAG_LIMIT flags -> licence revoked for good)
    Returns the approving coach if their licence was just revoked, else None. Caller commits.
    """
    now = datetime.utcnow()
    revoked = None
    if decision == 'true':
        _countersign(record, admin, 'admin', 'Dispute reviewed: the numbers are right. ' + (note or ''))
        return None
    record.status = 'rejected'
    record.rejection_reason = 'fake' if decision == 'fraud' else 'inaccurate'
    record.rejection_note = (note or record.dispute_reason or '')[:200]
    record.rejected_by = admin.id
    record.rejected_at = now
    log_event(record, admin, 'dispute_' + decision, note or '')
    if decision == 'fraud' and record.coach_approved_by:
        coach = db.session.get(User, record.coach_approved_by)
        if coach and coach.role == 'Coach':
            coach.fraud_flags = (coach.fraud_flags or 0) + 1
            log_event(record, admin, 'coach_flagged', f'{coach.full_name}: fraud flag {coach.fraud_flags} of {FRAUD_FLAG_LIMIT}')
            if coach.fraud_flags >= FRAUD_FLAG_LIMIT and not coach.license_revoked:
                coach.license_revoked = True
                coach.license_revoked_at = now
                coach.is_suspended = True
                coach.suspended_at = now
                coach.suspension_reason = 'D.A.R.T. licence revoked: fraudulent record approvals'
                log_event(record, admin, 'licence_revoked', coach.full_name)
                revoked = coach
    return revoked


def attach_proof(record, actor, path=None, highlight_id=None):
    record.proof_path = path
    record.proof_highlight_id = highlight_id
    record.proof_uploaded_at = datetime.utcnow()
    log_event(record, actor, 'proof_attached', 'match sheet' if path else f'highlight video #{highlight_id}')


def apply_match_report(report, official):
    """A new match report settles records already waiting for that game. Returns (confirmed, disputed)."""
    confirmed = disputed = 0
    waiting = SportRecord.query.filter(SportRecord.status == 'verifying', SportRecord.sport == report.sport,
                                       SportRecord.game_date == report.match_date).all()
    lines = {line.athlete_id: line for line in report.lines}
    for record in waiting:
        if record.user_id not in lines or not _same_match(report, record) or not can_countersign(official, record, via_report=True):
            continue
        diffs = compare_with_report(record, lines[record.user_id])
        if diffs:
            _dispute(record, official, 'Match report differs: ' + '; '.join(diffs), auto=True)
            disputed += 1
        else:
            _countersign(record, official, 'official', 'Matches the match report filed by a match official')
            confirmed += 1
    return confirmed, disputed
