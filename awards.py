"""
Weekly / monthly awards: Highlight, Goal, Assist... of the Week / Month.

The D.A.R.T Admin creates a contest with three dates:
  opens_at        -> athletes (team or individual) enter one approved highlight
  entry_deadline  -> entries close, members vote (one vote each, changeable)
  voting_ends_at  -> voting closes; the entry with the most votes wins
Entries are shown for voting only after the D.A.R.T Admin approves them.
"""
from datetime import datetime

from sqlalchemy import func

from models import db, AwardContest, AwardEntry, AwardVote, AthleteHighlight

AWARDS = {
    'highlight': ('Highlight', None, 'fa-clapperboard'),
    'goal': ('Goal', 'Football', 'fa-futbol'),
    'assist': ('Assist', None, 'fa-handshake-angle'),
    'save': ('Save', 'Football', 'fa-hand'),
    'skill': ('Skill', None, 'fa-wand-magic-sparkles'),
    'dunk': ('Dunk', 'Basketball', 'fa-basketball'),
    'block': ('Block', 'Basketball', 'fa-shield-halved'),
    'home_run': ('Home Run', 'Kickball', 'fa-baseball'),
}
PERIODS = {'week': 'Week', 'month': 'Month'}
SPORTS = ('Football', 'Basketball', 'Kickball')
MAX_NOTE = 150


def title_for(award, period, sport=None):
    label = AWARDS[award][0]
    return f'{label} of the {PERIODS[period]}' + (f' · {sport}' if sport else '')


def phase(contest, now=None):
    now = now or datetime.utcnow()
    if now < contest.opens_at:
        return 'upcoming'
    if now < contest.entry_deadline:
        return 'entries'
    if now < contest.voting_ends_at:
        return 'voting'
    return 'closed'


def vote_counts(contest_id):
    return dict(db.session.query(AwardVote.entry_id, func.count(AwardVote.id))
                .filter(AwardVote.contest_id == contest_id).group_by(AwardVote.entry_id).all())


def approved_entries(contest):
    return [e for e in contest.entries if e.status == 'approved']


def ranked(contest):
    """[(entry, votes)] most votes first; ties: the earlier entry first."""
    counts = vote_counts(contest.id)
    return sorted(((e, counts.get(e.id, 0)) for e in approved_entries(contest)), key=lambda ev: (-ev[1], ev[0].id))


def decide(contest, now=None):
    """Once voting has closed, record the winner (most votes). Returns the winning entry or None. Caller commits."""
    if contest.decided_at or phase(contest, now) != 'closed':
        return db.session.get(AwardEntry, contest.winner_entry_id) if contest.winner_entry_id else None
    order = ranked(contest)
    winner = order[0][0] if order and order[0][1] > 0 else None
    contest.winner_entry_id = winner.id if winner else None
    contest.decided_at = datetime.utcnow()
    return winner


def recent_results(limit=6):
    """
    Finished awards with their podium, newest first:
    [{'contest': c, 'podium': [(entry, votes), ...up to 3], 'winner': entry or None}].
    Read-only (the winner is the most-voted entry), for coach / scout / organization dashboards.
    """
    now = datetime.utcnow()
    contests = (AwardContest.query.filter(AwardContest.voting_ends_at <= now)
                .order_by(AwardContest.voting_ends_at.desc()).limit(limit).all())
    results = []
    for c in contests:
        podium = [(e, v) for e, v in ranked(c) if v > 0][:3]
        if not podium:
            continue
        winner = next((e for e, _ in podium if e.id == c.winner_entry_id), podium[0][0])
        results.append({'contest': c, 'podium': podium, 'winner': winner})
    return results


def entry_error(contest, athlete, highlight):
    """Why this athlete can't enter this highlight now (None = OK)."""
    if phase(contest) != 'entries':
        return 'Entries for this award are closed.'
    if athlete.role != 'Athlete' or not athlete.is_verified or athlete.is_suspended:
        return 'Only verified athletes can enter.'
    if not highlight or highlight.user_id != athlete.id or highlight.status != 'approved':
        return 'Choose one of your own approved highlights.'
    if contest.sport and highlight.sport and highlight.sport != contest.sport:
        return f'This award is for {contest.sport} highlights.'
    if AwardEntry.query.filter_by(contest_id=contest.id, athlete_id=athlete.id).first():
        return 'You have already entered this award.'
    return None


def vote_error(contest, entry, voter):
    if phase(contest) != 'voting':
        return 'Voting is not open for this award.'
    if not entry or entry.contest_id != contest.id or entry.status != 'approved':
        return 'This entry is not in the vote.'
    if not getattr(voter, 'is_authenticated', False) or voter.is_suspended:
        return 'Please log in to vote.'
    if entry.athlete_id == voter.id:
        return "You can't vote for your own entry."
    return None


def cast_vote(contest, entry, voter):
    """One vote per member per contest; voting again moves it. Caller commits."""
    vote = AwardVote.query.filter_by(contest_id=contest.id, voter_id=voter.id).first()
    if vote:
        vote.entry_id = entry.id
        vote.created_at = datetime.utcnow()
    else:
        db.session.add(AwardVote(contest_id=contest.id, entry_id=entry.id, voter_id=voter.id))


def my_vote(contest, voter):
    if not getattr(voter, 'is_authenticated', False):
        return None
    vote = AwardVote.query.filter_by(contest_id=contest.id, voter_id=voter.id).first()
    return vote.entry_id if vote else None


def eligible_highlights(athlete, contest):
    q = AthleteHighlight.query.filter_by(user_id=athlete.id, status='approved')
    if contest.sport:
        q = q.filter(db.or_(AthleteHighlight.sport == contest.sport, AthleteHighlight.sport.is_(None)))
    return q.order_by(AthleteHighlight.created_at.desc()).limit(30).all()
