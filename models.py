from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash  # type: ignore[import-not-found]
from competitions import COMPETITION_TEAM_LABELS
from datetime import datetime
import re
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.orm import declared_attr


db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.BigInteger, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    # Unique @handle (lowercase) for athletes, coaches and scouts: tells
    # people with the same full name apart. See usernames.py.
    username = db.Column(db.String(20), nullable=True, unique=True, index=True)
    username_changed_at = db.Column(db.DateTime, nullable=True)

    @property
    def display_name(self):
        """The name shown to people. The platform admin account always shows as
        "D.A.R.T Admin" when its stored name is a role placeholder (e.g. "Super" + "Admin")."""
        if self.role == 'System' and re.fullmatch(r'(?:\s|super|admin|system|root|d\.?a\.?r\.?t\.?)*', self.full_name or '', re.IGNORECASE):
            return 'D.A.R.T Admin'
        return self.full_name

    @property
    def role_label(self):
        return {'System': 'D.A.R.T Admin', 'Statistician': 'Match Official'}.get(self.role, self.role)
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
    # Latest Terms of Use / Privacy Policy version this user accepted
    # (every acceptance is also kept in TermsAcceptance).
    terms_version = db.Column(db.String(20), nullable=True)
    terms_accepted_at = db.Column(db.DateTime, nullable=True)
    # Who may see age, nationality, height, weight and preferred foot:
    # 'everyone', 'members' (signed-in coaches, scouts and organizations)
    # or 'private'. None = not chosen yet = hidden.
    personal_visibility = db.Column(db.String(10), nullable=True)
    # Media safety: rejected uploads add strikes; nudity or too many strikes
    # block photo/video uploads until a D.A.R.T Admin restores them.
    media_strikes = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    media_blocked = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    # Records a coach rejected as FAKE (kept even if the athlete deletes the
    # record). 1st = warning, 2nd = final warning, 3rd = account suspended.
    fake_record_strikes = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    # Record integrity (anti-bribery). Coaches and match officials are tied to
    # a Liberian mobile number (one account per number). A coach whose record
    # approvals are found fraudulent twice loses the right to use D.A.R.T.
    phone = db.Column(db.String(20), nullable=True)
    phone_verified_at = db.Column(db.DateTime, nullable=True)
    fraud_flags = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    license_revoked = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    license_revoked_at = db.Column(db.DateTime, nullable=True)
    # Match officials (role 'Statistician'): journalist, league secretary...
    official_type = db.Column(db.String(40), nullable=True)
    # Athletes under 17: a parent / guardian contact is required, and the
    # athlete confirms a parent, guardian or coach helps manage the page.
    # D.A.R.T Market$: the D.A.R.T Admin can stop an account from selling / messaging.
    market_banned = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    shop_name = db.Column(db.String(80), nullable=True)          # Vendor accounts
    # Vendors: a person (government name) or a business (business name), with the
    # address / location on their business card; the D.A.R.T Admin verifies them.
    vendor_type = db.Column(db.String(10), nullable=True)        # person / business
    business_address = db.Column(db.String(200), nullable=True)
    business_location = db.Column(db.String(80), nullable=True)
    vendor_verified_at = db.Column(db.DateTime, nullable=True)
    # Club League division the athlete plays in (Club League / All Athlete), from registration.
    club_division = db.Column(db.String(20), nullable=True)
    guardian_name = db.Column(db.String(100), nullable=True)
    guardian_relationship = db.Column(db.String(30), nullable=True)
    guardian_phone = db.Column(db.String(20), nullable=True)
    guardian_updated_at = db.Column(db.DateTime, nullable=True)
    minor_notice_ack_at = db.Column(db.DateTime, nullable=True)
    # Two-step approval for NEW athletes (None = account from before this
    # rule: the coach alone verified it). The profile goes live
    # (is_verified) once the coach approved it AND the organization of
    # their team / school approved it - or no such organization is on
    # D.A.R.T. ('not_required').
    coach_verified = db.Column(db.Boolean, nullable=True)
    org_approval = db.Column(db.String(12), nullable=True)      # pending / approved / rejected / not_required
    org_approval_by = db.Column(db.BigInteger, nullable=True)   # the Organization user asked to approve
    org_approval_name = db.Column(db.String(150), nullable=True)
    org_approved_at = db.Column(db.DateTime, nullable=True)
    # A suspended account can't sign in or use D.A.R.T. until the Super
    # Admin lifts it.
    is_suspended = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    suspended_at = db.Column(db.DateTime, nullable=True)
    suspension_reason = db.Column(db.String(200), nullable=True)

    # (No "same name + same team" rule any more: two people can share a full
    # name, even on one team. Their unique username tells them apart.)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class TermsAcceptance(db.Model):
    """Permanent record of each time a user ticked "I agree" to the Terms of
    Use and Privacy Policy (one row per user per terms version)."""
    __tablename__ = 'terms_acceptance'

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    terms_version = db.Column(db.String(20), nullable=False)
    # Role and name at the time of acceptance (kept even if they change later).
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    # Which documents were shown with the checkbox, e.g. "terms,privacy,cookies".
    documents = db.Column(db.String(120), nullable=False)
    accepted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    # Who they allowed to see their personal details when accepting.
    personal_visibility = db.Column(db.String(10), nullable=True)

    user = db.relationship('User', backref=db.backref('terms_acceptances', lazy='dynamic', passive_deletes=True))

    __table_args__ = (db.UniqueConstraint('user_id', 'terms_version', name='uq_terms_acceptance_user_version'),)


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

    # Why a coach rejected it: 'fake' or 'inaccurate' (see RECORD_REJECT_REASONS
    # in app.py). Cleared when the record goes back to pending.
    rejection_reason = db.Column(db.String(20), nullable=True)
    rejection_note = db.Column(db.String(200), nullable=True)
    rejected_by = db.Column(db.BigInteger, nullable=True)     # coach / D.A.R.T Admin user id
    rejected_at = db.Column(db.DateTime, nullable=True)

    # ---------- Integrity: two independent sign-offs ----------
    # status 'pending'   -> waiting for the athlete's coach
    #        'verifying' -> coach approved; waiting for a counter-signature
    #                       (opposing coach, a match official or the D.A.R.T Admin)
    #        'disputed'  -> the other side says it is false; the D.A.R.T Admin decides
    #        'approved'  -> public; 'rejected' -> refused
    coach_approved_by = db.Column(db.BigInteger, nullable=True)
    coach_approved_at = db.Column(db.DateTime, nullable=True)
    countersign_target = db.Column(db.String(16), nullable=True)     # opposing_coach / official
    countersigned_by = db.Column(db.BigInteger, nullable=True)
    countersigned_at = db.Column(db.DateTime, nullable=True)
    countersign_role = db.Column(db.String(16), nullable=True)       # opposing_coach / official / admin
    dispute_reason = db.Column(db.String(300), nullable=True)
    disputed_by = db.Column(db.BigInteger, nullable=True)
    disputed_at = db.Column(db.DateTime, nullable=True)
    # Numbers far above the athlete's usual (or extreme for one game): held
    # until a match sheet or a highlight video is attached as proof.
    anomaly_flags = db.Column(db.String(300), nullable=True)
    proof_required = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    proof_path = db.Column(db.String(255), nullable=True)
    proof_highlight_id = db.Column(db.BigInteger, nullable=True)
    proof_uploaded_at = db.Column(db.DateTime, nullable=True)

    @property
    def needs_proof(self):
        return bool(self.proof_required and not (self.proof_path or self.proof_highlight_id))

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

    # Content safety. Only 'approved' highlights are ever shown to others.
    # 'pending' files sit in the private 'highlight-review' bucket until a
    # D.A.R.T Admin approves them (then they move to the public bucket);
    # 'rejected' rows keep only the reason (the file is deleted).
    sport = db.Column(db.String(20), nullable=True)          # Football / Basketball / Kickball
    status = db.Column(db.String(12), nullable=False, default='pending', server_default='pending', index=True)
    storage_bucket = db.Column(db.String(40), nullable=False, default='highlight-review', server_default='athlete-highlights')
    ai_checked = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    ai_summary = db.Column(db.String(300), nullable=True)
    ai_flags = db.Column(db.String(120), nullable=True)
    review_reason = db.Column(db.String(300), nullable=True)
    reviewed_by = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    # Two approvals before a new upload goes live (feed + profile):
    # the D.A.R.T Admin (or the AI check, if auto-approve is on) and the
    # coach of the athlete's team - when that team has a coach on D.A.R.T.
    # coach_approval: pending / approved / not_required (None = older upload).
    team_name = db.Column(db.String(150), nullable=True)
    # Several photos / videos posted together share a post_key (swipe
    # through them in one feed post); post_position is their order.
    post_key = db.Column(db.String(32), nullable=True, index=True)
    post_position = db.Column(db.SmallInteger, nullable=False, default=0, server_default='0')
    admin_approved_at = db.Column(db.DateTime, nullable=True)
    coach_approval = db.Column(db.String(12), nullable=True)
    coach_reviewed_by = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    coach_reviewed_at = db.Column(db.DateTime, nullable=True)
    published_at = db.Column(db.DateTime, nullable=True, index=True)

    # Highlights feed counters (kept in step with the rows below).
    like_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    repost_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    comment_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    save_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    share_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')

    user = db.relationship('User', foreign_keys=[user_id])

    @property
    def is_approved(self):
        return self.status == 'approved'

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


class _HighlightReaction:
    """
    One row per (highlight, person): a like, a repost or a saved post.
    The person is a signed-in member (user_id) OR an anonymous visitor
    (visitor_key: a random id kept in a long-lived cookie). ip_hash is a
    one-way hash of the visitor's IP, only used to slow down spam.
    """
    id = db.Column(db.BigInteger, primary_key=True)
    visitor_key = db.Column(db.String(64), nullable=True)
    ip_hash = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    @declared_attr
    def highlight_id(cls):
        return db.Column(db.BigInteger, db.ForeignKey('athlete_highlight.id', ondelete='CASCADE'), nullable=False, index=True)

    @declared_attr
    def user_id(cls):
        return db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=True, index=True)

    @declared_attr
    def __table_args__(cls):
        return (
            db.UniqueConstraint('highlight_id', 'user_id', name=f'uq_{cls.__tablename__}'),
            db.UniqueConstraint('highlight_id', 'visitor_key', name=f'uq_{cls.__tablename__}_visitor'),
            db.Index(f'ix_{cls.__tablename__}_visitor_key', 'visitor_key'),
        )

    @property
    def is_member(self):
        return self.user_id is not None


class HighlightLike(_HighlightReaction, db.Model):
    __tablename__ = 'highlight_like'


class HighlightRepost(_HighlightReaction, db.Model):
    __tablename__ = 'highlight_repost'


class HighlightSave(_HighlightReaction, db.Model):
    """A post the person bookmarked (only they see their saved posts)."""
    __tablename__ = 'highlight_save'


class HighlightShare(db.Model):
    """One row each time someone shares a post (counted once per person)."""
    __tablename__ = 'highlight_share'

    id = db.Column(db.BigInteger, primary_key=True)
    highlight_id = db.Column(db.BigInteger, db.ForeignKey('athlete_highlight.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=True, index=True)
    visitor_key = db.Column(db.String(64), nullable=True, index=True)
    ip_hash = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class HighlightComment(db.Model):
    """A comment by a member (user_id) or a visitor (visitor_key + the name they typed)."""
    __tablename__ = 'highlight_comment'

    id = db.Column(db.BigInteger, primary_key=True)
    highlight_id = db.Column(db.BigInteger, db.ForeignKey('athlete_highlight.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=True, index=True)
    visitor_key = db.Column(db.String(64), nullable=True, index=True)
    visitor_name = db.Column(db.String(40), nullable=True)
    ip_hash = db.Column(db.String(32), nullable=True)
    body = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    edited_at = db.Column(db.DateTime, nullable=True)   # set when the writer edits it ("edited")

    user = db.relationship('User', foreign_keys=[user_id])

    @property
    def is_member(self):
        return self.user_id is not None


class OrganizationProfile(db.Model):
    """
    An Enterprise / Institution / Academy / Club account (User.role =
    'Organization'). Its roster lists every verified athlete whose team
    name matches the organization's name or one of its approved aliases.
    Nothing is shown until the D.A.R.T Admin verifies the organization.
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
    # aliases are used for the roster; changes wait for the D.A.R.T Admin.
    approved_aliases = db.Column(db.Text, nullable=True)
    requested_aliases = db.Column(db.Text, nullable=True)
    verification_status = db.Column(db.String(20), nullable=False, default='pending')  # pending / verified / rejected
    # Team badge / logo (the organization's only picture). Uploads wait for
    # the D.A.R.T Admin; the approved logo shows next to the team's name everywhere.
    logo_path = db.Column(db.String(255), nullable=True)            # approved (public)
    logo_pending_path = db.Column(db.String(255), nullable=True)    # waiting for review (private)
    logo_status = db.Column(db.String(12), nullable=True)           # pending / approved / rejected
    logo_note = db.Column(db.String(200), nullable=True)
    logo_reviewed_at = db.Column(db.DateTime, nullable=True)
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
    # what the payer told us. Reviewed by the D.A.R.T Admin.
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


class AwardContest(db.Model):
    """
    "Goal of the Week", "Highlight of the Month"... run by the D.A.R.T Admin.
    Entries are open until entry_deadline, voting until voting_ends_at; the
    entry with the most votes wins (decided once voting has closed).
    """
    __tablename__ = 'award_contest'

    id = db.Column(db.BigInteger, primary_key=True)
    award = db.Column(db.String(30), nullable=False)          # highlight / goal / assist / save / skill ...
    period = db.Column(db.String(10), nullable=False)         # week / month
    sport = db.Column(db.String(20), nullable=True)           # None = any sport
    title = db.Column(db.String(120), nullable=False)
    opens_at = db.Column(db.DateTime, nullable=False)
    entry_deadline = db.Column(db.DateTime, nullable=False)
    voting_ends_at = db.Column(db.DateTime, nullable=False)
    winner_entry_id = db.Column(db.BigInteger, nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.BigInteger, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    entries = db.relationship('AwardEntry', backref='contest', cascade='all, delete-orphan', lazy='selectin')


class AwardEntry(db.Model):
    """An athlete's approved highlight entered in a contest (the D.A.R.T Admin approves the entry)."""
    __tablename__ = 'award_entry'

    id = db.Column(db.BigInteger, primary_key=True)
    contest_id = db.Column(db.BigInteger, db.ForeignKey('award_contest.id', ondelete='CASCADE'), nullable=False, index=True)
    athlete_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    highlight_id = db.Column(db.BigInteger, db.ForeignKey('athlete_highlight.id', ondelete='CASCADE'), nullable=False)
    note = db.Column(db.String(150), nullable=True)
    status = db.Column(db.String(12), nullable=False, default='pending')   # pending / approved / rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    athlete = db.relationship('User', foreign_keys=[athlete_id])
    highlight = db.relationship('AthleteHighlight', foreign_keys=[highlight_id])

    __table_args__ = (db.UniqueConstraint('contest_id', 'athlete_id', name='uq_award_entry_contest_athlete'),)


class AwardVote(db.Model):
    """
    ONE final vote per contest, by a signed-in member (voter_id) or by a
    visitor (visitor_key: the anonymous 'dart_vid' cookie; ip_hash: one-way
    hash of their IP, to limit cookie-clearing abuse).
    """
    __tablename__ = 'award_vote'

    id = db.Column(db.BigInteger, primary_key=True)
    contest_id = db.Column(db.BigInteger, db.ForeignKey('award_contest.id', ondelete='CASCADE'), nullable=False, index=True)
    entry_id = db.Column(db.BigInteger, db.ForeignKey('award_entry.id', ondelete='CASCADE'), nullable=False, index=True)
    voter_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=True)
    visitor_key = db.Column(db.String(32), nullable=True)
    ip_hash = db.Column(db.String(32), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('contest_id', 'voter_id', name='uq_award_vote_contest_voter'),
        db.UniqueConstraint('contest_id', 'visitor_key', name='uq_award_vote_contest_visitor'),
    )


# ==========================================================
# D.A.R.T MARKET$ (sports gear marketplace). D.A.R.T never handles the
# money: buyers and sellers chat and call each other and pay off D.A.R.T.
# Every listing is checked by the D.A.R.T Admin before it goes live.
# ==========================================================
class MarketListing(db.Model):
    __tablename__ = 'market_listing'

    id = db.Column(db.BigInteger, primary_key=True)
    seller_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(30), nullable=False, index=True)
    sport = db.Column(db.String(20), nullable=False, index=True)        # Football / Basketball / Kickball / Multi-sport
    brand = db.Column(db.String(40), nullable=True)
    condition = db.Column(db.String(10), nullable=False, default='new')  # new / used
    price = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default='USD')    # USD / LRD
    sizes = db.Column(db.String(120), nullable=True)                     # "40, 41, 42" / "S, M, L"
    colors = db.Column(db.String(120), nullable=True)                    # "black, red"
    description = db.Column(db.String(1500), nullable=False)
    specs = db.Column(db.String(1000), nullable=True)
    location = db.Column(db.String(80), nullable=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    # pending -> approved / rejected; approved -> sold / removed (by the seller or the D.A.R.T Admin)
    status = db.Column(db.String(12), nullable=False, default='pending', index=True)
    review_note = db.Column(db.String(200), nullable=True)
    reviewed_by = db.Column(db.BigInteger, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    views = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    seller = db.relationship('User', foreign_keys=[seller_id])
    images = db.relationship('MarketImage', backref='listing', cascade='all, delete-orphan', lazy='selectin',
                             order_by='MarketImage.position')

    @property
    def cover(self):
        return self.images[0] if self.images else None

    @property
    def size_list(self):
        return [x.strip() for x in (self.sizes or '').split(',') if x.strip()]

    @property
    def color_list(self):
        return [x.strip() for x in (self.colors or '').split(',') if x.strip()]


class MarketImage(db.Model):
    """A product photo (cleaned: re-encoded, no EXIF / GPS). Private until the listing is approved."""
    __tablename__ = 'market_image'

    id = db.Column(db.BigInteger, primary_key=True)
    listing_id = db.Column(db.BigInteger, db.ForeignKey('market_listing.id', ondelete='CASCADE'), nullable=False, index=True)
    storage_path = db.Column(db.String(255), nullable=False)
    bucket = db.Column(db.String(40), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)


class MarketThread(db.Model):
    """A buyer's conversation with a seller about one listing."""
    __tablename__ = 'market_thread'

    id = db.Column(db.BigInteger, primary_key=True)
    listing_id = db.Column(db.BigInteger, db.ForeignKey('market_listing.id', ondelete='CASCADE'), nullable=False, index=True)
    buyer_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    seller_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_message_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    listing = db.relationship('MarketListing')
    buyer = db.relationship('User', foreign_keys=[buyer_id])
    seller = db.relationship('User', foreign_keys=[seller_id])
    messages = db.relationship('MarketMessage', backref='thread', cascade='all, delete-orphan', lazy='selectin',
                               order_by='MarketMessage.id')

    __table_args__ = (db.UniqueConstraint('listing_id', 'buyer_id', name='uq_market_thread_listing_buyer'),)


class MarketMessage(db.Model):
    __tablename__ = 'market_message'

    id = db.Column(db.BigInteger, primary_key=True)
    thread_id = db.Column(db.BigInteger, db.ForeignKey('market_thread.id', ondelete='CASCADE'), nullable=False, index=True)
    sender_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    body = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    read_at = db.Column(db.DateTime, nullable=True)


class MarketCartItem(db.Model):
    """
    A product a buyer saved in their D.A.R.T Market$ cart: a list to discuss
    with the seller in the app; the purchase itself happens off D.A.R.T.
    """
    __tablename__ = 'market_cart_item'

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    listing_id = db.Column(db.BigInteger, db.ForeignKey('market_listing.id', ondelete='CASCADE'), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    size = db.Column(db.String(15), nullable=True)
    color = db.Column(db.String(15), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # Order tracker: cart -> requested (list sent to the vendor) -> sold (left
    # the vendor's shop, marked by the vendor) -> received (confirmed by the
    # buyer only). cancelled = the vendor did not sell it.
    status = db.Column(db.String(12), nullable=False, default='cart', server_default='cart')
    requested_at = db.Column(db.DateTime, nullable=True)
    sold_at = db.Column(db.DateTime, nullable=True)
    received_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)

    listing = db.relationship('MarketListing')
    buyer = db.relationship('User', foreign_keys=[user_id])

    __table_args__ = (db.UniqueConstraint('user_id', 'listing_id', name='uq_market_cart_user_listing'),)


class MarketReport(db.Model):
    """A user reports a listing (scam, not sports gear, fake...)."""
    __tablename__ = 'market_report'

    id = db.Column(db.BigInteger, primary_key=True)
    listing_id = db.Column(db.BigInteger, db.ForeignKey('market_listing.id', ondelete='CASCADE'), nullable=False, index=True)
    reporter_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    reason = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(12), nullable=False, default='open')     # open / resolved
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    listing = db.relationship('MarketListing')
    reporter = db.relationship('User', foreign_keys=[reporter_id])

    __table_args__ = (db.UniqueConstraint('listing_id', 'reporter_id', name='uq_market_report_listing_reporter'),)


# ==========================================================
# D.A.R.T PREDICTIONS: a FREE game (points, no money) on adult (18+)
# premium athletes' next-game stats. Athletes can't take part.
# ==========================================================
class PredictionQuestion(db.Model):
    __tablename__ = 'prediction_question'

    id = db.Column(db.BigInteger, primary_key=True)
    athlete_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    sport = db.Column(db.String(20), nullable=False)
    stat = db.Column(db.String(20), nullable=False)        # goals / assists / saves / points / rebounds / home_runs
    line = db.Column(db.Integer, nullable=False)           # "at least <line>"
    match_date = db.Column(db.Date, nullable=False)
    match_label = db.Column(db.String(120), nullable=True)
    closes_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(10), nullable=False, default='open')   # open / settled / void
    result_value = db.Column(db.Integer, nullable=True)
    settled_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.BigInteger, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    athlete = db.relationship('User', foreign_keys=[athlete_id])
    picks = db.relationship('PredictionPick', backref='question', cascade='all, delete-orphan', lazy='selectin')


class PredictionPick(db.Model):
    __tablename__ = 'prediction_pick'

    id = db.Column(db.BigInteger, primary_key=True)
    question_id = db.Column(db.BigInteger, db.ForeignKey('prediction_question.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    pick = db.Column(db.String(5), nullable=False)          # yes / no  ("will reach the line?")
    points = db.Column(db.Integer, nullable=True)           # set when settled
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship('User', foreign_keys=[user_id])

    __table_args__ = (db.UniqueConstraint('question_id', 'user_id', name='uq_prediction_pick_question_user'),)


class Notification(db.Model):
    """
    In-app notification (the bell). Only good news about the user's own
    account: profile / record approved (athletes), account approved
    (coaches, scouts, organizations). Created automatically (notifications.py).
    """
    __tablename__ = 'notification'

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    kind = db.Column(db.String(30), nullable=False)
    message = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=True)


class RecordVerificationEvent(db.Model):
    """
    Append-only history of every step a sports record goes through (submit,
    approve, counter-sign, dispute, edit, decision). Each row stores a hash
    of the record's numbers and is chained to the previous row of the same
    record (prev_hash -> event_hash), so any later change is detectable. The
    database refuses UPDATE and DELETE on this table (see the migration).
    record_id is not a foreign key on purpose: the history outlives the record.
    """
    __tablename__ = 'record_verification_event'

    id = db.Column(db.BigInteger, primary_key=True)
    record_id = db.Column(db.BigInteger, nullable=False, index=True)
    actor_id = db.Column(db.BigInteger, nullable=True, index=True)
    actor_role = db.Column(db.String(20), nullable=True)
    action = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.String(400), nullable=True)
    stats_hash = db.Column(db.String(64), nullable=False)
    prev_hash = db.Column(db.String(64), nullable=True)
    event_hash = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class MatchReport(db.Model):
    """A match official's own record of a game (who scored what)."""
    __tablename__ = 'match_report'

    id = db.Column(db.BigInteger, primary_key=True)
    official_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    sport = db.Column(db.String(20), nullable=False)
    match_date = db.Column(db.Date, nullable=False, index=True)
    home_team = db.Column(db.String(150), nullable=False)
    away_team = db.Column(db.String(150), nullable=False)
    competition = db.Column(db.String(60), nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    official = db.relationship('User', foreign_keys=[official_id])
    lines = db.relationship('MatchReportLine', backref='report', cascade='all, delete-orphan', lazy='selectin')


class MatchReportLine(db.Model):
    """One athlete's numbers in a match report."""
    __tablename__ = 'match_report_line'

    id = db.Column(db.BigInteger, primary_key=True)
    report_id = db.Column(db.BigInteger, db.ForeignKey('match_report.id', ondelete='CASCADE'), nullable=False, index=True)
    athlete_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    goals = db.Column(db.Integer, nullable=False, default=0)
    assists = db.Column(db.Integer, nullable=False, default=0)
    points = db.Column(db.Integer, nullable=False, default=0)
    rebounds = db.Column(db.Integer, nullable=False, default=0)
    blocks = db.Column(db.Integer, nullable=False, default=0)
    home_runs = db.Column(db.Integer, nullable=False, default=0)

    athlete = db.relationship('User', foreign_keys=[athlete_id])


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

    # bigint in the database (Supabase): always an id number.
    target_id = db.Column(
        db.BigInteger,
        nullable=True
    )

    # inet in the database (Supabase); plain text on other databases (tests).
    ip_address = db.Column(
        db.String(64).with_variant(INET(), 'postgresql'),
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


# ==========================================================
# COACH CAREER (Coach Pro)
# ==========================================================
class CoachTeam(db.Model):
    """
    A team a coach coaches (or coached). The coach's registered team
    (User.school) becomes the first, primary row. Coach Pro coaches can add
    more (e.g. a high-school team and a club at the same time); every new
    team waits for the D.A.R.T Admin before the coach may approve its
    athletes' records. Leaving a team keeps its history.
    """
    __tablename__ = 'coach_team'

    id = db.Column(db.BigInteger, primary_key=True)
    coach_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    team_name = db.Column(db.String(150), nullable=False)
    coach_category = db.Column(db.String(50), nullable=False)       # which competitions, for this team
    role = db.Column(db.String(30), nullable=False, default='Head Coach')
    # pending -> approved / rejected; approved -> left (history)
    status = db.Column(db.String(12), nullable=False, default='pending', index=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    review_note = db.Column(db.String(200), nullable=True)
    reviewed_by = db.Column(db.BigInteger, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    coach = db.relationship('User', foreign_keys=[coach_id])

    @property
    def is_active(self):
        return self.status == 'approved' and self.end_date is None


class CoachGameRecord(db.Model):
    """One game a coach led: result, score, formation (football), subs, cards."""
    __tablename__ = 'coach_game_record'

    id = db.Column(db.BigInteger, primary_key=True)
    coach_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    team_name = db.Column(db.String(150), nullable=False)     # kept even after the coach leaves
    sport = db.Column(db.String(20), nullable=False)
    competition = db.Column(db.String(60), nullable=True)
    game_date = db.Column(db.Date, nullable=False)
    season = db.Column(db.Integer, nullable=False)
    opponent = db.Column(db.String(150), nullable=False)
    result = db.Column(db.String(5), nullable=False)          # win / draw / loss
    score_for = db.Column(db.Integer, nullable=True)
    score_against = db.Column(db.Integer, nullable=True)
    formation = db.Column(db.String(12), nullable=True)       # football only, e.g. 4-3-3
    substitutions = db.Column(db.Integer, nullable=False, default=0)
    yellow_cards = db.Column(db.Integer, nullable=False, default=0)   # cards the coach received
    red_cards = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.String(200), nullable=True)
    # pending -> approved / rejected (D.A.R.T Admin). Only approved show publicly.
    status = db.Column(db.String(12), nullable=False, default='pending', index=True)
    review_note = db.Column(db.String(200), nullable=True)
    reviewed_by = db.Column(db.BigInteger, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    coach = db.relationship('User', foreign_keys=[coach_id])


class CoachAchievement(db.Model):
    """A trophy (league / cup / title) or award (e.g. Best Coach) a coach won."""
    __tablename__ = 'coach_achievement'

    id = db.Column(db.BigInteger, primary_key=True)
    coach_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)            # trophy / award
    title = db.Column(db.String(150), nullable=False)
    competition = db.Column(db.String(80), nullable=True)
    team_name = db.Column(db.String(150), nullable=True)
    season = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(12), nullable=False, default='pending', index=True)
    review_note = db.Column(db.String(200), nullable=True)
    reviewed_by = db.Column(db.BigInteger, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    coach = db.relationship('User', foreign_keys=[coach_id])
