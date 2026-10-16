"""
Records that come from fixtures (the only way to add a new record).

1. A federation (or the D.A.R.T Admin) publishes a fixture; each team's
   coaches confirm it. Then the athletes registered with those teams are
   notified.
2. After the game the athlete taps "I played in this game" and gives only:
   sport + opponent (from the fixture), Man / Queen of the Match (Y/N),
   shirt number, trophy won and their own numbers (ATHLETE_STATS).
3. Each number is cross-signed (SIGNERS): the team's coach, the opponent's
   coach and, for goals / points / dunks, the main referee.
4. The team's coach sends the team sheet (COACH_FIELDS for every player);
   the main referee sends the discipline report (DISCIPLINE_FIELDS).
5. Everything is combined into the record, which the D.A.R.T Admin publishes.
"""

ATHLETE_STATS = {
    'Football': ('goals', 'assists'),
    'Basketball': ('points', 'assists', 'dunks', 'offensive_rebounds', 'defensive_rebounds', 'blocks'),
    'Kickball': ('home_runs',),
}
STAT_LABELS = {
    'goals': 'Goals', 'assists': 'Assists', 'points': 'Points', 'dunks': 'Dunks', 'offensive_rebounds': 'Offensive rebounds',
    'defensive_rebounds': 'Defensive rebounds', 'blocks': 'Blocks', 'home_runs': 'Home runs',
}
STAT_MAX = {'goals': 20, 'assists': 30, 'points': 120, 'dunks': 40, 'offensive_rebounds': 40, 'defensive_rebounds': 40, 'blocks': 30,
            'home_runs': 20}

# Who must cross-sign each number.
TEAM_COACH, OPPONENT_COACH, REFEREE, REP = 'team_coach', 'opponent_coach', 'referee', 'rep'
SIGNERS = {
    'goals': (TEAM_COACH, REFEREE, OPPONENT_COACH),
    'assists': (TEAM_COACH, OPPONENT_COACH),
    'points': (TEAM_COACH, REFEREE, OPPONENT_COACH),
    'dunks': (TEAM_COACH, REFEREE, OPPONENT_COACH),
    'offensive_rebounds': (TEAM_COACH, OPPONENT_COACH),
    'defensive_rebounds': (TEAM_COACH, OPPONENT_COACH),
    'home_runs': (TEAM_COACH, OPPONENT_COACH),
    'blocks': (TEAM_COACH, OPPONENT_COACH),
}
CHECK_ROLES = {TEAM_COACH: "Team's coach", OPPONENT_COACH: "Opponent's coach", REFEREE: 'Main referee', REP: 'D.A.R.T Rep'}

# Sent in by the main referee only (per player).
DISCIPLINE_FIELDS = {
    'Football': (('yellow_cards', 'Yellow cards', 2), ('red_cards', 'Red card', 1), ('foul_played', 'Fouls', 30)),
    'Basketball': (('foul_played', 'Fouls', 6), ('sent_off', 'Sent off court', 1)),
    'Kickball': (('kickball_yellow_cards', 'Yellow cards', 2), ('kickball_red_cards', 'Red card', 1), ('sent_off', 'Sent off', 1)),
}

# Sent in by each team's coach for every player of the team.
COACH_LINE_FIELDS = {
    'Football': ('played', 'mvp', 'minutes', 'position', 'passes'),
    'Basketball': ('played', 'mvp', 'minutes', 'position'),
    'Kickball': ('played', 'mvp', 'minutes', 'position', 'cut_base'),
}
MAX_MINUTES = {'Football': 130, 'Basketball': 60, 'Kickball': 120}
RECORD_WINDOW_DAYS = 14          # after kick-off, athletes have 2 weeks to submit their record


def required_roles(sport):
    roles = set()
    for stat in ATHLETE_STATS.get(sport, ()):
        roles.update(SIGNERS[stat])
    return roles


def as_int(raw, low=0, high=999):
    raw = str(raw if raw is not None else '').strip()
    if raw == '':
        return 0
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if low <= value <= high else None


def yes_no(raw):
    return 1 if str(raw or '').lower() in ('yes', 'y', '1', 'true', 'on') else 0


def parse_athlete_stats(form, sport):
    """({stat: value}, error)."""
    stats = {}
    for stat in ATHLETE_STATS.get(sport, ()):
        value = as_int(form.get(stat), 0, STAT_MAX[stat])
        if value is None:
            return None, f'{STAT_LABELS[stat]} must be a whole number from 0 to {STAT_MAX[stat]}.'
        stats[stat] = value
    if sport == 'Basketball' and stats.get('dunks', 0) * 2 > stats.get('points', 0):
        return None, 'Dunks are counted in your points: each dunk is at least 2 points.'
    return stats, None


def trophy_choices(sport, trophies_tree):
    """Flat, sorted list of every trophy the sport offers (from competitions.TROPHIES)."""
    out = set()

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            out.add(node)
    walk(trophies_tree.get(sport, {}))
    return sorted(out)
