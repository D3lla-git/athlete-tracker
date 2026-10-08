"""
Coach rules shared by the app and plans.py:

  * Which teams a coach manages (their registered team + teams the Super
    Admin approved, minus teams they left). A coach may only verify
    athletes and approve / reject records of those teams, for the
    competitions their category allows on that team.
  * Coach Pro career records: games (result, score, formation, subs,
    cards) and trophies / awards, checked here before they are saved.
  * Career totals for profiles and the coach search.
"""
from datetime import date

from flask import g, has_request_context

from models import db, CoachTeam, CoachGameRecord, CoachAchievement

COACH_SPORTS = ('Football', 'Basketball', 'Kickball')
RESULTS = {'win': 'Win', 'draw': 'Draw', 'loss': 'Loss'}
FORMATIONS = (
    '4-4-2', '4-3-3', '4-2-3-1', '4-1-4-1', '4-5-1', '4-4-1-1', '4-3-2-1', '4-1-2-1-2',
    '3-5-2', '3-4-3', '3-4-1-2', '5-3-2', '5-4-1', '4-2-4',
)
TEAM_ROLES = ('Head Coach', 'Assistant Coach', 'Goalkeeper Coach', 'Fitness Coach', 'Technical Director')
ACHIEVEMENT_KINDS = {'trophy': 'Trophy / title', 'award': 'Personal award'}
MAX_SUBSTITUTIONS = 15
MAX_CARDS = 5
MAX_SCORE = 300           # basketball scores can be high
MAX_ACTIVE_TEAMS = 4


def _key(name):
    return (name or '').strip().casefold()


def clean_text(value, limit):
    return ' '.join((value or '').split())[:limit]


# ---------- Teams ----------
def ensure_primary_team(coach):
    """Create the coach's primary team row from their registration (once). Caller commits."""
    if coach.role != 'Coach' or not coach.school:
        return None
    existing = CoachTeam.query.filter_by(coach_id=coach.id, is_primary=True).first()
    if existing:
        return existing
    row = CoachTeam(coach_id=coach.id, team_name=coach.school.strip(), coach_category=coach.coach_category or 'All Coach',
                    status='approved', is_primary=True, start_date=None)
    db.session.add(row)
    return row


def active_teams(coach):
    """
    [(team key, team name, coach category)] the coach manages now. Coaches
    without team rows (never opened their career page) use their
    registration.
    """
    if not coach or getattr(coach, 'role', None) != 'Coach':
        return []
    cache = None
    if has_request_context():
        cache = g.setdefault('_dart_coach_teams', {})
        if coach.id in cache:
            return cache[coach.id]
    teams = _load_active_teams(coach)
    if cache is not None:
        cache[coach.id] = teams
    return teams


def forget_teams():
    """Call after a coach's teams change within a request."""
    if has_request_context():
        g.pop('_dart_coach_teams', None)


def _load_active_teams(coach):
    rows = CoachTeam.query.filter(
        CoachTeam.coach_id == coach.id,
        CoachTeam.status == 'approved',
        CoachTeam.end_date.is_(None),
    ).all()
    if not rows:
        return [(_key(coach.school), (coach.school or '').strip(), coach.coach_category)] if coach.school else []
    teams = [(_key(r.team_name), r.team_name, r.coach_category) for r in rows]
    # The registered team follows the profile (category changes, etc.).
    for i, (key, name, category) in enumerate(teams):
        if key == _key(coach.school):
            teams[i] = (key, name, coach.coach_category or category)
    return teams


def team_keys(coach):
    return {key for key, _, _ in active_teams(coach)}


def manages_record(coach, record, can_manage_competition):
    """Coach may approve / reject this record (team + category rules)."""
    record_key = _key(record.team)
    return any(
        key == record_key and can_manage_competition(category, record.competition_category)
        for key, _, category in active_teams(coach)
    )


def manages_athlete(coach, athlete):
    """Coach may verify / view the ID document of this athlete (same team)."""
    return _key(getattr(athlete, 'school', '')) in team_keys(coach)


# ---------- Career records ----------
def validate_game(form, allowed_competitions, today):
    """
    (values dict, None) or (None, error message) for a coach game record.
    """
    sport = form.get('sport', '')
    if sport not in COACH_SPORTS:
        return None, 'Choose the sport.'
    team_name = clean_text(form.get('team_name'), 150)
    if not team_name:
        return None, 'Choose the team you coached in this game.'
    opponent = clean_text(form.get('opponent'), 150)
    if not opponent:
        return None, 'Enter the opponent.'
    try:
        game_date = date.fromisoformat(form.get('game_date', ''))
    except ValueError:
        return None, 'Enter the game date.'
    if game_date > today:
        return None, "The game date can't be in the future."
    if game_date.year < 1990:
        return None, 'Enter a real game date.'
    result = form.get('result', '')
    if result not in RESULTS:
        return None, 'Choose the result: win, draw or loss.'
    if sport == 'Basketball' and result == 'draw':
        return None, "Basketball games can't end in a draw."

    def number(name, low, high, required=False):
        raw = (form.get(name) or '').strip()
        if raw == '':
            return (None, None) if not required else (None, f'Enter {name.replace("_", " ")}.')
        try:
            value = int(raw)
        except ValueError:
            return None, f'{name.replace("_", " ").capitalize()} must be a whole number.'
        if not low <= value <= high:
            return None, f'{name.replace("_", " ").capitalize()} must be between {low} and {high}.'
        return value, None

    score_for, err = number('score_for', 0, MAX_SCORE)
    if err:
        return None, err
    score_against, err = number('score_against', 0, MAX_SCORE)
    if err:
        return None, err
    if (score_for is None) != (score_against is None):
        return None, 'Enter both scores, or leave both empty.'
    if score_for is not None:
        expected = 'win' if score_for > score_against else ('loss' if score_for < score_against else 'draw')
        if expected != result:
            return None, f'The score {score_for}-{score_against} is a {RESULTS[expected].lower()}, not a {RESULTS[result].lower()}.'
    subs, err = number('substitutions', 0, MAX_SUBSTITUTIONS)
    if err:
        return None, err
    yellow, err = number('yellow_cards', 0, MAX_CARDS)
    if err:
        return None, err
    red, err = number('red_cards', 0, 1)
    if err:
        return None, err

    formation = form.get('formation', '') or None
    if sport != 'Football':
        formation = None
    elif formation and formation not in FORMATIONS:
        return None, 'Choose a formation from the list.'

    competition = form.get('competition', '') or None
    if competition and competition not in allowed_competitions:
        return None, 'Choose a competition from the list.'

    return {
        'sport': sport, 'team_name': team_name, 'opponent': opponent, 'game_date': game_date,
        'season': game_date.year, 'result': result, 'score_for': score_for, 'score_against': score_against,
        'formation': formation, 'substitutions': subs or 0, 'yellow_cards': yellow or 0, 'red_cards': red or 0,
        'competition': competition, 'notes': clean_text(form.get('notes'), 200) or None,
    }, None


def validate_achievement(form, today):
    kind = form.get('kind', '')
    if kind not in ACHIEVEMENT_KINDS:
        return None, 'Choose trophy or award.'
    title = clean_text(form.get('title'), 150)
    if len(title) < 3:
        return None, 'Enter what you won (e.g. "County Meet Champions" or "Best Coach of the Season").'
    try:
        season = int(form.get('season', ''))
    except ValueError:
        return None, 'Enter the season (year).'
    if not 1990 <= season <= today.year:
        return None, f'The season must be between 1990 and {today.year}.'
    return {
        'kind': kind, 'title': title, 'season': season,
        'competition': clean_text(form.get('competition'), 80) or None,
        'team_name': clean_text(form.get('team_name'), 150) or None,
    }, None


# ---------- Career totals ----------
def career_summary(games, achievements):
    """Totals over approved games, per sport and overall."""
    total = {'games': 0, 'win': 0, 'draw': 0, 'loss': 0, 'yellow': 0, 'red': 0, 'subs': 0}
    per_sport = {}
    formations = {}
    teams = []
    for g in games:
        for bucket in (total, per_sport.setdefault(g.sport, {'games': 0, 'win': 0, 'draw': 0, 'loss': 0})):
            bucket['games'] += 1
            bucket[g.result] += 1
        total['yellow'] += g.yellow_cards or 0
        total['red'] += g.red_cards or 0
        total['subs'] += g.substitutions or 0
        if g.formation:
            formations[g.formation] = formations.get(g.formation, 0) + 1
        if g.team_name not in teams:
            teams.append(g.team_name)
    total['win_pct'] = round(100 * total['win'] / total['games']) if total['games'] else 0
    for s in per_sport.values():
        s['win_pct'] = round(100 * s['win'] / s['games']) if s['games'] else 0
    return {
        'total': total,
        'sports': per_sport,
        'top_formation': max(formations, key=formations.get) if formations else None,
        'teams': teams,
        'trophies': [a for a in achievements if a.kind == 'trophy'],
        'awards': [a for a in achievements if a.kind == 'award'],
    }


def public_career(coach_ids):
    """{coach id: (approved games newest first, approved achievements)} in two queries."""
    if not coach_ids:
        return {}
    out = {cid: ([], []) for cid in coach_ids}
    for g in (CoachGameRecord.query.filter(CoachGameRecord.coach_id.in_(coach_ids), CoachGameRecord.status == 'approved')
              .order_by(CoachGameRecord.game_date.desc(), CoachGameRecord.id.desc()).all()):
        out[g.coach_id][0].append(g)
    for a in (CoachAchievement.query.filter(CoachAchievement.coach_id.in_(coach_ids), CoachAchievement.status == 'approved')
              .order_by(CoachAchievement.season.desc(), CoachAchievement.id.desc()).all()):
        out[a.coach_id][1].append(a)
    return out


def coaches_with_public_career():
    """Ids of coaches that have at least one approved game or achievement."""
    ids = {r[0] for r in db.session.query(CoachGameRecord.coach_id).filter(CoachGameRecord.status == 'approved').distinct()}
    ids |= {r[0] for r in db.session.query(CoachAchievement.coach_id).filter(CoachAchievement.status == 'approved').distinct()}
    return ids


def pending_counts():
    return {
        'teams': CoachTeam.query.filter_by(status='pending').count(),
        'games': CoachGameRecord.query.filter_by(status='pending').count(),
        'achievements': CoachAchievement.query.filter_by(status='pending').count(),
    }
