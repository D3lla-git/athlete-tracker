from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash  # type: ignore[import-not-found]
from competitions import COMPETITION_TEAM_LABELS
from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB


db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.BigInteger, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    school = db.Column(db.String(150), nullable=False)
    # Athlete / Coach category permissions
    athlete_category = db.Column(db.String(50), nullable=True)
    coach_category = db.Column(db.String(50), nullable=True)
    gender = db.Column(db.String(10), nullable=True)
    # Athlete / Coach personal information
    age = db.Column(db.Integer, nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    nationality = db.Column(db.String(100), nullable=True)
    height_cm = db.Column(db.Numeric(6, 2), nullable=True)
    weight_kg = db.Column(db.Numeric(6, 2), nullable=True)
    preferred_foot = db.Column(db.String(20), nullable=True)
    # Current shirt number (optional, "0"-"99"; text so "00" is kept).
    shirt_number = db.Column(db.String(2), nullable=True)
    # Google search listing: None = default (adults listed, under-18s not),
    # 'on' = athlete chose to be listed, 'off' = athlete chose not to be.
    search_visibility = db.Column(db.String(10), nullable=True)
    email = db.Column(db.String(255), nullable=True, unique=True)

        # Password reset security fields
    reset_token = db.Column(db.String(128), nullable=True, unique=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)

    # Two-factor authentication security fields
    two_factor_secret = db.Column(db.String(32), nullable=True)
    two_factor_enabled = db.Column(db.Boolean, default=False, nullable=False)
    # 2f recovery code security field
    two_factor_recovery_codes = db.Column(db.Text, nullable=True)

    password_hash = db.Column(db.String(256), nullable=False)
    # Google account ID ("sub") once Google sign-in is connected.
    google_sub = db.Column(db.String(255), nullable=True, unique=True)
    role = db.Column(db.String(20), default='Athlete')  # Athlete, Coach, scout or System
    id_document = db.Column(db.String(255), nullable=True)      # from registration
    profile_picture = db.Column(db.String(255), nullable=True)  # optional update
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('full_name', 'school', name='unique_student_school'),)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class SportRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sport = db.Column(db.String(30), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    # Date on which the actual game/match was played.
    # This allows multiple records for the same competition,
    # sport, team and year.
    game_date = db.Column(db.Date, nullable=True)
    position = db.Column(db.String(50))
    # Shirt number worn in this game (optional).
    shirt_number = db.Column(db.String(2), nullable=True)
    games_played = db.Column(db.Integer, default=0)
    trophy = db.Column(db.String(100), nullable=True)
    team = db.Column(db.String(100), nullable=True)
    team_played_against = db.Column(db.String(100), nullable=True)
    school = db.Column(db.String(150), nullable=True)
    # Competition category
    competition_category = db.Column(db.String(50),nullable=False,default='County Meet')
    club_division = db.Column(db.String(30),nullable=True)
    # National-team competitions only: the AFCON / World Cup team or the
    # WAFU age category (e.g. "Lonestar Men's Team", "Male-U17").
    # competition_category says which of the three it is.
    competition_team = db.Column(db.String(50), nullable=True)
    # Grassroots League only: LFA age group (U-10, U-12, U-15, U-17).
    age_group = db.Column(db.String(10), nullable=True)

    # Football fields
    goals = db.Column(db.Integer, default=0)
    assists = db.Column(db.Integer, default=0)
    yellow_cards = db.Column(db.Integer, default=0)
    red_cards = db.Column(db.Integer, default=0)
    clean_sheets = db.Column(db.Integer, default=0)
    saves = db.Column(db.Integer, default=0)

    # General performance field
    match_minutes_played = db.Column(db.Integer, default=0)

    # Basketball fields
    points = db.Column(db.Integer, default=0)
    blocks = db.Column(db.Integer, default=0)
    sent_off = db.Column(db.Integer, default=0)
    # Older records only stored which kind of rebound (no count).
    rebound_type = db.Column(db.String(50), nullable=True)
    # Rebound counts for the game.
    offensive_rebounds = db.Column(db.Integer, default=0)
    defensive_rebounds = db.Column(db.Integer, default=0)

    # Kickball fields
    home_runs = db.Column(db.Integer, default=0)
    kickball_red_cards = db.Column(db.Integer, default=0)
    kickball_yellow_cards = db.Column(db.Integer, default=0)
    cut_base = db.Column(db.Integer, default=0)
    foul_played = db.Column(db.Integer, default=0)

    # Awards
    man_of_the_match = db.Column(db.Integer, default=0)
    mvp = db.Column(db.Integer, default=0)

    status = db.Column(db.String(20), default='pending')  # pending / approved / rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='records')

    # What competition_team means for each national-team competition.
    COMPETITION_TEAM_LABELS = COMPETITION_TEAM_LABELS

    @property
    def competition_team_label(self):
        """e.g. 'WAFU Category', or None when there is no competition team."""
        if not self.competition_team:
            return None

        return self.COMPETITION_TEAM_LABELS.get(self.competition_category)

    @property
    def competition_detail(self):
        """Club division, national team/category or age group, if any."""
        return self.club_division or self.competition_team or self.age_group

    @property
    def competition_detail_label(self):
        """Label for competition_detail, e.g. 'Club Division', 'Age Group'."""
        if self.club_division:
            return 'Club Division'

        if self.competition_team:
            return self.competition_team_label

        if self.age_group:
            return 'Age Group'

        return None

    @property
    def total_rebounds(self):
        return (self.offensive_rebounds or 0) + (self.defensive_rebounds or 0)

    @property
    def rebounds_display(self):
        """e.g. 'Off 3 · Def 5 (8 total)'; older records show their rebound type."""
        if self.total_rebounds or not self.rebound_type:
            return (
                f'Off {self.offensive_rebounds or 0} · '
                f'Def {self.defensive_rebounds or 0} '
                f'({self.total_rebounds} total)'
            )

        return self.rebound_type

    @property
    def competition_display(self):
        """Competition with its team/category or age group, e.g. 'WAFU · Male-U17'."""
        detail = self.competition_team or self.age_group

        if detail and self.competition_category:
            return f'{self.competition_category} · {detail}'

        return self.competition_category


class SavedSearch(db.Model):
    """An athlete search a user saved to run again (the search page's filters)."""
    __tablename__ = 'saved_search'

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    name = db.Column(db.String(80), nullable=False)
    # Cleaned search filters, e.g. "sport=Football&gender=Male&stat=goals&stat_op=gte&stat_value=2"
    query_string = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AthleteHighlight(db.Model):
    """
    A photo or short video (60 seconds max) an athlete uploaded to show
    their live action. The file itself is in Supabase Storage, bucket
    'athlete-highlights', at storage_path ("<user_id>/<random>.<ext>").
    """
    __tablename__ = 'athlete_highlight'

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    media_type = db.Column(db.String(10), nullable=False)   # photo / video
    storage_path = db.Column(db.String(255), nullable=False, unique=True)
    content_type = db.Column(db.String(50), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)
    duration_seconds = db.Column(db.Numeric(6, 2), nullable=True)   # videos only
    caption = db.Column(db.String(150), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def is_video(self):
        return self.media_type == 'video'

    @property
    def duration_label(self):
        """e.g. 0:42"""
        if self.duration_seconds is None:
            return ''
        seconds = int(round(float(self.duration_seconds)))
        return f'{seconds // 60}:{seconds % 60:02d}'


class OrganizationProfile(db.Model):
    """
    An Enterprise / Institution / Academy / Club account (User.role =
    'Organization'). Its roster lists every verified athlete whose team
    name matches the organization's name or one of its approved aliases.
    Nothing is shown until the Super Admin verifies the organization.
    """
    __tablename__ = 'organization_profile'

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        unique=True,
        index=True
    )
    org_name = db.Column(db.String(150), nullable=False)
    org_type = db.Column(db.String(50), nullable=False)
    sports = db.Column(db.String(200), nullable=True)
    country = db.Column(db.String(100), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    website = db.Column(db.String(255), nullable=True)
    contact_name = db.Column(db.String(150), nullable=False)
    contact_title = db.Column(db.String(100), nullable=True)
    contact_phone = db.Column(db.String(30), nullable=True)
    # Registration certificate / letterhead (Supabase 'id-documents' bucket).
    document_path = db.Column(db.String(255), nullable=True)
    # Other names athletes use for this team, one per line. Only approved
    # aliases are used for the roster; changes wait for the Super Admin.
    approved_aliases = db.Column(db.Text, nullable=True)
    requested_aliases = db.Column(db.Text, nullable=True)
    verification_status = db.Column(db.String(20), nullable=False, default='pending')  # pending / verified / rejected
    verified_at = db.Column(db.DateTime, nullable=True)
    terms_version = db.Column(db.String(20), nullable=True)
    terms_accepted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        'User',
        backref=db.backref('organization_profile', uselist=False, cascade='all, delete-orphan')
    )

    @staticmethod
    def split_names(text):
        return [line.strip() for line in (text or '').splitlines() if line.strip()]

    @property
    def roster_names(self):
        """Names that put an athlete on this roster."""
        return [self.org_name] + self.split_names(self.approved_aliases)


class OrganizationRosterExclusion(db.Model):
    """An athlete an organization removed from its roster (wrong match)."""
    __tablename__ = 'organization_roster_exclusion'

    id = db.Column(db.BigInteger, primary_key=True)
    organization_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    athlete_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('organization_user_id', 'athlete_id', name='uq_org_roster_exclusion'),
    )


class LoginAttempt(db.Model):
    __tablename__ = 'login_attempt'

    id = db.Column(db.BigInteger, primary_key=True)
    identifier = db.Column(db.String(255), nullable=False, unique=True)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    blocked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    last_attempt = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow
    )

class Registration(db.Model):
    __tablename__ = 'registration'

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, nullable=False)

    registration_type = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    registration_year = db.Column(db.Integer, nullable=False)

    fee_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    payment_status = db.Column(
        db.String(20),
        nullable=False,
        default='unpaid'
    )
    payment_reference = db.Column(db.String(100), nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)

    status = db.Column(
        db.String(20),
        nullable=False,
        default='active'
    )

    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    user = db.relationship(
        'User',
        primaryjoin='Registration.user_id == User.id',
        foreign_keys=[user_id],
        viewonly=True
    )

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'registration_type',
            'category',
            'registration_year',
            name='unique_user_registration_year'
        ),
    )

class ScoutProfile(db.Model):
    __tablename__ = 'scout_profile'

    id = db.Column(
        db.BigInteger,
        primary_key=True
    )

    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        unique=True
    )

    organization = db.Column(
        db.String(200),
        nullable=False
    )

    job_title = db.Column(
        db.String(150),
        nullable=True
    )

    sports = db.Column(
        db.Text,
        nullable=True
    )

    country = db.Column(
        db.String(100),
        nullable=True
    )

    city = db.Column(
        db.String(100),
        nullable=True
    )

    years_experience = db.Column(
        db.Integer,
        nullable=True
    )

    bio = db.Column(
        db.Text,
        nullable=True
    )

    verification_status = db.Column(
        db.String(30),
        nullable=False,
        default='pending'
    )

    verification_document = db.Column(
        db.String(255),
        nullable=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    user = db.relationship(
        'User',
        backref=db.backref(
            'scout_profile',
            uselist=False,
            cascade='all, delete-orphan'
        )
    )

class ChatMessage(db.Model):
    __tablename__ = 'chat_message'

    id = db.Column(db.BigInteger, primary_key=True)

    sender_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False
    )

    recipient_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False
    )

    body = db.Column(db.Text, nullable=False)

    is_read = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )
# ==========================================================
# MONETIZATION / SUBSCRIPTION FOUNDATION
# ==========================================================

class Subscription(db.Model):
    """
    Stores a user's subscription lifecycle.

    IMPORTANT:
    Subscription status is server-side data.
    Never trust a frontend field such as is_premium.
    """

    __tablename__ = 'subscription'

    id = db.Column(db.BigInteger, primary_key=True)

    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Example:
    # athlete_premium
    # organization
    # scout_search
    # api
    plan_code = db.Column(
        db.String(80),
        nullable=False,
        index=True
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default='pending',
        index=True
    )
    # pending / active / past_due / canceled / expired

    provider = db.Column(
        db.String(50),
        nullable=True
    )
    # e.g. stripe, paystack, manual

    provider_customer_id = db.Column(
        db.String(255),
        nullable=True,
        index=True
    )

    provider_subscription_id = db.Column(
        db.String(255),
        nullable=True,
        unique=True,
        index=True
    )

    current_period_start = db.Column(
        db.DateTime,
        nullable=True
    )

    current_period_end = db.Column(
        db.DateTime,
        nullable=True
    )

    cancel_at_period_end = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    user = db.relationship(
        'User',
        backref=db.backref(
            'subscriptions',
            lazy=True,
            cascade='all, delete-orphan'
        )
    )


class Payment(db.Model):
    """
    Internal record of payment attempts/results.

    NEVER store:
    - card number
    - CVV
    - full payment credentials

    Only store provider references and safe transaction metadata.
    """

    __tablename__ = 'payment'

    id = db.Column(db.BigInteger, primary_key=True)

    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    subscription_id = db.Column(
        db.BigInteger,
        db.ForeignKey('subscription.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )

    provider = db.Column(
        db.String(50),
        nullable=False
    )

    provider_payment_id = db.Column(
        db.String(255),
        nullable=True,
        unique=True,
        index=True
    )

    # Internal reference generated by our application.
    payment_reference = db.Column(
        db.String(120),
        nullable=False,
        unique=True,
        index=True
    )

    amount = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

    currency = db.Column(
        db.String(10),
        nullable=False,
        default='USD'
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default='pending',
        index=True
    )
    # pending / succeeded / failed / refunded / canceled

    description = db.Column(
        db.String(255),
        nullable=True
    )

    # Plan bought (see plans.PLANS) and, for manual mobile-money payments,
    # what the payer told us. Reviewed by the Super Admin.
    plan_code = db.Column(db.String(80), nullable=True, index=True)
    payment_method = db.Column(db.String(30), nullable=True)    # orange_money / mtn_momo / card
    payer_phone = db.Column(db.String(30), nullable=True)
    reviewed_by = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True
    )
    reviewed_at = db.Column(db.DateTime, nullable=True)

    failure_reason = db.Column(
        db.String(255),
        nullable=True
    )

    paid_at = db.Column(
        db.DateTime,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    user = db.relationship(
        'User',
        foreign_keys=[user_id],
        backref=db.backref(
            'payments',
            lazy=True
        )
    )

    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

    subscription = db.relationship(
        'Subscription',
        backref=db.backref(
            'payments',
            lazy=True
        )
    )


class Entitlement(db.Model):
    """
    Represents what a user is actually allowed to use.

    This is intentionally separate from Subscription.

    Example:
        athlete_premium_profile
        verified_video
        resume_pdf
        scout_search
        api_access

    A payment does not automatically mean unlimited access.
    The application grants specific entitlements.
    """

    __tablename__ = 'entitlement'

    id = db.Column(db.BigInteger, primary_key=True)

    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    entitlement_code = db.Column(
        db.String(100),
        nullable=False,
        index=True
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default='active',
        index=True
    )
    # active / revoked / expired

    source = db.Column(
        db.String(50),
        nullable=False,
        default='subscription'
    )
    # subscription / purchase / admin / system

    source_reference = db.Column(
        db.String(255),
        nullable=True
    )

    starts_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    expires_at = db.Column(
        db.DateTime,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    user = db.relationship(
        'User',
        backref=db.backref(
            'entitlements',
            lazy=True,
            cascade='all, delete-orphan'
        )
    )

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'entitlement_code',
            name='unique_user_entitlement'
        ),
    )


class AuditLog(db.Model):
    """
    Security/business audit trail.

    This records sensitive monetization and authorization events.
    """

    __tablename__ = 'audit_log'

    id = db.Column(db.BigInteger, primary_key=True)

    actor_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )

    action = db.Column(
        db.String(100),
        nullable=False,
        index=True
    )

    target_type = db.Column(
        db.String(50),
        nullable=True
    )

    target_id = db.Column(
        db.String(100),
        nullable=True
    )

    ip_address = db.Column(
        db.String(64),
        nullable=True
    )

    user_agent = db.Column(
        db.String(500),
        nullable=True
    )

    details = db.Column(
        JSONB,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True
    )

    actor = db.relationship(
        'User',
        backref=db.backref(
            'audit_logs',
            lazy=True
        )
    )
class PremiumProfile(db.Model):
    __tablename__ = 'premium_profile'

    id = db.Column(db.BigInteger, primary_key=True)

    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        unique=True
    )

    bio = db.Column(db.Text, nullable=True)

    is_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    is_public = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    user = db.relationship(
        'User',
        backref=db.backref(
            'premium_profile',
            uselist=False,
            cascade='all, delete-orphan'
        )
    )


class PinnedSportRecord(db.Model):
    __tablename__ = 'pinned_sport_record'

    id = db.Column(
        db.BigInteger,
        primary_key=True
    )

    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False
    )

    sport_record_id = db.Column(
    db.BigInteger,
    db.ForeignKey(
        'sport_record.id',
        ondelete='CASCADE'
    ),
    nullable=False
)

    display_order = db.Column(
        db.Integer,
        nullable=False,
        default=1
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    user = db.relationship(
        'User',
        backref='pinned_sport_records'
    )

    sport_record = db.relationship(
        'SportRecord',
        backref='pinned_by_users'
    )

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'sport_record_id',
            name='uq_pinned_record_user_record'
        ),

        db.UniqueConstraint(
            'user_id',
            'display_order',
            name='uq_pinned_record_user_order'
        ),
    )
