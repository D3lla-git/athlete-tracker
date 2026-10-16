"""
Organization Pro: games, line-ups and results.

Before a game the organization enters its line-up (starters + substitutes);
it locks LINEUP_LOCK_MINUTES before kick-off and is required before the
result can be posted. After the game: score, a 10-word summary, cards,
fouls, goalkeeper saves and the league standing (LFA / LBA / MYS agenda).
The D.A.R.T Admin compares both teams' line-ups (when the opponent is also
on D.A.R.T.) and confirms them. Each game has a public page with the
head-to-head record and links to the players' match records.
"""
import io
import json
import re

import fixtures as _fx
from datetime import datetime, timedelta

from fpdf import FPDF

from record_pdf import pdf_text, GREEN, GREEN_DARK, GREEN_LIGHT, INK, GREY

GAME_SPORTS = ('Football', 'Basketball', 'Kickball')
# (starters, most substitutes) per sport.
LINEUP_SIZES = {'Football': (11, 12), 'Basketball': (5, 7), 'Kickball': (10, 10)}
LINEUP_LOCK_MINUTES = 5
SUMMARY_MAX_WORDS = 10
# Competitions on the national sporting agenda (standings are entered per competition).
# Organization Pro games: every federation competition (fixtures.CATALOGUE: leagues, cups, grassroots,
# national teams) plus the names used before, which stay valid on saved games.
_LEGACY_COMPETITIONS = ('MYS National County Sports Meet', 'MYS Kickball League')
COMPETITION_GROUPS = [(f"{_fx.BODIES[code]['name']} ({code})", tuple(dict.fromkeys(
    _fx.BODIES[code]['competitions'] + (_LEGACY_COMPETITIONS if code == 'MYS' else ())))) for code in _fx.BODY_CODES]
COMPETITIONS = tuple(dict.fromkeys(name for _label, names in COMPETITION_GROUPS for name in names)) + ('Friendly',)
_NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ' .-]{2,80}$")


def clean(text, limit):
    return ' '.join((text or '').split())[:limit]


def as_int(value, low=0, high=999):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def lineup_deadline(game):
    return game.kickoff_at - timedelta(minutes=LINEUP_LOCK_MINUTES)


def lineup_open(game, now=None):
    return (now or datetime.utcnow()) < lineup_deadline(game)


def started(game, now=None):
    return (now or datetime.utcnow()) >= game.kickoff_at


def lineup_of(game):
    try:
        return json.loads(game.lineup or '[]')
    except ValueError:
        return []


def parse_lineup(form, sport, roster_names):
    """
    (players, error). players = [{'name', 'shirt', 'position', 'role', 'athlete_id'}].
    form fields: name_N, shirt_N, position_N, role_N for N = 0..
    roster_names: {lower name: athlete id} of the organization's athletes (to link them).
    """
    starters_needed, subs_max = LINEUP_SIZES[sport]
    players, seen_names, seen_shirts = [], set(), set()
    for i in range(starters_needed + subs_max):
        name = clean(form.get(f'name_{i}'), 80)
        if not name:
            continue
        if not _NAME_RE.match(name):
            return None, f'Player name "{name}" has characters that are not allowed.'
        if name.lower() in seen_names:
            return None, f'{name} is in the line-up twice.'
        shirt = clean(form.get(f'shirt_{i}'), 2)
        if shirt and (not shirt.isdigit() or int(shirt) > 99):
            return None, f'Shirt number for {name} must be 0 to 99.'
        if shirt and shirt in seen_shirts:
            return None, f'Shirt #{shirt} is used twice.'
        role = 'starter' if form.get(f'role_{i}') != 'sub' else 'sub'
        seen_names.add(name.lower())
        if shirt:
            seen_shirts.add(shirt)
        players.append({'name': name, 'shirt': shirt, 'position': clean(form.get(f'position_{i}'), 20), 'role': role,
                        'athlete_id': roster_names.get(name.lower())})
    starters = [p for p in players if p['role'] == 'starter']
    subs = [p for p in players if p['role'] == 'sub']
    if len(starters) != starters_needed:
        return None, f'A {sport.lower()} line-up needs exactly {starters_needed} starters (you have {len(starters)}).'
    if len(subs) > subs_max:
        return None, f'At most {subs_max} substitutes.'
    return players, None


def clean_summary(text):
    summary = clean(text, 120)
    if summary and len(summary.split()) > SUMMARY_MAX_WORDS:
        return None, f'The summary can be {SUMMARY_MAX_WORDS} words at most.'
    return summary, None


def result_label(game):
    if game.score_for is None or game.score_against is None:
        return ''
    if game.score_for > game.score_against:
        return 'W'
    return 'L' if game.score_for < game.score_against else 'D'


def head_to_head(games):
    """{'w', 'd', 'l', 'for', 'against', 'games'} for finished games (newest first)."""
    done = [g for g in games if g.score_for is not None and g.score_against is not None]
    out = {'w': 0, 'd': 0, 'l': 0, 'for': 0, 'against': 0, 'games': done}
    for g in done:
        out[{'W': 'w', 'D': 'd', 'L': 'l'}[result_label(g)]] += 1
        out['for'] += g.score_for
        out['against'] += g.score_against
    return out


def lineup_pdf(game, org_name):
    """The line-up as a one-page PDF."""
    pdf = FPDF(format='A4')
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_fill_color(*GREEN_DARK)
    pdf.rect(0, 0, 210, 34, 'F')
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 18)
    pdf.set_xy(12, 9)
    pdf.cell(0, 8, pdf_text(f'{org_name} - line-up'))
    pdf.set_font('Helvetica', '', 10)
    pdf.set_xy(12, 19)
    pdf.cell(0, 6, pdf_text(f'{game.sport} - {game.competition} - vs {game.opponent} - '
                             f'{game.kickoff_at.strftime("%d %b %Y, %H:%M")} UTC - {game.venue}'))
    pdf.set_text_color(*INK)
    y = 44
    for title, role in (('Starting line-up', 'starter'), ('Substitutes', 'sub')):
        players = [p for p in lineup_of(game) if p['role'] == role]
        pdf.set_xy(12, y)
        pdf.set_font('Helvetica', 'B', 12)
        pdf.set_text_color(*GREEN)
        pdf.cell(0, 7, pdf_text(f'{title} ({len(players)})'))
        y += 9
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_fill_color(*GREEN_LIGHT)
        pdf.set_text_color(*INK)
        pdf.set_xy(12, y)
        pdf.cell(16, 7, '#', fill=True)
        pdf.cell(110, 7, 'Player', fill=True)
        pdf.cell(60, 7, 'Position', fill=True)
        y += 7
        pdf.set_font('Helvetica', '', 10)
        for p in players:
            pdf.set_xy(12, y)
            pdf.cell(16, 7, pdf_text(p.get('shirt') or '-'))
            pdf.cell(110, 7, pdf_text(p['name'] + ('  (on D.A.R.T.)' if p.get('athlete_id') else '')))
            pdf.cell(60, 7, pdf_text(p.get('position') or '-'))
            y += 7
        y += 6
    pdf.set_xy(12, y + 2)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(*GREY)
    saved = game.lineup_saved_at.strftime('%d %b %Y, %H:%M') if game.lineup_saved_at else '-'
    pdf.multi_cell(186, 5, pdf_text(f'Saved on D.A.R.T. {saved} UTC (locked {LINEUP_LOCK_MINUTES} minutes before kick-off). '
                                     + ('Confirmed by the D.A.R.T Admin.' if game.lineup_confirmed_at else 'Waiting for D.A.R.T Admin confirmation.')))
    return bytes(pdf.output())
