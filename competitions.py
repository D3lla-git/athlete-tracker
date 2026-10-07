"""
Competition and trophy rules for D.A.R.T. sports records.

The single source of truth for which competitions each sport offers and
which trophy (the competition actually won) an athlete may record. Used by
the record routes in app.py and, as JSON, by the record forms in
student_dashboard.html and edit_record.html.

Each sport follows its Liberian governing body:

Football: Liberia Football Association (LFA) and national competitions
  - LFA leagues: First, Second and Third Division (the Third Division is run
    through the county Sub-Associations / Montserrado Sub-Committees) and the
    LFA Women's First Division.
  - LFA cups: LFA Cup and LFA Women's Cup (sponsored as the "Orange Cup")
    and the LFA Super Cup. Sponsor-free names are stored so records stay
    correct when sponsors change.
  - LFA grassroots: U-10 programme, U-12 / U-15 / U-17 grassroots leagues and
    the Grassroots Football School League (FIFA Forward supported).
  - National County Sports Meet (Ministry of Youth & Sports), National High
    School Football Championship and the ISSA National Championship.
  - Lone Star national teams: AFCON, WAFU Zone A and FIFA World Cup.

Basketball: Liberia Basketball Association (LBA)
  - National Basketball League: First, Second and Third Division and the
    women's division. The First Division champion enters BAL qualifying.
  - National teams: FIBA Basketball World Cup Qualifiers and FIBA AfroBasket.

Kickball: Liberia Kickball Federation (LKF)
  - LKF National League: First and Second Division, and the
    D. Zeogar Wilson Cup.
  - National High School Kickball Championship.
"""

# ==========================================================
# COMPETITIONS EACH SPORT OFFERS
# ==========================================================

SPORT_COMPETITIONS = {
    'Football': [
        'High School',
        'County Meet',
        'Club League',
        'University League',
        'Community/Area League',
        'Grassroots League',
        'AFCON',
        'WAFU',
        'World Cup',
    ],
    # AFCON and WAFU are football competitions.
    'Basketball': [
        'High School',
        'County Meet',
        'Club League',
        'University League',
        'Community/Area League',
        'World Cup',
    ],
    # There is no international kickball competition.
    'Kickball': [
        'High School',
        'County Meet',
        'Club League',
        'University League',
        'Community/Area League',
    ],
}

SPORTS = tuple(SPORT_COMPETITIONS)

ALL_COMPETITIONS = [
    competition
    for competition in SPORT_COMPETITIONS['Football']
]

CLUB_DIVISIONS = ('1st Division', '2nd Division', '3rd Division')

# LFA grassroots age groups (no U-9 competition exists).
AGE_GROUPS = ('U-10', 'U-12', 'U-15', 'U-17')

# Competitions whose trophies depend on a second choice, and the form
# field that carries it.
DETAIL_FIELDS = {
    'Club League': 'club_division',
    'Grassroots League': 'age_group',
}

# ==========================================================
# TROPHIES
# ==========================================================
# sport -> competition -> list of trophies, or for competitions in
# DETAIL_FIELDS: {division/age group: trophies, '*': trophies for any}.

TROPHIES = {
    'Football': {
        'High School': [
            'Classes League',
            'National High School Championship',
            'ISSA National Championship',
        ],
        'County Meet': [
            'County Meet',
            'National County Sports Meet',
        ],
        'Club League': {
            '1st Division': ['LFA First Division', "LFA Women's First Division"],
            '2nd Division': ['LFA Second Division'],
            '3rd Division': ['LFA Third Division'],
            '*': ['LFA Cup', "LFA Women's Cup", 'LFA Super Cup'],
        },
        # No national university or community football competition exists,
        # so these keep their original trophy.
        'University League': ['University Championship'],
        'Community/Area League': ['Community Trophy'],
        'Grassroots League': {
            'U-10': [],
            'U-12': ['LFA U-12 Grassroots League'],
            'U-15': ['LFA U-15 Grassroots League'],
            'U-17': ['LFA U-17 Grassroots League'],
            '*': ['LFA Grassroots School League'],
        },
        'AFCON': ['AFCON Finals', 'AFCON Qualifiers'],
        'WAFU': ['WAFU Zone A Tournament'],
        'World Cup': ['FIFA World Cup', 'FIFA World Cup Qualifiers'],
    },
    'Basketball': {
        'High School': ['Classes League'],
        'County Meet': ['County Meet', 'National County Sports Meet'],
        'Club League': {
            '1st Division': ['LBA First Division', "LBA Women's Division", 'BAL Qualifiers'],
            '2nd Division': ['LBA Second Division'],
            '3rd Division': ['LBA Third Division'],
            '*': [],
        },
        'University League': ['University Championship'],
        'Community/Area League': ['Community Trophy'],
        'World Cup': [
            'FIBA Basketball World Cup Qualifiers',
            'FIBA AfroBasket',
            'FIBA AfroBasket Qualifiers',
        ],
    },
    'Kickball': {
        'High School': ['Classes League', 'National High School Championship'],
        'County Meet': ['County Meet', 'National County Sports Meet'],
        'Club League': {
            '1st Division': ['LKF First Division'],
            '2nd Division': ['LKF Second Division'],
            '3rd Division': [],
            '*': ['D. Zeogar Wilson Cup'],
        },
        'University League': ['University Championship'],
        'Community/Area League': ['Community Trophy'],
    },
}

# ==========================================================
# ORIGINAL RULES (before LFA / LBA / LKF alignment)
# ==========================================================
# Every sport had one generic trophy per competition, and any Basketball
# record could also use three international trophies. Names that are no
# longer offered stay valid only on existing records, so an athlete editing
# an old record can keep them; new and offline-synced records must use a
# real competition.

ORIGINAL_CATEGORY_TROPHIES = {
    'High School': 'Classes League',
    'County Meet': 'County Meet',
    'Club League': 'Club Trophy',
    'University League': 'University Championship',
    'Community/Area League': 'Community Trophy',
    'AFCON': 'AFCON',
    'WAFU': 'WAFU',
    'World Cup': 'World Cup',
}

ORIGINAL_BASKETBALL_TROPHIES = [
    'Basketball Africa League (BAL)',
    'FIBA Africa Zone',
    'FIBA AfroBasket Championships',
]

# ==========================================================
# AFCON / WAFU / WORLD CUP TEAM (SportRecord.competition_team)
# ==========================================================

COMPETITION_TEAM_OPTIONS = {
    'Football': {
        'AFCON': ["Lonestar Men's Team"],
        'WAFU': [
            'Male-U15', 'Female-U15',
            'Male-U17', 'Female-U17',
            'Male-U20', 'Female-U20',
            'Male-U23', 'Female-U23',
        ],
        'World Cup': ["Lonestar Men's Team"],
    },
    'Basketball': {
        'World Cup': ["Liberia Men's National Team", "Liberia Women's National Team"],
    },
}

# Field that carries the choice on the new-record form, which has one
# dropdown per competition. The edit form sends 'competition_team'.
COMPETITION_TEAM_FORM_FIELDS = {
    'AFCON': 'afcon_team',
    'WAFU': 'wafu_category',
    'World Cup': 'world_cup_team',
}

COMPETITION_TEAM_LABELS = {
    'AFCON': 'AFCON Team',
    'WAFU': 'WAFU Category',
    'World Cup': 'World Cup Team',
}


# ==========================================================
# RULE LOOKUPS
# ==========================================================

def competitions_for(sport):
    """Competitions offered for a sport, in display order."""
    return list(SPORT_COMPETITIONS.get(sport, []))


def competition_allowed(sport, competition_category, current_category=None):
    """
    True if the competition is offered for this sport. When editing, the
    record's existing competition stays allowed even if it is no longer
    offered (e.g. an old Kickball record filed under AFCON).
    """
    if competition_category in SPORT_COMPETITIONS.get(sport, []):
        return True

    return (
        current_category is not None
        and competition_category == current_category
        and competition_category in ORIGINAL_CATEGORY_TROPHIES
    )


def trophy_options(sport, competition_category, detail=None):
    """
    Trophies an athlete can choose, in form order. `detail` is the club
    division (Club League) or age group (Grassroots League).
    """
    rules = TROPHIES.get(sport, {}).get(competition_category)

    if rules is None:
        return []

    if isinstance(rules, dict):
        return list(rules.get(detail, [])) + list(rules.get('*', []))

    return list(rules)


def _all_current_trophies(sport, competition_category):
    rules = TROPHIES.get(sport, {}).get(competition_category)

    if rules is None:
        return set()

    if isinstance(rules, dict):
        return {trophy for trophies in rules.values() for trophy in trophies}

    return set(rules)


def legacy_trophy_options(sport, competition_category):
    """Retired names an existing record may keep."""
    original = []

    if competition_category in ORIGINAL_CATEGORY_TROPHIES:
        original.append(ORIGINAL_CATEGORY_TROPHIES[competition_category])

        if sport == 'Basketball':
            original.extend(ORIGINAL_BASKETBALL_TROPHIES)

    current = _all_current_trophies(sport, competition_category)

    return [trophy for trophy in original if trophy not in current]


NO_TROPHY_VALUES = ('none', '-')


def validate_trophy(trophies, sport, competition_category, detail=None,
                    current_trophy=None):
    """
    Check the trophy submitted for a record.

    trophies:       values of the form's 'trophy' field (a list).
    detail:         club division or age group (see DETAIL_FIELDS).
    current_trophy: the record's saved trophy when editing, so an old record
                    can keep a retired name; None for new records.

    Returns (trophy, None) when valid, (None, None) when no trophy was won,
    or (None, error_message).
    """
    # "None" / "-" / nothing selected means no trophy was won in this game.
    trophies = [
        t.strip() for t in trophies
        if t and t.strip() and t.strip().lower() not in NO_TROPHY_VALUES
    ]

    if not trophies:
        return None, None

    if len(trophies) > 1:
        return None, 'Please select only one trophy for a game record.'

    trophy = trophies[0]
    options = trophy_options(sport, competition_category, detail)

    if trophy in options:
        return trophy, None

    if (
        current_trophy
        and trophy == current_trophy.strip()
        and trophy in legacy_trophy_options(sport, competition_category)
    ):
        return trophy, None

    if not options:
        return None, (
            f'No {sport} trophy is available for {competition_category}'
            + (f' ({detail})' if detail else '')
            + '. Please check the competition details.'
        )

    return None, (
        f'Invalid trophy for {sport} {competition_category}. '
        f'Choose one of: {", ".join(options)}.'
    )


def clean_age_group(form, competition_category):
    """Return (age_group, error) — required only for Grassroots League."""
    if competition_category != 'Grassroots League':
        return None, None

    age_group = (form.get('age_group') or '').strip()

    if age_group not in AGE_GROUPS:
        return None, 'Please select a valid Age Group for the Grassroots League.'

    return age_group, None


def competition_team_options(sport, competition_category):
    return list(COMPETITION_TEAM_OPTIONS.get(sport, {}).get(competition_category, []))


def clean_competition_team(form, sport, competition_category, current_team=None):
    """
    Return (competition_team, error) for a submitted record.

    National-team competitions must name a valid team/category for the
    sport; every other competition stores None. When editing, the record's
    current value stays valid.
    """
    options = competition_team_options(sport, competition_category)

    if competition_category not in COMPETITION_TEAM_LABELS:
        return None, None

    value = (
        form.get('competition_team')
        or form.get(COMPETITION_TEAM_FORM_FIELDS[competition_category])
        or ''
    ).strip()

    if value in options or (current_team and value == current_team):
        return value, None

    label = COMPETITION_TEAM_LABELS[competition_category]
    return None, f'Please select a valid {label} for {competition_category}.'


# ==========================================================
# DATA FOR THE RECORD FORMS
# ==========================================================

def form_rules():
    """
    Everything the record forms' JavaScript needs, from the same rules the
    server enforces.

    trophies: "sport|competition|detail" -> trophies (detail is the club
              division or age group, empty for other competitions)
    competitions: sport -> competitions offered
    teams: "sport|competition" -> AFCON / WAFU / World Cup team options
    """
    trophies = {}

    for sport, competitions in TROPHIES.items():
        for competition, rules in competitions.items():
            if isinstance(rules, dict):
                for detail in rules:
                    if detail == '*':
                        continue
                    options = trophy_options(sport, competition, detail)
                    if options:
                        trophies[f'{sport}|{competition}|{detail}'] = options
            else:
                trophies[f'{sport}|{competition}|'] = list(rules)

    teams = {
        f'{sport}|{competition}': list(options)
        for sport, competitions in COMPETITION_TEAM_OPTIONS.items()
        for competition, options in competitions.items()
    }

    return {
        'trophies': trophies,
        'competitions': {sport: competitions_for(sport) for sport in SPORTS},
        'teams': teams,
        'teamLabels': dict(COMPETITION_TEAM_LABELS),
        'detailFields': dict(DETAIL_FIELDS),
        'ageGroups': list(AGE_GROUPS),
        'clubDivisions': list(CLUB_DIVISIONS),
    }


# ==========================================================
# REGISTRATION CATEGORIES (shown when athletes / coaches register)
# ==========================================================
# Each single-competition category matches one competition (see
# athlete_can_submit_competition / coach_can_manage_competition in app.py);
# "All" categories cover every competition.

def _category(slug, name, icon, summary, detail, competitions):
    return {
        'slug': slug,
        'name': name,
        'icon': icon,
        'summary': summary,
        'detail': detail,
        'competitions': list(competitions),
    }


ATHLETE_CATEGORIES = [
    _category(
        'all-athlete', 'All Athlete', 'fa-layer-group',
        'You play in more than one competition.',
        'This category means you can play high school ball and club or community '
        'league at the same time. Records from every competition are stored, '
        'including grassroots and national-team (AFCON, WAFU, World Cup) games.',
        ALL_COMPETITIONS,
    ),
    _category(
        'county-meet', 'County Meet Athlete', 'fa-map-location-dot',
        'You only play for your county.',
        'This category means you are only playing for your county in the County '
        'Meet, therefore only County Meet records will be stored.',
        ['County Meet'],
    ),
    _category(
        'club-league', 'Club League Athlete', 'fa-shield-halved',
        'You only play club league (1st–3rd Division).',
        'This category means you are only playing for a club in the 1st, 2nd or 3rd '
        'Division, therefore only Club League records will be stored.',
        ['Club League'],
    ),
    _category(
        'university', 'University Athlete', 'fa-graduation-cap',
        'You only play university sports.',
        'This category means you are only playing for your university, therefore '
        'only University League records will be stored.',
        ['University League'],
    ),
    _category(
        'community-league', 'Community/Area League Athlete', 'fa-people-group',
        'You only play community or area league.',
        'This category means you are only playing in a community or area league, '
        'therefore only Community/Area League records will be stored.',
        ['Community/Area League'],
    ),
    _category(
        'high-school', 'High School Athlete', 'fa-school',
        'You only play high school ball.',
        'This category means you are only playing high school ball, therefore only '
        'High School records will be stored.',
        ['High School'],
    ),
]

COACH_CATEGORIES = [
    _category(
        'all-coach', 'All Coach', 'fa-layer-group',
        'You coach teams in more than one competition.',
        'This category means you can coach or manage a high school team and a club '
        'or community side at the same time. You review and approve your athletes\' '
        'records from every competition, including grassroots and national-team games.',
        ALL_COMPETITIONS,
    ),
    _category(
        'county-meet', 'County Meet Coach', 'fa-map-location-dot',
        'You only coach a county team.',
        'This category means you only coach a county team in the County Meet, '
        'therefore you review and approve only County Meet records.',
        ['County Meet'],
    ),
    _category(
        'club-league', 'Club League Coach', 'fa-shield-halved',
        'You only coach a club (1st–3rd Division).',
        'This category means you only coach a club in the 1st, 2nd or 3rd Division, '
        'therefore you review and approve only Club League records.',
        ['Club League'],
    ),
    _category(
        'university', 'University Coach', 'fa-graduation-cap',
        'You only coach a university team.',
        'This category means you only coach a university team, therefore you review '
        'and approve only University League records.',
        ['University League'],
    ),
    _category(
        'community-league', 'Community/Area League Coach', 'fa-people-group',
        'You only coach a community or area team.',
        'This category means you only coach in a community or area league, therefore '
        'you review and approve only Community/Area League records.',
        ['Community/Area League'],
    ),
    _category(
        'high-school', 'High School Coach', 'fa-school',
        'You only coach high school ball.',
        'This category means you only coach a high school team, therefore you review '
        'and approve only High School records.',
        ['High School'],
    ),
]
