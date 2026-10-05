"""
Competition → trophy rules for D.A.R.T. sports records.

The single source of truth for which trophy (the competition actually won)
an athlete may record. Used by the record routes in app.py and, as JSON, by
the record forms in student_dashboard.html and edit_record.html.

Football follows the Liberia Football Association (LFA) and Liberia's
national competitions:
  - LFA leagues: First, Second and Third Division (the Third Division is run
    through the county Sub-Associations / Montserrado Sub-Committees) and the
    LFA Women's First Division.
  - LFA cups: LFA Cup and LFA Women's Cup (sponsored as the "Orange Cup")
    and the LFA Super Cup. Sponsor-free names are stored so records stay
    correct when sponsors change.
  - National County Sports Meet (Ministry of Youth & Sports).
  - National High School Football Championship (National High School Sports)
    and the ISSA National Championship.
  - Lone Star national teams: AFCON, WAFU Zone A and FIFA World Cup.

Basketball and Kickball keep their original one-trophy-per-competition rules
(plus three international trophies for Basketball).
"""

# ==========================================================
# FOOTBALL (LFA + national competitions)
# ==========================================================

# Club League trophies depend on the club's division.
CLUB_LEAGUE_DIVISION_TROPHIES = {
    '1st Division': [
        'LFA First Division',
        "LFA Women's First Division",
    ],
    '2nd Division': [
        'LFA Second Division',
    ],
    '3rd Division': [
        'LFA Third Division',
    ],
}

# Cups any club can win, whatever its division.
CLUB_LEAGUE_CUP_TROPHIES = [
    'LFA Cup',
    "LFA Women's Cup",
    'LFA Super Cup',
]

FOOTBALL_TROPHIES = {
    'High School': [
        'Classes League',
        'National High School Championship',
        'ISSA National Championship',
    ],
    'County Meet': [
        'County Meet',
        'National County Sports Meet',
    ],
    # No national university or community football competition was found,
    # so these keep their original trophy.
    'University League': [
        'University Championship',
    ],
    'Community/Area League': [
        'Community Trophy',
    ],
    'AFCON': [
        'AFCON Finals',
        'AFCON Qualifiers',
    ],
    'WAFU': [
        'WAFU Zone A Tournament',
    ],
    'World Cup': [
        'FIFA World Cup',
        'FIFA World Cup Qualifiers',
    ],
}

# Original generic Football trophy names that now have real replacements.
# They stay valid only on existing records (an athlete editing an old record
# can keep it); new and offline-synced records must use a real competition.
LEGACY_FOOTBALL_TROPHIES = {
    'Club League': ['Club Trophy'],
    'AFCON': ['AFCON'],
    'WAFU': ['WAFU'],
    'World Cup': ['World Cup'],
}

# ==========================================================
# BASKETBALL AND KICKBALL (unchanged rules)
# ==========================================================

CATEGORY_TROPHIES = {
    'High School': 'Classes League',
    'County Meet': 'County Meet',
    'Club League': 'Club Trophy',
    'University League': 'University Championship',
    'Community/Area League': 'Community Trophy',
    'AFCON': 'AFCON',
    'WAFU': 'WAFU',
    'World Cup': 'World Cup',
}

BASKETBALL_TROPHIES = [
    'Basketball Africa League (BAL)',
    'FIBA Africa Zone',
    'FIBA AfroBasket Championships',
]

SPORTS = ('Football', 'Basketball', 'Kickball')
CLUB_DIVISIONS = tuple(CLUB_LEAGUE_DIVISION_TROPHIES)


def trophy_options(sport, competition_category, club_division=None):
    """Trophies an athlete can choose, in the order the form lists them."""
    if sport == 'Football':
        if competition_category == 'Club League':
            return (
                CLUB_LEAGUE_DIVISION_TROPHIES.get(club_division, [])
                + CLUB_LEAGUE_CUP_TROPHIES
            )

        return list(FOOTBALL_TROPHIES.get(competition_category, []))

    category_trophy = CATEGORY_TROPHIES.get(competition_category)

    if not category_trophy:
        return []

    if sport == 'Basketball':
        return [category_trophy] + BASKETBALL_TROPHIES

    return [category_trophy]


def legacy_trophy_options(sport, competition_category):
    """Retired names an existing record may keep (Football only)."""
    if sport == 'Football':
        return LEGACY_FOOTBALL_TROPHIES.get(competition_category, [])

    return []


def validate_trophy(trophies, sport, competition_category, club_division=None,
                    current_trophy=None):
    """
    Check the trophy submitted for a record.

    trophies:       values of the form's 'trophy' field (a list).
    current_trophy: the record's saved trophy when editing, so an old record
                    can keep a retired name; None for new records.

    Returns (trophy, None) when valid, or (None, error_message).
    """
    trophies = [t.strip() for t in trophies if t and t.strip()]

    if sport != 'Basketball' and any(t in BASKETBALL_TROPHIES for t in trophies):
        return None, (
            'BAL, FIBA Africa Zone, and FIBA AfroBasket Championships '
            'are only available for Basketball records.'
        )

    if not trophies:
        return None, (
            f'Please select the trophy won for the '
            f'{competition_category} competition.'
        )

    if len(trophies) > 1:
        return None, 'Please select only one trophy for a game record.'

    trophy = trophies[0]
    options = trophy_options(sport, competition_category, club_division)

    if trophy in options:
        return trophy, None

    if (
        current_trophy
        and trophy == current_trophy.strip()
        and trophy in legacy_trophy_options(sport, competition_category)
    ):
        return trophy, None

    if not options:
        if competition_category == 'Club League':
            return None, 'Please select a valid Club League Division.'

        return None, f'Invalid competition category: {competition_category}.'

    return None, (
        f'Invalid trophy for {competition_category}. '
        f'Choose one of: {", ".join(options)}.'
    )


def trophy_options_table():
    """
    Every trophy list, for the record forms' JavaScript.

    Keys are "sport|competition|division"; the division part is only set
    for Club League (empty otherwise), e.g. "Football|Club League|1st Division".
    """
    table = {}
    categories = set(FOOTBALL_TROPHIES) | set(CATEGORY_TROPHIES) | {'Club League'}

    for sport in SPORTS:
        for category in categories:
            divisions = ('',) + CLUB_DIVISIONS if category == 'Club League' else ('',)

            for division in divisions:
                options = trophy_options(sport, category, division or None)

                if options:
                    table[f'{sport}|{category}|{division}'] = options

    return table
