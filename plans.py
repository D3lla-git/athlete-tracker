"""
D.A.R.T. plans: prices, limits and what each plan unlocks.

Single source of truth for the pricing page, checkout, upload limits,
advanced search, record visibility and ads. Premium status always comes
from the server (an active, confirmed Subscription), never from the page.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import g, has_request_context
from sqlalchemy import func, or_

from models import db, User, SportRecord, Subscription, AthleteHighlight


# ==========================================================
# PLAN CATALOG
# ==========================================================

PERIOD_DAYS = 30

# Free-plan monthly limits count uploads from this date (the first full
# month after the limits were introduced), so nobody is cut off mid-month.
FREE_LIMITS_START = date(2026, 11, 1)

PAYMENT_METHODS = {
    'orange_money': 'Orange Money',
    'mtn_momo': 'MTN MoMo',
    'card': 'Credit / Visa card',
}

MOMO_METHODS = ('orange_money', 'mtn_momo')

# Athlete categories (as stored in User.athlete_category).
HIGH_SCHOOL = 'High School Athlete'
COMMUNITY = 'Community/Area League Athlete'
UNIVERSITY = 'University Athlete'
COUNTY_MEET = 'County Meet Athlete'
CLUB_LEAGUE = 'Club League Athlete'
ALL_ATHLETE = 'All Athlete'


@dataclass(frozen=True)
class Limits:
    """Monthly upload limits. None = unlimited."""
    records: int | None
    photos: int | None
    videos: int | None


@dataclass(frozen=True)
class Plan:
    code: str
    audience: str                  # athlete / scout / organization
    name: str
    price: Decimal
    methods: tuple
    features: tuple
    limits: Limits = Limits(None, None, None)
    category: str | None = None    # athletes: the category this price is for
    advanced_search: bool = False
    # Where a premium athlete's full record history is shown to others.
    visible_on_dashboards: bool = False
    visible_in_search: bool = False
    highlight: bool = False        # "most popular" style emphasis

    @property
    def price_label(self):
        return f'${self.price:.2f}'


FREE_ATHLETE_LIMITS = Limits(records=3, photos=2, videos=1)

_UNLIMITED_ATHLETE_FEATURES = (
    'Unlimited sports record uploads',
    'Unlimited photo & video highlights',
    'Advanced search filters',
    'All your records visible to premium scouts (dashboards + search)',
    'Recruit-Ready premium profile',
    'No ads',
)


def _athlete_unlimited(code, category, name, price, highlight=False):
    return Plan(
        code=code, audience='athlete', name=name, price=Decimal(price),
        methods=MOMO_METHODS, features=_UNLIMITED_ATHLETE_FEATURES,
        category=category, advanced_search=True,
        visible_on_dashboards=True, visible_in_search=True, highlight=highlight,
    )


PLANS = {
    plan.code: plan for plan in (
        Plan(
            code='athlete_high_school', audience='athlete', name='High School Premium',
            price=Decimal('2.50'), methods=MOMO_METHODS, category=HIGH_SCHOOL,
            limits=Limits(records=10, photos=5, videos=5),
            visible_on_dashboards=True,
            features=(
                '10 sports record uploads a month',
                '10 highlights a month (5 photos + 5 videos)',
                'All your records visible to premium scouts on their dashboards',
                'Recruit-Ready premium profile',
                'No ads',
            ),
        ),
        _athlete_unlimited('athlete_community', COMMUNITY, 'Community Premium', '10.00'),
        _athlete_unlimited('athlete_university', UNIVERSITY, 'University Premium', '15.00'),
        _athlete_unlimited('athlete_county_meet', COUNTY_MEET, 'County Meet Premium', '20.00'),
        _athlete_unlimited('athlete_club_league', CLUB_LEAGUE, 'Club League Premium', '20.00'),
        _athlete_unlimited('athlete_all', ALL_ATHLETE, 'All-Athletes Premium', '50.00', highlight=True),
        Plan(
            code='scout_pro', audience='scout', name='Scout Pro', price=Decimal('99.90'),
            methods=('card',) + MOMO_METHODS, advanced_search=True, highlight=True,
            features=(
                'Filter verified talent',
                'Advanced filters',
                'Multi-metric custom queries',
                'Full scout dashboard: season leaders, rising talent, saved searches',
                'Full record history of premium athletes',
                'No ads',
            ),
        ),
        Plan(
            code='organization_pro', audience='organization', name='Organization Pro',
            price=Decimal('99.90'), methods=('card',) + MOMO_METHODS, advanced_search=True,
            highlight=True,
            features=(
                'Full roster of every athlete registered under your name',
                'Approved records, highlights and team leaders',
                'Roster search, filters and sport breakdown',
                'Secure roster export (audited)',
                'Advanced search filters',
                'No ads',
            ),
        ),
    )
}

ATHLETE_PLAN_BY_CATEGORY = {
    plan.category: plan for plan in PLANS.values() if plan.audience == 'athlete'
}

AUDIENCE_FOR_ROLE = {'Athlete': 'athlete', 'Scout': 'scout', 'Organization': 'organization'}

# Free organization accounts see this many roster athletes (names only).
FREE_ORG_ROSTER_PREVIEW = 5


# ==========================================================
# SUBSCRIPTIONS
# ==========================================================

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cache():
    if has_request_context():
        if not hasattr(g, '_dart_plans'):
            g._dart_plans = {}
        return g._dart_plans
    return {}


def clear_plan_cache():
    if has_request_context() and hasattr(g, '_dart_plans'):
        del g._dart_plans


def active_subscription(user):
    """The user's current paid subscription (confirmed and not expired), or None."""
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'id', None):
        return None

    cache = _cache()
    key = ('sub', user.id)

    if key not in cache:
        cache[key] = (
            Subscription.query.filter(
                Subscription.user_id == user.id,
                Subscription.status == 'active',
                Subscription.plan_code.in_(PLANS.keys()),
                Subscription.current_period_end > utcnow(),
            )
            .order_by(Subscription.current_period_end.desc())
            .first()
        )

    return cache[key]


def current_plan(user):
    subscription = active_subscription(user)
    return PLANS.get(subscription.plan_code) if subscription else None


def is_premium(user):
    return current_plan(user) is not None


def is_staff(user):
    """Coaches and the Super Admin manage records; plans don't apply to them."""
    return bool(user and getattr(user, 'is_authenticated', False) and user.role in ('Coach', 'System'))


def plan_for_user(user):
    """The plan this user would buy (athletes: by their category)."""
    audience = AUDIENCE_FOR_ROLE.get(getattr(user, 'role', None))

    if audience == 'athlete':
        return ATHLETE_PLAN_BY_CATEGORY.get(user.athlete_category)
    if audience == 'scout':
        return PLANS['scout_pro']
    if audience == 'organization':
        return PLANS['organization_pro']
    return None


def eligibility_error(user, plan):
    """Why this user can't buy this plan, or None if they can."""
    if not user or not user.is_authenticated:
        return 'Please log in or create an account first.'

    if AUDIENCE_FOR_ROLE.get(user.role) != plan.audience:
        return f'{plan.name} is for {plan.audience} accounts.'

    if not user.is_verified:
        return 'Your account must be verified before you can upgrade.'

    if plan.audience == 'athlete' and user.athlete_category != plan.category:
        return (
            f'{plan.name} is for verified {plan.category}s. Your category is '
            f'{user.athlete_category or "not set"}.'
        )

    return None


# ==========================================================
# FEATURES
# ==========================================================

def can_use_advanced_search(user):
    if is_staff(user):
        return True
    plan = current_plan(user)
    return bool(plan and plan.advanced_search)


def shows_ads(user):
    """Free users and visitors see ads; premium users and staff don't."""
    if not user or not getattr(user, 'is_authenticated', False):
        return True
    return not (is_staff(user) or is_premium(user))


def scout_has_full_dashboard(user):
    return is_staff(user) or (user.role == 'Scout' and is_premium(user))


def upload_limits(user):
    """Monthly limits for an athlete, or Limits(None, None, None) when unlimited."""
    plan = current_plan(user)
    if plan:
        return plan.limits
    return FREE_ATHLETE_LIMITS


def month_start(today=None):
    today = today or utcnow().date()
    start = today.replace(day=1)
    return max(start, FREE_LIMITS_START)


def next_month_start(today=None):
    today = today or utcnow().date()
    return (today.replace(day=1) + timedelta(days=32)).replace(day=1)


def limits_active(today=None):
    return (today or utcnow().date()) >= FREE_LIMITS_START


def monthly_usage(user, today=None):
    """Uploads counted against this month's limits."""
    since = datetime.combine(month_start(today), datetime.min.time())

    records = SportRecord.query.filter(
        SportRecord.user_id == user.id,
        SportRecord.created_at >= since,
    ).count()

    highlight_counts = dict(
        db.session.query(AthleteHighlight.media_type, func.count(AthleteHighlight.id))
        .filter(AthleteHighlight.user_id == user.id, AthleteHighlight.created_at >= since,
                AthleteHighlight.status != 'rejected')   # rejected uploads don't use up the month
        .group_by(AthleteHighlight.media_type)
        .all()
    )

    return {
        'records': records,
        'photos': highlight_counts.get('photo', 0),
        'videos': highlight_counts.get('video', 0),
    }


def limit_reached(user, kind, today=None):
    """
    None if the athlete may upload one more `kind` ('records', 'photos',
    'videos') this month, else a friendly message.
    """
    if not limits_active(today):
        return None

    limit = getattr(upload_limits(user), kind)

    if limit is None:
        return None

    used = monthly_usage(user, today)[kind]

    if used < limit:
        return None

    label = {'records': 'sports records', 'photos': 'photos', 'videos': 'videos'}[kind]
    resets = next_month_start(today).strftime('%B %d')
    plan = current_plan(user)

    if plan:
        return f'You have used your {limit} {label} for this month on {plan.name}. Your limit resets on {resets}.'

    return (
        f'Free accounts can upload {limit} {label} a month and you have used them all. '
        f'Upgrade to Premium for more, or wait until {resets}.'
    )


def usage_summary(user, today=None):
    """For the athlete dashboard meter."""
    limits = upload_limits(user)
    used = monthly_usage(user, today)

    def row(kind, label):
        limit = getattr(limits, kind)
        return {
            'label': label,
            'used': used[kind],
            'limit': limit,
            'percent': 0 if not limit else min(100, round(used[kind] * 100 / limit)),
        }

    return {
        'active': limits_active(today),
        'starts': FREE_LIMITS_START,
        'resets': next_month_start(today),
        'rows': [row('records', 'Sports records'), row('photos', 'Photos'), row('videos', 'Videos')],
    }


# ==========================================================
# RECORD VISIBILITY
# ==========================================================
# Free athletes: only their latest approved record is shown to others on
# scout dashboards and in search. Premium athletes: all approved records,
# where their plan allows (High School Premium: dashboards; others: both).
# The athlete themself, coaches and the Super Admin always see everything;
# organizations see their own roster in full on their dashboard.

def full_visibility_athlete_ids(context):
    """Athletes whose whole record history others may see in `context` ('dashboard' / 'search')."""
    cache = _cache()
    key = ('visible', context)

    if key in cache:
        return cache[key]

    codes = [
        plan.code for plan in PLANS.values()
        if plan.audience == 'athlete' and (
            plan.visible_on_dashboards if context == 'dashboard' else plan.visible_in_search
        )
    ]

    ids = {
        row[0] for row in db.session.query(Subscription.user_id).filter(
            Subscription.status == 'active',
            Subscription.plan_code.in_(codes),
            Subscription.current_period_end > utcnow(),
        ).distinct()
    }

    cache[key] = ids
    return ids


def latest_approved_record_ids():
    """Subquery: the newest approved record id of every athlete."""
    ranked = (
        db.session.query(
            SportRecord.id.label('id'),
            func.row_number().over(
                partition_by=SportRecord.user_id,
                order_by=(SportRecord.game_date.desc(), SportRecord.id.desc()),
            ).label('rank'),
        )
        .filter(SportRecord.status == 'approved')
        .subquery()
    )

    return db.session.query(ranked.c.id).filter(ranked.c.rank == 1)


def sees_everything(viewer):
    # Organizations see their own roster in full on their dashboard; in
    # search they follow the same rules as scouts.
    return is_staff(viewer)


def apply_record_visibility(query, viewer, context):
    """Limit a SportRecord query to what `viewer` may see in `context`."""
    if sees_everything(viewer):
        return query

    conditions = [
        SportRecord.id.in_(latest_approved_record_ids()),
    ]

    full_ids = full_visibility_athlete_ids(context)
    if full_ids:
        conditions.append(SportRecord.user_id.in_(full_ids))

    if viewer and getattr(viewer, 'is_authenticated', False):
        conditions.append(SportRecord.user_id == viewer.id)

    return query.filter(or_(*conditions))


# ==========================================================
# SUBSCRIPTION CHANGES (called after a payment is confirmed)
# ==========================================================

def activate_plan(user, plan, provider='manual'):
    """
    Start or extend `plan` for `user` by PERIOD_DAYS. Paying again while
    active extends from the current end date. Returns the Subscription.
    """
    now = utcnow()

    current = (
        Subscription.query.filter(
            Subscription.user_id == user.id,
            Subscription.plan_code == plan.code,
            Subscription.status == 'active',
            Subscription.current_period_end > now,
        )
        .order_by(Subscription.current_period_end.desc())
        .first()
    )

    if current:
        current.current_period_end = current.current_period_end + timedelta(days=PERIOD_DAYS)
        subscription = current
    else:
        # A different plan (e.g. category changed) replaces the old one.
        for old in Subscription.query.filter(
            Subscription.user_id == user.id,
            Subscription.status == 'active',
        ).all():
            old.status = 'canceled'

        subscription = Subscription(
            user_id=user.id,
            plan_code=plan.code,
            status='active',
            provider=provider,
            current_period_start=now,
            current_period_end=now + timedelta(days=PERIOD_DAYS),
        )
        db.session.add(subscription)

    db.session.flush()
    clear_plan_cache()
    return subscription
