"""
Official Game Record PDF (one approved D.A.R.T. record per page).

Built with fpdf2 (pure Python, works on Vercel). Core PDF fonts only cover
Latin-1, so text is cleaned with pdf_text() first.
"""
import io
import os
from datetime import datetime, timezone

import qrcode
from fpdf import FPDF

GREEN_DARK = (7, 18, 8)
GREEN = (25, 135, 84)
GREEN_LIGHT = (232, 245, 238)
GOLD = (245, 197, 66)
GREY = (108, 117, 125)
INK = (33, 37, 41)

# What each sport's record shows, in order: (label, value function).
SPORT_STATS = {
    'Football': [
        ('Goals', lambda r: r.goals or 0),
        ('Assists', lambda r: r.assists or 0),
        ('Yellow cards', lambda r: r.yellow_cards or 0),
        ('Red cards', lambda r: r.red_cards or 0),
        ('Clean sheets', lambda r: r.clean_sheets or 0),
        ('Saves', lambda r: r.saves or 0),
    ],
    'Basketball': [
        ('Points', lambda r: r.points or 0),
        ('Assists', lambda r: r.assists or 0),
        ('Rebounds (total)', lambda r: r.total_rebounds or 0),
        ('Offensive rebounds', lambda r: r.offensive_rebounds or 0),
        ('Defensive rebounds', lambda r: r.defensive_rebounds or 0),
        ('Blocks', lambda r: r.blocks or 0),
        ('Sent off court', lambda r: r.sent_off or 0),
    ],
    'Kickball': [
        ('Home runs', lambda r: r.home_runs or 0),
        ('Cut base', lambda r: r.cut_base or 0),
        ('Fouls played', lambda r: r.foul_played or 0),
        ('Yellow cards', lambda r: r.kickball_yellow_cards or 0),
        ('Red cards', lambda r: r.kickball_red_cards or 0),
    ],
}

_REPLACEMENTS = {
    '’': "'", '‘': "'", '“': '"', '”': '"', '–': '-', '—': '-',
    '≥': '>=', '≤': '<=', '·': '-', '…': '...', '•': '-',
}


def pdf_text(value):
    """Text safe for the built-in PDF fonts (Latin-1)."""
    text = '' if value is None else str(value)
    for bad, good in _REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text.encode('latin-1', 'replace').decode('latin-1')


def stat_rows(record):
    rows = SPORT_STATS.get(record.sport, [])
    if record.sport == 'Football' and record.position != 'GK':
        rows = [row for row in rows if row[0] not in ('Clean sheets', 'Saves')]
    return [(label, fn(record)) for label, fn in rows]


def _qr_png(url):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    # RGB, not 1-bit: 1-bit PNGs don't embed correctly in the PDF.
    image = qr.make_image(fill_color='black', back_color='white').get_image().convert('RGB')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


class _RecordPDF(FPDF):
    def footer(self):
        self.set_y(-14)
        self.set_font('Helvetica', '', 7.5)
        self.set_text_color(*GREY)
        self.cell(
            0, 5,
            pdf_text('D.A.R.T. (D3ll Athlete Records Tracker) - official records, approved by coaches. '
                     'Scan the QR code or open the link to check it online.'),
            align='C'
        )


def build_record_pdf(record, athlete, share_url, logo_path=None):
    """Return the PDF bytes for one approved record."""
    pdf = _RecordPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(pdf_text(f'{athlete.full_name} - {record.sport} record'))
    pdf.set_author('D.A.R.T.')
    pdf.set_creator('D.A.R.T. Athlete Records Tracker')
    pdf.add_page()

    page_w = pdf.w - 2 * pdf.l_margin

    # ---------- Header band ----------
    pdf.set_fill_color(*GREEN_DARK)
    pdf.rect(0, 0, pdf.w, 38, style='F')
    pdf.set_fill_color(*GREEN)
    pdf.rect(0, 38, pdf.w, 1.6, style='F')

    if logo_path and os.path.exists(logo_path):
        try:
            pdf.image(logo_path, x=pdf.l_margin, y=9, h=20)
        except Exception:
            pass

    pdf.set_xy(pdf.l_margin + 26, 10)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 20)
    pdf.cell(0, 9, 'Official Game Record')
    pdf.set_xy(pdf.l_margin + 26, 20)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(*GOLD)
    pdf.cell(0, 6, pdf_text(f'D.A.R.T. verified {record.sport} record  -  Record #{record.id}'))

    # ---------- Athlete ----------
    pdf.set_xy(pdf.l_margin, 48)
    pdf.set_text_color(*INK)
    pdf.set_font('Helvetica', 'B', 18)
    name = athlete.full_name + (f'   #{record.shirt_number}' if record.shirt_number else '')
    pdf.cell(page_w, 9, pdf_text(name), new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 10.5)
    pdf.set_text_color(*GREY)
    details = [record.team or athlete.school, athlete.nationality, record.position]
    pdf.cell(page_w, 6, pdf_text('  |  '.join(d for d in details if d)), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(4)

    # ---------- Match facts (two columns) ----------
    facts = [
        ('Sport', record.sport),
        ('Game date', record.game_date.strftime('%B %d, %Y') if record.game_date else '-'),
        ('Season', record.year),
        ('Team', record.team or '-'),
        ('Opponent', record.team_played_against or '-'),
        ('Competition', record.competition_display if hasattr(record, 'competition_display') else record.competition_category),
        ('Trophy', record.trophy or 'None'),
        ('Position', record.position or '-'),
        ('Minutes played', record.match_minutes_played or 0),
        ('Man of the Match' if record.sport != 'Kickball' else 'QOTM', 'Yes' if record.man_of_the_match else 'No'),
        ('MVP', record.mvp or 0),
    ]

    col_w = page_w / 2
    pdf.set_draw_color(222, 226, 230)
    for i, (label, value) in enumerate(facts):
        x = pdf.l_margin + (i % 2) * col_w
        if i % 2 == 0 and i:
            pdf.ln(12)
        y = pdf.get_y()
        pdf.set_xy(x, y)
        pdf.set_font('Helvetica', 'B', 8)
        pdf.set_text_color(*GREY)
        pdf.cell(col_w - 4, 4, pdf_text(label.upper()))
        pdf.set_xy(x, y + 4)
        pdf.set_font('Helvetica', '', 11)
        pdf.set_text_color(*INK)
        pdf.cell(col_w - 4, 5, pdf_text(value))
        pdf.set_xy(x, y)
    pdf.ln(16)

    # ---------- Stats table ----------
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(*INK)
    pdf.cell(page_w, 8, 'Game statistics', new_x='LMARGIN', new_y='NEXT')

    pdf.set_fill_color(*GREEN)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(page_w * 0.7, 8, '  Statistic', border=0, fill=True)
    pdf.cell(page_w * 0.3, 8, 'Value  ', border=0, fill=True, align='R', new_x='LMARGIN', new_y='NEXT')

    pdf.set_text_color(*INK)
    pdf.set_font('Helvetica', '', 10.5)
    for i, (label, value) in enumerate(stat_rows(record)):
        pdf.set_fill_color(*(GREEN_LIGHT if i % 2 == 0 else (255, 255, 255)))
        pdf.cell(page_w * 0.7, 8, pdf_text('  ' + label), fill=True)
        pdf.set_font('Helvetica', 'B', 10.5)
        pdf.cell(page_w * 0.3, 8, pdf_text(f'{value}  '), fill=True, align='R', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 10.5)

    if record.sport == 'Basketball' and not record.total_rebounds and getattr(record, 'rebound_type', None):
        pdf.set_font('Helvetica', 'I', 9)
        pdf.set_text_color(*GREY)
        pdf.cell(page_w, 6, pdf_text(f'Rebound recorded before counts: {record.rebound_type}'), new_x='LMARGIN', new_y='NEXT')

    pdf.ln(8)

    # ---------- Verification ----------
    top = pdf.get_y()
    if top > pdf.h - 70:
        pdf.add_page()
        top = pdf.get_y()

    box_h = 46
    pdf.set_draw_color(*GREEN)
    pdf.set_line_width(0.6)
    pdf.rect(pdf.l_margin, top, page_w, box_h)

    pdf.image(_qr_png(share_url), x=pdf.l_margin + 4, y=top + 4, w=38, h=38)

    text_x = pdf.l_margin + 48
    pdf.set_xy(text_x, top + 6)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(*GREEN)
    pdf.cell(0, 6, 'APPROVED RECORD')
    pdf.set_xy(text_x, top + 14)
    pdf.set_font('Helvetica', '', 9.5)
    pdf.set_text_color(*INK)
    pdf.multi_cell(
        page_w - 52, 5,
        pdf_text('This game record was submitted by the athlete and approved by a coach on D.A.R.T. '
                 'Scan the code or open the link below to check it is genuine and current.')
    )
    pdf.set_x(text_x)
    pdf.set_font('Helvetica', 'U', 8.5)
    pdf.set_text_color(*GREEN)
    pdf.cell(page_w - 52, 5, pdf_text(share_url), link=share_url, new_x='LMARGIN', new_y='NEXT')
    pdf.set_x(text_x)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(*GREY)
    generated = datetime.now(timezone.utc).strftime('%B %d, %Y %H:%M UTC')
    pdf.cell(page_w - 52, 5, pdf_text(f'Generated {generated}'))

    return bytes(pdf.output())


# ==========================================================
# PROFILE PDF (athlete / coach / scout card + career)
# ==========================================================

def _headline_stat(record):
    if record.sport == 'Football':
        return f'{record.goals or 0} G - {record.assists or 0} A'
    if record.sport == 'Basketball':
        return f'{record.points or 0} PTS - {record.total_rebounds or 0} REB'
    return f'{record.home_runs or 0} HR'


def build_profile_pdf(info, card_png, records, profile_url):
    """
    info: profile_card_info() dict. card_png: the card image bytes.
    records: approved records to list (already filtered for visibility).
    """
    pdf = _RecordPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(pdf_text(f"{info['name']} - D.A.R.T. profile"))
    pdf.set_author('D.A.R.T.')
    pdf.add_page()
    page_w = pdf.w - 2 * pdf.l_margin

    # Header band
    pdf.set_fill_color(*GREEN_DARK)
    pdf.rect(0, 0, pdf.w, 26, style='F')
    pdf.set_fill_color(*GREEN)
    pdf.rect(0, 26, pdf.w, 1.4, style='F')
    pdf.set_xy(pdf.l_margin, 8)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 8, pdf_text(f"Official {info['eyebrow']}"))
    pdf.set_xy(pdf.l_margin, 8)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(*GOLD)
    pdf.cell(page_w, 8, 'D.A.R.T.' + (' VERIFIED' if info['verified'] else ''), align='R')

    # The shareable card
    card_h = page_w * 630 / 1200
    pdf.image(io.BytesIO(card_png), x=pdf.l_margin, y=34, w=page_w, h=card_h)
    pdf.set_y(34 + card_h + 8)

    # Career summary (athletes)
    career = info.get('career') or []
    if career:
        pdf.set_font('Helvetica', 'B', 12)
        pdf.set_text_color(*INK)
        pdf.cell(page_w, 8, 'Career on D.A.R.T.', new_x='LMARGIN', new_y='NEXT')
        cols = [('Sport', 0.22), ('Games', 0.14), ('Key stats', 0.44), ('Awards', 0.20)]
        pdf.set_fill_color(*GREEN)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font('Helvetica', 'B', 9.5)
        for label, frac in cols:
            pdf.cell(page_w * frac, 7, '  ' + label, fill=True)
        pdf.ln(7)
        pdf.set_text_color(*INK)
        for i, c in enumerate(career):
            if c['sport'] == 'Football':
                key = f"{c['goals']} goals - {c['assists']} assists"
            elif c['sport'] == 'Basketball':
                key = f"{c['points']} points - {c['rebounds']} rebounds"
            else:
                key = f"{c['home_runs']} home runs"
            pdf.set_fill_color(*(GREEN_LIGHT if i % 2 == 0 else (255, 255, 255)))
            pdf.set_font('Helvetica', '', 10)
            for value, (_, frac) in zip((c['sport'], c['games'], key, c['awards']), cols):
                pdf.cell(page_w * frac, 7, pdf_text(f'  {value}'), fill=True)
            pdf.ln(7)
        pdf.ln(4)

    # Recent approved games
    if records:
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(page_w, 8, 'Recent approved games', new_x='LMARGIN', new_y='NEXT')
        cols = [('Date', 0.17), ('Sport', 0.15), ('Opponent', 0.27), ('Competition', 0.23), ('Stats', 0.18)]
        pdf.set_fill_color(*GREEN)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font('Helvetica', 'B', 9)
        for label, frac in cols:
            pdf.cell(page_w * frac, 6.5, '  ' + label, fill=True)
        pdf.ln(6.5)
        pdf.set_text_color(*INK)
        pdf.set_font('Helvetica', '', 9)
        for i, r in enumerate(records):
            values = (
                r.game_date.strftime('%b %d, %Y') if r.game_date else str(r.year),
                r.sport,
                (r.team_played_against or '-')[:26],
                (getattr(r, 'competition_display', None) or r.competition_category or '-')[:24],
                _headline_stat(r),
            )
            pdf.set_fill_color(*(GREEN_LIGHT if i % 2 == 0 else (255, 255, 255)))
            for value, (_, frac) in zip(values, cols):
                pdf.cell(page_w * frac, 6.5, pdf_text(f'  {value}'), fill=True)
            pdf.ln(6.5)
        pdf.ln(4)

    # Verification box with QR to the live profile
    top = pdf.get_y()
    if top > pdf.h - 62:
        pdf.add_page()
        top = pdf.get_y()
    pdf.set_draw_color(*GREEN)
    pdf.set_line_width(0.6)
    pdf.rect(pdf.l_margin, top, page_w, 40)
    pdf.image(_qr_png(profile_url), x=pdf.l_margin + 4, y=top + 4, w=32, h=32)
    tx = pdf.l_margin + 42
    pdf.set_xy(tx, top + 6)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(*GREEN)
    pdf.cell(0, 6, 'D.A.R.T. VERIFIED PROFILE' if info['verified'] else 'D.A.R.T. PROFILE')
    pdf.set_xy(tx, top + 13)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(*INK)
    pdf.multi_cell(page_w - 46, 4.6, pdf_text(
        'Records on D.A.R.T. are approved by coaches. Scan the code or open the link '
        'to see the live profile and every approved game.'))
    pdf.set_x(tx)
    pdf.set_font('Helvetica', 'U', 8.5)
    pdf.set_text_color(*GREEN)
    pdf.cell(page_w - 46, 5, pdf_text(profile_url), link=profile_url, new_x='LMARGIN', new_y='NEXT')
    pdf.set_x(tx)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(*GREY)
    pdf.cell(page_w - 46, 5, pdf_text('Generated ' + datetime.now(timezone.utc).strftime('%B %d, %Y %H:%M UTC')))

    return bytes(pdf.output())
