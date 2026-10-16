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

BODIES = {
    'MYS': {'name': 'Ministry of Youth & Sports', 'sports': ('Football', 'Basketball', 'Kickball'),
            'competitions': ('National County Sports Meet', 'MYS Schools Sports', 'MYS Community Games')},
    'LFA': {'name': 'Liberia Football Association', 'sports': ('Football',),
            'competitions': ('LFA First Division', 'LFA Second Division', 'LFA Third Division', "LFA Women's League", 'LFA Cup')},
    'LBA': {'name': 'Liberia Basketball Federation', 'sports': ('Basketball',),
            'competitions': ('LBA League', 'LBA Cup', "LBA Women's League")},
    'LKF': {'name': 'Liberia Kickball Federation', 'sports': ('Kickball',),
            'competitions': ('LKF National League', 'LKF Cup')},
    'ISSA': {'name': 'Inter-High School Association', 'sports': ('Football', 'Basketball', 'Kickball'),
             'competitions': ('ISSA Inter-High School League', 'ISSA Championship')},
}
BODY_CODES = tuple(BODIES)
SPORTS = ('Football', 'Basketball', 'Kickball')
FRIENDLY = 'Friendly'
PARTY_ROLES = {'home_coach': 'Home coach', 'away_coach': 'Away coach', 'referee': 'Referee', 'linesman': 'Linesman'}
OFFICIAL_ROLE_TYPES = {'referee': 'Referee / match official', 'linesman': 'Linesman / assistant referee'}
MAX_LINESMEN = 2
_SEASON_RE = re.compile(r'^(\d{4})(?:/(\d{2}))?$')
MIN_LEAD_MINUTES = 30        # a fixture is published at least 30 minutes before kick-off


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
