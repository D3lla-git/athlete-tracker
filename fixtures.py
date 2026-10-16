"""
Federations and match fixtures.

Five federation accounts (role 'Federation') register the teams of each
season and publish fixtures from their dashboards:
  MYS  - Ministry of Youth & Sports (all sports, national meets)
  LFA  - Liberia Football Association
  LBA  - Liberia Basketball Federation
  LKF  - Liberia Kickball Federation
  ISSA - Inter-High School Association (school football, basketball, kickball)
The D.A.R.T Admin can publish for any body. Coaches can only request a
fixture (e.g. a friendly), which the D.A.R.T Admin approves. When a fixture
is published, each team's coaches and the match officials must confirm it.
"""
import re
from datetime import datetime, timedelta

# Every competition each body runs, by sport and type (aligned with competitions.py: leagues, cups,
# grassroots, national teams). Older names stay valid so saved fixtures and teams keep working.
CATALOGUE = {
    'MYS': [
        ('Football', 'National meets', ('National County Sports Meet', 'County Meet', 'National High School Championship', 'MYS Schools Sports')),
        ('Basketball', 'National meets', ('National County Sports Meet', 'County Meet', 'MYS Schools Sports')),
        ('Kickball', 'National meets', ('National County Sports Meet', 'County Meet', 'National High School Championship', 'MYS Schools Sports')),
        ('Football', 'Community & university', ('MYS Community Games', 'Community Trophy', 'University Championship')),
        ('Basketball', 'Community & university', ('MYS Community Games', 'Community Trophy', 'University Championship')),
        ('Kickball', 'Community & university', ('MYS Community Games', 'Community Trophy', 'University Championship')),
    ],
    'LFA': [
        ('Football', 'Leagues', ('LFA First Division', 'LFA Second Division', 'LFA Third Division', "LFA Women's First Division",
                                 "LFA Women's League")),
        ('Football', 'Cups', ('LFA Cup', "LFA Women's Cup", 'LFA Super Cup')),
        ('Football', 'Grassroots', ('LFA U-10 Grassroots Programme', 'LFA U-12 Grassroots League', 'LFA U-15 Grassroots League',
                                    'LFA U-17 Grassroots League', 'LFA Grassroots School League')),
        ('Football', 'National teams (Lone Star)', ('AFCON Finals', 'AFCON Qualifiers', 'WAFU Zone A Tournament', 'FIFA World Cup',
                                                    'FIFA World Cup Qualifiers')),
    ],
    'LBA': [
        ('Basketball', 'Leagues', ('LBA First Division', 'LBA Second Division', 'LBA Third Division', "LBA Women's Division",
                                   'LBA League', "LBA Women's League")),
        ('Basketball', 'Cups & Africa', ('LBA Cup', 'BAL Qualifiers')),
        ('Basketball', 'National teams', ('FIBA Basketball World Cup Qualifiers', 'FIBA AfroBasket', 'FIBA AfroBasket Qualifiers')),
    ],
    'LKF': [
        ('Kickball', 'Leagues', ('LKF First Division', 'LKF Second Division', 'LKF National League')),
        ('Kickball', 'Cups', ('D. Zeogar Wilson Cup', 'LKF Cup')),
    ],
    'ISSA': [
        ('Football', 'Schools', ('ISSA Inter-High School League', 'ISSA Championship', 'ISSA National Championship', 'Classes League')),
        ('Basketball', 'Schools', ('ISSA Inter-High School League', 'ISSA Championship', 'Classes League')),
        ('Kickball', 'Schools', ('ISSA Inter-High School League', 'ISSA Championship', 'Classes League')),
    ],
}
_NAMES = {
    'MYS': 'Ministry of Youth & Sports', 'LFA': 'Liberia Football Association', 'LBA': 'Liberia Basketball Federation',
    'LKF': 'Liberia Kickball Federation', 'ISSA': 'Inter-High School Association',
}


def _body(code):
    groups = CATALOGUE[code]
    sports = tuple(dict.fromkeys(sport for sport, _g, _n in groups))
    flat = tuple(dict.fromkeys(name for _s, _g, names in groups for name in names))
    return {'name': _NAMES[code], 'sports': sports, 'competitions': flat,
            'groups': [(f'{sport} · {group}' if len(sports) > 1 else group, sport, names) for sport, group, names in groups],
            'by_sport': {s: tuple(dict.fromkeys(n for sp, _g, names in groups if sp == s for n in names)) for s in sports}}


BODIES = {code: _body(code) for code in CATALOGUE}
BODY_CODES = tuple(BODIES)
SPORTS = ('Football', 'Basketball', 'Kickball')
FRIENDLY = 'Friendly'
PARTY_ROLES = {'home_coach': 'Home coach', 'away_coach': 'Away coach', 'referee': 'Referee', 'linesman': 'Linesman'}
OFFICIAL_ROLE_TYPES = {'referee': 'Referee / match official', 'linesman': 'Linesman / assistant referee'}
MAX_LINESMEN = 2
_SEASON_RE = re.compile(r'^(\d{4})(?:/(\d{2}))?$')
MIN_LEAD_MINUTES = 30        # a fixture is published at least 30 minutes before kick-off


def competition_ok(code, sport, name):
    """Is this a competition the body runs for this sport (or a friendly)?"""
    if name == FRIENDLY:
        return True
    body = BODIES.get(code or '')
    return bool(body) and name in body['by_sport'].get(sport, ())


def all_competitions():
    """Every federation competition (any body / sport), A-Z, for the D.A.R.T Admin's lists."""
    return sorted({name for body in BODIES.values() for name in body['competitions']})


def body_label(code):
    body = BODIES.get(code or '')
    return f"{body['name']} ({code})" if body else 'D.A.R.T. (friendly)'


def clean(text, limit):
    return ' '.join(str(text or '').split())[:limit]


def current_season(now=None):
    return str((now or datetime.utcnow()).year)


def valid_season(value):
    """'2026' or '2025/26' (consecutive years)."""
    m = _SEASON_RE.match(str(value or '').strip())
    if not m:
        return None
    year = int(m.group(1))
    if not 2000 <= year <= 2100:
        return None
    if m.group(2) and int(m.group(2)) != (year + 1) % 100:
        return None
    return m.group(0)


def season_choices(now=None):
    year = (now or datetime.utcnow()).year
    return [str(year + 1), f'{year}/{(year + 1) % 100:02d}', str(year), f'{year - 1}/{year % 100:02d}', str(year - 1)]


def parse_kickoff(raw):
    try:
        return datetime.strptime(str(raw or ''), '%Y-%m-%dT%H:%M')
    except ValueError:
        return None


def kickoff_error(kickoff, now=None):
    if not kickoff:
        return 'Enter the kick-off date and time (Liberia time).'
    if kickoff <= (now or datetime.utcnow()) + timedelta(minutes=MIN_LEAD_MINUTES):
        return f'Kick-off must be more than {MIN_LEAD_MINUTES} minutes from now.'
    if kickoff > (now or datetime.utcnow()) + timedelta(days=400):
        return 'Kick-off must be within the next 13 months.'
    return None


def parse_team_lines(text, limit=300):
    """Team names from a pasted list (one per line, or comma-separated CSV with the name first)."""
    names, seen = [], set()
    for line in str(text or '').splitlines()[:limit]:
        name = clean(line.split(',')[0], 150)
        if len(name) < 2 or name.lower() in ('team', 'team name', 'name'):
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def party_status(party):
    if party.confirmed_at:
        return 'confirmed'
    if party.declined_at:
        return 'declined'
    return 'waiting'
