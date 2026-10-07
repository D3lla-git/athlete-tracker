import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, send_from_directory, session, abort, make_response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, user_logged_in
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.utils import secure_filename
from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from models import (db,User,SportRecord,LoginAttempt,Registration,ChatMessage,Subscription,Payment,Entitlement,AuditLog,PremiumProfile,PinnedSportRecord, ScoutProfile, SavedSearch, AthleteHighlight, OrganizationProfile, OrganizationRosterExclusion)
from dotenv import load_dotenv
load_dotenv(override=True)  # This forces Python to read your local .env file
from config import Config
from itsdangerous import URLSafeSerializer, BadSignature
from record_pdf import build_record_pdf, build_profile_pdf
from profile_card import CardData, render_card as render_profile_card
from plans import (
    PLANS, PAYMENT_METHODS, MOMO_METHODS, FREE_ATHLETE_LIMITS, FREE_LIMITS_START,
    FREE_ORG_ROSTER_PREVIEW, ATHLETE_PLAN_BY_CATEGORY, active_subscription,
    current_plan, is_premium, plan_for_user, eligibility_error,
    can_use_advanced_search, shows_ads, scout_has_full_dashboard,
    limit_reached, usage_summary, apply_record_visibility, activate_plan,
    clear_plan_cache,
)
from competitions import (
    ALL_COMPETITIONS,
    ATHLETE_CATEGORIES,
    COACH_CATEGORIES,
    clean_age_group,
    clean_competition_team,
    competition_allowed,
    competition_team_options,
    competitions_for,
    form_rules,
    legacy_trophy_options,
    validate_trophy,
)
from datetime import datetime, timedelta
from urllib.parse import urlsplit, parse_qsl, urlencode
from flask_migrate import Migrate
from supabase import create_client
import csv
import io
import re
import requests as http_requests
import uuid
import pycountry
import base64
from io import BytesIO
import pyotp
import qrcode
import secrets
from flask_mail import Mail, Message
from sqlalchemy import or_, func, select
from authlib.integrations.flask_client import OAuth
import time
from flask import abort


# ==========================================
# RECOVERY CODE HELPERS
# ==========================================

def generate_recovery_codes(count=10):
    """Generate one-time recovery codes."""
    codes = []

    for _ in range(count):
        code = pyotp.random_base32()[:10].upper()
        formatted_code = f"{code[:5]}-{code[5:]}"
        codes.append(formatted_code)

    return codes


def hash_recovery_codes(codes):
    """Hash recovery codes before storing them."""
    return [
        generate_password_hash(code)
        for code in codes
    ]


def verify_recovery_code(code, stored_hashes):
    """Check a recovery code against stored hashes.

    Returns the index of the matching hash, or None.
    """
    normalized_code = code.strip().upper()

    for index, stored_hash in enumerate(stored_hashes):
        if check_password_hash(stored_hash, normalized_code):
            return index

    return None

# ============================================================
# MONETIZATION SECURITY HELPERS
# ============================================================

from datetime import datetime, timezone


def has_entitlement(
    user_id,
    entitlement_code
):
    """
    Check whether a user currently has an active entitlement.

    This is a SERVER-SIDE check.
    Never trust a browser/HTML field to determine entitlement.
    """

    now = datetime.now(timezone.utc)

    entitlement = Entitlement.query.filter(
        Entitlement.user_id == user_id,
        Entitlement.entitlement_code == entitlement_code,
        Entitlement.status == 'active',
        db.or_(
            Entitlement.expires_at.is_(None),
            Entitlement.expires_at > now
        ),
        Entitlement.starts_at <= now
    ).first()

    return entitlement is not None


def get_entitlement(
    user_id,
    entitlement_code
):
    """
    Return the active entitlement object, if one exists.
    """

    now = datetime.now(timezone.utc)

    return Entitlement.query.filter(
        Entitlement.user_id == user_id,
        Entitlement.entitlement_code == entitlement_code,
        Entitlement.status == 'active',
        db.or_(
            Entitlement.expires_at.is_(None),
            Entitlement.expires_at > now
        ),
        Entitlement.starts_at <= now
    ).first()


def grant_entitlement(
    user_id,
    entitlement_code,
    source,
    source_reference=None,
    expires_at=None
):
    """
    Create or update an entitlement.

    IMPORTANT:
    This function should only be called by trusted server-side
    operations after the required authorization/payment/approval
    checks have succeeded.
    """

    entitlement = Entitlement.query.filter_by(
        user_id=user_id,
        entitlement_code=entitlement_code
    ).first()

    if entitlement:
        entitlement.status = 'active'
        entitlement.source = source
        entitlement.source_reference = source_reference
        entitlement.expires_at = expires_at

        if entitlement.starts_at is None:
            entitlement.starts_at = datetime.now(timezone.utc)

    else:
        entitlement = Entitlement(
            user_id=user_id,
            entitlement_code=entitlement_code,
            status='active',
            source=source,
            source_reference=source_reference,
            starts_at=datetime.now(timezone.utc),
            expires_at=expires_at
        )

        db.session.add(entitlement)

    db.session.flush()

    return entitlement


def revoke_entitlement(
    user_id,
    entitlement_code
):
    """
    Revoke an existing entitlement.
    """

    entitlement = Entitlement.query.filter_by(
        user_id=user_id,
        entitlement_code=entitlement_code
    ).first()

    if not entitlement:
        return False

    entitlement.status = 'revoked'

    db.session.flush()

    return True


def create_audit_log(
    action,
    actor_user_id=None,
    target_type=None,
    target_id=None,
    details=None
):
    """
    Create a security/business audit event.
    """

    audit = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=request.remote_addr,
        user_agent=request.headers.get(
            'User-Agent',
            ''
        )[:1000],
        details=details
    )

    db.session.add(audit)

    return audit

def has_premium_profile_access(user_id):
    return has_entitlement(
        user_id,
        'premium_profile'
    )


def get_or_create_premium_profile(user_id):
    premium_profile = PremiumProfile.query.filter_by(
        user_id=user_id
    ).first()

    if not premium_profile:
        premium_profile = PremiumProfile(
            user_id=user_id,
            bio=None,
            is_enabled=False,
            is_public=True
        )

        db.session.add(premium_profile)
        db.session.flush()

    return premium_profile

# Static files live in public/static so Vercel serves them straight from its
# CDN (Vercel serves public/** and does not use Flask's static folder).
# They keep the same /static/... URLs, and Flask still serves them locally.
app = Flask(
    __name__,
    static_folder='public/static',
    static_url_path='/static'
)
app.config.from_object(Config)

# ========== DATABASE CONNECTION POOL ==========
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 1800,
}

mail = Mail(app)
# ========== BRUTE-FORCE PROTECTION ==========
MAX_LOGIN_ATTEMPTS = 3
LOGIN_BLOCK_MINUTES = 10
MAX_2FA_ATTEMPTS = 3
TWO_FA_BLOCK_MINUTES = 10
# Forgot-password rate limiting
MAX_FORGOT_ATTEMPTS = 5
FORGOT_BLOCK_MINUTES = 10

MAX_FORGOT_EMAIL_ATTEMPTS = 3
FORGOT_EMAIL_BLOCK_MINUTES = 60
# ========== CSRFSECURITY ==========
csrf = CSRFProtect(app)

# ========== SECURITY HEADERS ==========
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = (
        'camera=(), microphone=(), geolocation=()'
    )
    # Force HTTPS for a year. Browsers ignore this header on plain-HTTP
    # localhost, so it is safe during local development.
    response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response
# ========== SUPABASE STORAGE ==========
supabase = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICE_KEY")
)

# ==========================================================
# FILE UPLOAD CONFIGURATION
# ==========================================================
# Persistent athlete files are stored in Supabase Storage.
# /tmp is writable for temporary files during a Vercel request.
app.config['UPLOAD_FOLDER'] = '/tmp/uploads'
os.makedirs(
    app.config['UPLOAD_FOLDER'],
    exist_ok=True
)

db.init_app(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ========== FILE UPLOAD Security HELPERS ==========
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def valid_file_content(file):
    """Validate the actual file signature, not just the filename extension."""
    file.stream.seek(0)
    header = file.stream.read(16)
    file.stream.seek(0)

    # PNG
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'

    # JPEG
    if header.startswith(b'\xff\xd8\xff'):
        return 'jpg'

    # PDF
    if header.startswith(b'%PDF'):
        return 'pdf'

    return None

# ========== PASSWORD STRENGTH CHECK ==========
def is_strong_password(password):
    """Check whether a password meets the application's security requirements."""
    if len(password) < 8:
        return False

    if not any(char.isupper() for char in password):
        return False

    if not any(char.islower() for char in password):
        return False

    if not any(char.isdigit() for char in password):
        return False

    if not any(not char.isalnum() for char in password):
        return False

    return True

# ========== CATEGORY PERMISSION HELPERS ==========

# Competitions are defined per sport in competitions.py (LFA / LBA / LKF).
VALID_COMPETITIONS = set(ALL_COMPETITIONS)
# ==========================================================
# SPORT-SPECIFIC POSITIONS
# ==========================================================

VALID_POSITIONS = {
    'Football': {
        'GK',
        'CB',
        'LB',
        'LWB',
        'RWB',
        'RB',
        'DM',
        'CM',
        'AM',
        'LW',
        'RW',
        'SS',
        'CF'
    },

    'Basketball': {
        'Point Guard',
        'Shooting guard',
        'small forward',
        'power forward',
        'center'
    },

    'Kickball': {
        'Pitcher',
        'Catcher',
        '1st baseman',
        '2nd baseman',
        '3rd baseman',
        'Shortstop',
        'Left fielder',
        'Right fielder',
        'center fielder',
        'short fielder/Rover'
    }
}
# ==========================================================
# PREFERRED FOOT
# ==========================================================

VALID_PREFERRED_FEET = {
    'Right',
    'Left',
    'Both'
}
def athlete_can_submit_competition(category, competition):
    """Check whether an athlete category allows a competition."""
    if competition not in VALID_COMPETITIONS:
        return False

    if category == 'All Athlete':
        return True

    category_map = {
        'County Meet Athlete': 'County Meet',
        'Club League Athlete': 'Club League',
        'University Athlete': 'University League',
        'Community/Area League Athlete': 'Community/Area League',
        'High School Athlete': 'High School'
    }

    return category_map.get(category) == competition


def athlete_allowed_competitions(user, year=None):
    """
    Competitions this athlete may submit records for this season, in form
    order: every competition any of their active registrations allows.
    The record form only offers these (the server checks again).
    """
    year = year or datetime.now().year
    categories = [
        registration.category
        for registration in Registration.query.filter_by(
            user_id=user.id,
            registration_type='athlete',
            registration_year=year,
            status='active'
        )
    ]
    return [
        competition for competition in ALL_COMPETITIONS
        if any(athlete_can_submit_competition(c, competition) for c in categories)
    ]


def coach_can_manage_competition(category, competition):
    """Check whether a coach category allows a competition."""
    if competition not in VALID_COMPETITIONS:
        return False

    if category == 'All Coach':
        return True

    category_map = {
        'County Meet Coach': 'County Meet',
        'Club League Coach': 'Club League',
        'University Coach': 'University League',
        'Community/Area League Coach': 'Community/Area League',
        'High School Coach': 'High School'
    }

    return category_map.get(category) == competition

# ========== BRUTE-FORCE PROTECTION HELPERS ==========

# ========== CLIENT IP HELPER ==========
def get_client_ip():
    """Get the client's real IP address behind Vercel/proxies."""
    real_ip = request.headers.get('X-Real-IP')

    if real_ip:
        return real_ip.strip()

    forwarded_for = request.headers.get('X-Forwarded-For')

    if forwarded_for:
        return forwarded_for.split(',')[0].strip()

    return request.remote_addr or 'unknown'

def is_rate_limited(identifier, max_attempts, block_minutes):
    """Return True if this identifier is currently blocked."""
    attempt = LoginAttempt.query.filter_by(
        identifier=identifier
    ).first()

    if not attempt:
        return False

    if attempt.blocked_until:
        if datetime.utcnow() < attempt.blocked_until.replace(tzinfo=None):
            return True

        # Block has expired — reset the counter
        attempt.failed_attempts = 0
        attempt.blocked_until = None
        db.session.commit()

    return False


def record_failed_attempt(identifier, max_attempts, block_minutes):
    """Record a failed attempt and apply a temporary block if needed."""
    attempt = LoginAttempt.query.filter_by(
        identifier=identifier
    ).first()

    if not attempt:
        attempt = LoginAttempt(
            identifier=identifier,
            failed_attempts=0
        )
        db.session.add(attempt)

    attempt.failed_attempts += 1
    attempt.last_attempt = datetime.utcnow()

    if attempt.failed_attempts >= max_attempts:
        from datetime import timedelta

        attempt.blocked_until = (
            datetime.utcnow() +
            timedelta(minutes=block_minutes)
        )

    db.session.commit()


def reset_failed_attempts(identifier):
    """Clear failed attempts after a successful authentication."""
    attempt = LoginAttempt.query.filter_by(
        identifier=identifier
    ).first()

    if attempt:
        db.session.delete(attempt)
        db.session.commit()

def upload_to_supabase(file, bucket, path):
    file.stream.seek(0)
    file_data = file.read()

    supabase.storage.from_(bucket).upload(
        path,
        file_data,
        file_options={
            "content-type": file.mimetype or "application/octet-stream"
        }
    )

    return path

# ==========================================================
# CHAT PERMISSIONS---Helper
# ==========================================================

# ==========================================================
# CHAT PERMISSIONS
# ==========================================================

def can_message(sender, recipient):
    """
    Determine whether two registered users are allowed
    to exchange messages.

    Allowed:
        Coach  <-> Athlete
        Coach  <-> Scout
        Athlete <-> Scout
        Athlete <-> Coach
        Scout  <-> Athlete
        Scout  <-> Coach
    """

    if not sender.is_authenticated:
        return False

    # Never allow messaging yourself
    if sender.id == recipient.id:
        return False

    allowed_roles = {'Coach', 'Athlete', 'Scout'}

    # Both users must be messaging-enabled roles
    if sender.role not in allowed_roles:
        return False

    if recipient.role not in allowed_roles:
        return False

    # All three roles can communicate with each other
    return True

  #=======Common nationalities=========
def country_flag(nationality):
    if not nationality:
        return '🌍'

    nationality = nationality.strip()

    # Common nationality/demonym aliases
    aliases = {
        'Liberian': 'LR',
        'Liberia': 'LR',
        'American': 'US',
        'United States': 'US',
        'British': 'GB',
        'United Kingdom': 'GB',
        'Ghanaian': 'GH',
        'Ghana': 'GH',
        'Nigerian': 'NG',
        'Nigeria': 'NG',
        'Sierra Leonean': 'SL',
        'Sierra Leone': 'SL',
        'Ivorian': 'CI',
        'Ivory Coast': 'CI',
        'Guinean': 'GN',
        'Guinea': 'GN',
        'Senegalese': 'SN',
        'Senegal': 'SN',
        'Cameroonian': 'CM',
        'Cameroon': 'CM',
        'South African': 'ZA',
        'South Africa': 'ZA',
        'Togolese': 'TG',
        'Togo': 'TG',
        'Beninese': 'BJ',
        'Benin': 'BJ',
        'Burkinabe': 'BF',
        'Burkina Faso': 'BF',
        'Ivorian': 'CI',
        'France': 'FR',
        'French': 'FR',
        'Germany': 'DE',
        'German': 'DE',
        'Italy': 'IT',
        'Italian': 'IT',
        'Canada': 'CA',
        'Canadian': 'CA'
    }

    code = aliases.get(nationality)

    if not code:
        try:
            code = pycountry.countries.lookup(nationality).alpha_2
        except LookupError:
            return '🌍'

    code = code.upper()

    return ''.join(
        chr(127397 + ord(letter))
        for letter in code
    )


app.jinja_env.globals['country_flag'] = country_flag
# Competition / trophy rules for the record forms (see competitions.py).
app.jinja_env.globals['competition_form_rules'] = form_rules

@app.route('/')
def index():
    return render_template('index.html')

# ========== PUBLIC ROUTES ==========
@app.route('/api/suggest-names')
def suggest_names():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])

    # Only names that START with the typed letters
    students = User.query.filter(
        User.role == 'Athlete',
        User.is_verified == True,
        User.full_name.ilike(f'{q}%')          # ← starts with
    ).order_by(User.full_name).limit(8).all()

    return jsonify([{'full_name': s.full_name} for s in students])

# ==========================================================
# PWA: SERVICE WORKER
# ==========================================================
# Served from the site root (not /static/) so the worker's
# scope covers the whole app.
@app.route('/service-worker.js')
def service_worker():
    response = app.send_static_file('service-worker.js')

    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'

    return response

# ==========================================================
# MESSAGING RECIPIENT NAME SUGGESTIONS
# ==========================================================

# ==========================================================
# MESSAGE RECIPIENT SEARCH
# ==========================================================

@app.route('/api/suggest-message-recipients')
@login_required
def suggest_message_recipients():

    query = request.args.get('q', '').strip()

    if not query:
        return jsonify([])

    allowed_roles = ['Coach', 'Athlete', 'Scout']

    users = (
        User.query
        .filter(
            User.id != current_user.id,
            User.role.in_(allowed_roles),
            User.is_verified == True,
            User.full_name.ilike(f'%{query}%')
        )
        .order_by(User.full_name.asc())
        .limit(10)
        .all()
    )

    results = []

    for user in users:

        # Extra permission check
        if not can_message(current_user, user):
            continue

        results.append({
            'id': user.id,
            'full_name': user.full_name,
            'role': user.role,
            'school': user.school or ''
        })

    return jsonify(results)

# ==========================================================
# PWA: OFFLINE FALLBACK PAGE
# ==========================================================
# Precached by the service worker and shown when a page
# cannot be loaded because the device is offline.
@app.route('/offline')
def offline():
    return render_template('offline.html')

# =================premiumPro5 ROUTE=========================================
# =================premiumPro5 ROUTE=========================================
@app.route('/profile/premium', methods=['POST'])
@login_required
def update_premium_profile():
    # Premium access is determined server-side.
    if not has_premium_profile_access(current_user.id):
        flash(
            'Premium Recruit-Ready Profile access is required.',
            'warning'
        )
        return redirect(url_for('profile'))

    # Only athletes can use the athlete premium profile.
    if current_user.role != 'Athlete':
        flash(
            'Premium Recruit-Ready Profiles are currently available to athletes.',
            'danger'
        )
        return redirect(url_for('profile'))

    bio = request.form.get('bio', '').strip()

    if len(bio) > 2000:
        flash(
            'Your premium profile biography cannot exceed 2,000 characters.',
            'danger'
        )
        return redirect(url_for('profile'))

    is_public = request.form.get('is_public') == '1'

    premium_profile = get_or_create_premium_profile(
        current_user.id
    )

    premium_profile.bio = bio or None
    premium_profile.is_enabled = True
    premium_profile.is_public = is_public
    premium_profile.updated_at = datetime.now(timezone.utc)

    create_audit_log(
        action='premium_profile_updated',
        actor_user_id=current_user.id,
        target_type='PremiumProfile',
        target_id=premium_profile.id,
        details={
            'is_public': is_public
        }
    )

    db.session.commit()

    flash(
        'Premium Recruit-Ready Profile updated successfully.',
        'success'
    )

    return redirect(url_for('profile'))
# =================pinpremiumPro5RECRD ROUTE=========================================
# =================pinpremiumPro5RECRD ROUTE=========================================
@app.route(
    '/profile/premium/pin-record/<int:record_id>',
    methods=['POST']
)
@login_required
def pin_premium_record(record_id):

    if not has_premium_profile_access(current_user.id):
        flash(
            'Premium Recruit-Ready Profile access is required.',
            'warning'
        )
        return redirect(url_for('profile'))

    if current_user.role != 'Athlete':
        flash(
            'Only athletes can pin sports records.',
            'danger'
        )
        return redirect(url_for('profile'))

    record = SportRecord.query.get_or_404(record_id)

    # Critical object-level authorization.
    if record.user_id != current_user.id:
        flash(
            'You can only pin your own sports records.',
            'danger'
        )
        return redirect(url_for('profile'))

    # Only approved records can appear as recruiting evidence.
    if record.status != 'approved':
        flash(
            'Only approved sports records can be pinned.',
            'warning'
        )
        return redirect(url_for('profile'))

    existing_pin = PinnedSportRecord.query.filter_by(
        user_id=current_user.id,
        sport_record_id=record.id
    ).first()

    if existing_pin:
        flash(
            'This record is already pinned.',
            'info'
        )
        return redirect(url_for('profile'))

    pinned_count = PinnedSportRecord.query.filter_by(
        user_id=current_user.id
    ).count()

    if pinned_count >= 3:
        flash(
            'You can pin a maximum of 3 records.',
            'warning'
        )
        return redirect(url_for('profile'))

    next_order = pinned_count + 1

    pinned = PinnedSportRecord(
        user_id=current_user.id,
        sport_record_id=record.id,
        display_order=next_order
    )

    db.session.add(pinned)

    create_audit_log(
        action='premium_record_pinned',
        actor_user_id=current_user.id,
        target_type='SportRecord',
        target_id=record.id,
        details={
            'display_order': next_order
        }
    )

    db.session.commit()

    flash(
        'Record added to your premium profile.',
        'success'
    )

    return redirect(url_for('profile'))
# =================unpin-PP-Record ROUTE=========================================
@app.route(
    '/profile/premium/unpin-record/<int:record_id>',
    methods=['POST']
)
@login_required
def unpin_premium_record(record_id):

    if not has_premium_profile_access(current_user.id):
        flash(
            'Premium Recruit-Ready Profile access is required.',
            'warning'
        )
        return redirect(url_for('profile'))

    pinned = PinnedSportRecord.query.filter_by(
        user_id=current_user.id,
        sport_record_id=record_id
    ).first()

    if not pinned:
        flash(
            'That record is not currently pinned.',
            'info'
        )
        return redirect(url_for('profile'))

    db.session.delete(pinned)

    create_audit_log(
        action='premium_record_unpinned',
        actor_user_id=current_user.id,
        target_type='SportRecord',
        target_id=record_id
    )

    db.session.commit()

    # Re-number remaining pins.
    remaining = PinnedSportRecord.query.filter_by(
        user_id=current_user.id
    ).order_by(
        PinnedSportRecord.display_order.asc()
    ).all()

    for index, item in enumerate(remaining, start=1):
        item.display_order = index

    db.session.commit()

    flash(
        'Record removed from your premium profile.',
        'success'
    )

    return redirect(url_for('profile'))
# ==========================================================
# PRICING, CHECKOUT AND BILLING (plans.py has the plans)
# ==========================================================
# There is no payment API yet, so mobile money is confirmed by hand:
#   1. The user picks a plan; checkout shows D.A.R.T.'s Orange Money /
#      MTN MoMo number and a unique reference (DART-XXXXXXXX).
#   2. They pay from their phone, then enter the transaction ID.
#   3. The Super Admin checks it against the MoMo statement and confirms
#      (or rejects) it on /admin/payments. Confirming starts 30 days.
# Cards show "coming soon" until a card processor is connected; then
# only step 3 needs automating (activate_plan() does the rest).
#
# Set in the environment:
#   ORANGE_MONEY_NUMBER, MTN_MOMO_NUMBER, PAYMENT_ACCOUNT_NAME

PAYMENT_ACCOUNTS = {
    'orange_money': (os.environ.get('ORANGE_MONEY_NUMBER') or '').strip(),
    'mtn_momo': (os.environ.get('MTN_MOMO_NUMBER') or '').strip(),
}
PAYMENT_ACCOUNT_NAME = (os.environ.get('PAYMENT_ACCOUNT_NAME') or 'D.A.R.T.').strip()

TRANSACTION_ID_PATTERN = re.compile(r'^[A-Za-z0-9.\-]{5,40}$')
PAYER_PHONE_PATTERN = re.compile(r'^\+?[0-9][0-9 ]{6,19}$')
MAX_OPEN_PAYMENTS = 3
PAYMENT_REJECT_REASONS = (
    'Transaction ID not found on our statement',
    'Amount received does not match the plan price',
    'Reference missing or wrong',
    'Duplicate submission',
    'Other (contact support)',
)

app.jinja_env.globals.update(
    payment_methods=PAYMENT_METHODS,
    payment_accounts=PAYMENT_ACCOUNTS,
    payment_account_name=PAYMENT_ACCOUNT_NAME,
)


def _new_payment_reference():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # no 0/O/1/I mix-ups
    return 'DART-' + ''.join(secrets.choice(alphabet) for _ in range(8))


def open_checkout_payment(user, plan):
    """The not-yet-submitted payment for this user + plan (one reference per checkout)."""
    payment = Payment.query.filter_by(
        user_id=user.id,
        plan_code=plan.code,
        status='pending'
    ).order_by(Payment.id.desc()).first()

    if payment:
        return payment

    payment = Payment(
        user_id=user.id,
        provider='manual',
        payment_reference=_new_payment_reference(),
        amount=plan.price,
        currency='USD',
        status='pending',
        plan_code=plan.code,
        description=f'D.A.R.T. {plan.name} - 30 days'
    )
    db.session.add(payment)
    db.session.commit()
    return payment


@app.route('/pricing')
def pricing():
    my_eligible = plan_for_user(current_user) if current_user.is_authenticated else None
    awaiting = []

    if current_user.is_authenticated:
        awaiting = Payment.query.filter_by(
            user_id=current_user.id,
            status='awaiting_review'
        ).order_by(Payment.created_at.desc()).all()

    athlete_plans = [p for p in PLANS.values() if p.audience == 'athlete']

    return render_template(
        'pricing.html',
        athlete_plans=athlete_plans,
        high_school_plan=PLANS['athlete_high_school'],
        pro_athlete_plans=[p for p in athlete_plans if p.code != 'athlete_high_school'],
        pro_from_price=min(
            (p for p in athlete_plans if p.code != 'athlete_high_school'),
            key=lambda p: p.price
        ).price_label,
        free_org_preview=FREE_ORG_ROSTER_PREVIEW,
        scout_plan=PLANS['scout_pro'],
        organization_plan=PLANS['organization_pro'],
        free_limits=FREE_ATHLETE_LIMITS,
        my_eligible=my_eligible,
        my_subscription=active_subscription(current_user),
        awaiting=awaiting,
        eligibility_error=eligibility_error,
    )


@app.route('/checkout/<plan_code>', methods=['GET', 'POST'])
@login_required
def checkout(plan_code):
    plan = PLANS.get(plan_code)

    if not plan:
        abort(404)

    error = eligibility_error(current_user, plan)

    if error:
        flash(error, 'warning')
        return redirect(url_for('pricing'))

    open_count = Payment.query.filter_by(
        user_id=current_user.id,
        status='awaiting_review'
    ).count()

    payment = open_checkout_payment(current_user, plan)

    if request.method == 'POST':
        method = request.form.get('payment_method', '').strip()
        payer_phone = re.sub(r'\s+', ' ', request.form.get('payer_phone', '').strip())
        transaction_id = request.form.get('transaction_id', '').strip().upper()

        def back(message):
            flash(message, 'danger')
            return redirect(url_for('checkout', plan_code=plan.code))

        if method == 'card':
            return back('Card payments are coming soon. Please use Orange Money or MTN MoMo for now.')

        if method not in plan.methods or method not in MOMO_METHODS:
            return back('Please choose Orange Money or MTN MoMo.')

        if not PAYMENT_ACCOUNTS.get(method):
            return back(f'{PAYMENT_METHODS[method]} payments are not set up yet. Please try the other option.')

        if not PAYER_PHONE_PATTERN.match(payer_phone):
            return back('Please enter the phone number you paid from, e.g. +231 77 000 0000.')

        if not TRANSACTION_ID_PATTERN.match(transaction_id):
            return back('Please enter the transaction ID from your payment SMS (letters and numbers).')

        if open_count >= MAX_OPEN_PAYMENTS:
            return back('You already have payments waiting for confirmation. Please wait for them to be reviewed.')

        provider_payment_id = f'{method}:{transaction_id}'

        if Payment.query.filter_by(provider_payment_id=provider_payment_id).first():
            return back('This transaction ID has already been submitted.')

        payment.payment_method = method
        payment.payer_phone = payer_phone
        payment.provider_payment_id = provider_payment_id
        payment.amount = plan.price
        payment.status = 'awaiting_review'

        create_audit_log(
            action='payment_submitted',
            actor_user_id=current_user.id,
            target_type='Payment',
            target_id=str(payment.id),
            details={
                'plan_code': plan.code,
                'amount': str(plan.price),
                'method': method,
                'reference': payment.payment_reference,
            }
        )
        db.session.commit()

        flash(
            'Thank you! Your payment was submitted. Premium starts as soon as '
            'D.A.R.T. confirms it (usually within 24 hours).',
            'success'
        )
        return redirect(url_for('billing'))

    return render_template(
        'checkout.html',
        plan=plan,
        payment=payment,
        open_count=open_count,
        max_open_payments=MAX_OPEN_PAYMENTS,
    )


@app.route('/billing')
@login_required
def billing():
    payments = Payment.query.filter(
        Payment.user_id == current_user.id,
        Payment.status != 'pending'
    ).order_by(Payment.created_at.desc()).limit(50).all()

    return render_template(
        'billing.html',
        subscription=active_subscription(current_user),
        plan=current_plan(current_user),
        eligible_plan=plan_for_user(current_user),
        payments=payments,
    )


# ---------- Old Recruit-Ready premium URLs now lead to the pricing page ----------
@app.route('/profile/premium')
@login_required
def premium_plans():
    return redirect(url_for('pricing'))


@app.route('/profile/premium/checkout', methods=['POST'])
@login_required
def start_premium_checkout():
    return redirect(url_for('pricing'))


@app.route('/profile/premium/checkout/pending/<payment_reference>')
@login_required
def premium_checkout_pending(payment_reference):
    return redirect(url_for('billing'))


# ---------- Super Admin: confirm mobile-money payments ----------
def require_super_admin():
    if not current_user.is_authenticated or current_user.role != 'System':
        abort(403)


@app.route('/admin/payments')
@login_required
def admin_payments():
    require_super_admin()

    awaiting = Payment.query.filter_by(
        status='awaiting_review'
    ).order_by(Payment.created_at.asc()).all()

    reviewed = Payment.query.filter(
        Payment.status.in_(('succeeded', 'failed'))
    ).order_by(Payment.reviewed_at.desc()).limit(30).all()

    return render_template(
        'admin_payments.html',
        awaiting=awaiting,
        reviewed=reviewed,
        reject_reasons=PAYMENT_REJECT_REASONS,
    )


@app.route('/admin/payments/<int:payment_id>/confirm', methods=['POST'])
@login_required
def admin_confirm_payment(payment_id):
    require_super_admin()

    payment = db.session.get(Payment, payment_id)

    if not payment or payment.status != 'awaiting_review':
        flash('This payment is no longer waiting for review.', 'warning')
        return redirect(url_for('admin_payments'))

    plan = PLANS.get(payment.plan_code)
    user = db.session.get(User, payment.user_id)

    if not plan or not user:
        flash('This payment has no valid plan or user.', 'danger')
        return redirect(url_for('admin_payments'))

    subscription = activate_plan(user, plan)

    payment.status = 'succeeded'
    payment.paid_at = datetime.utcnow()
    payment.subscription_id = subscription.id
    payment.reviewed_by = current_user.id
    payment.reviewed_at = datetime.utcnow()

    # Athlete plans include the Recruit-Ready premium profile.
    if plan.audience == 'athlete':
        grant_entitlement(
            user.id,
            'premium_profile',
            source='subscription',
            source_reference=payment.payment_reference,
            expires_at=subscription.current_period_end
        )

    create_audit_log(
        action='payment_confirmed',
        actor_user_id=current_user.id,
        target_type='Payment',
        target_id=str(payment.id),
        details={
            'user_id': user.id,
            'plan_code': plan.code,
            'amount': str(payment.amount),
            'period_end': subscription.current_period_end.isoformat(),
        }
    )
    db.session.commit()

    flash(
        f'Confirmed: {user.full_name} now has {plan.name} until '
        f'{subscription.current_period_end.strftime("%B %d, %Y")}.',
        'success'
    )
    return redirect(url_for('admin_payments'))


@app.route('/admin/payments/<int:payment_id>/reject', methods=['POST'])
@login_required
def admin_reject_payment(payment_id):
    require_super_admin()

    payment = db.session.get(Payment, payment_id)
    reason = request.form.get('reason', '').strip()

    if not payment or payment.status != 'awaiting_review':
        flash('This payment is no longer waiting for review.', 'warning')
        return redirect(url_for('admin_payments'))

    if reason not in PAYMENT_REJECT_REASONS:
        flash('Please choose a reason.', 'danger')
        return redirect(url_for('admin_payments'))

    payment.status = 'failed'
    payment.failure_reason = reason
    payment.reviewed_by = current_user.id
    payment.reviewed_at = datetime.utcnow()

    create_audit_log(
        action='payment_rejected',
        actor_user_id=current_user.id,
        target_type='Payment',
        target_id=str(payment.id),
        details={'user_id': payment.user_id, 'reason': reason}
    )
    db.session.commit()

    flash('Payment rejected. The user can see the reason on their Billing page.', 'info')
    return redirect(url_for('admin_payments'))


@app.context_processor
def inject_admin_payment_count():
    if current_user.is_authenticated and current_user.role == 'System':
        return {'payments_awaiting': Payment.query.filter_by(status='awaiting_review').count()}
    return {'payments_awaiting': 0}


# =================SEARCH ROUTE=========================================
# ==========================================================
# SEARCH: FILTER DEFINITIONS
# ==========================================================
# Numeric record stats that can be filtered ("goals >= 2"), by sport.
SEARCH_STATS = {
    'Football': [
        ('goals', 'Goals'),
        ('assists', 'Assists'),
        ('yellow_cards', 'Yellow Cards'),
        ('red_cards', 'Red Cards'),
        ('clean_sheets', 'Clean Sheets'),
        ('saves', 'Saves'),
    ],
    'Basketball': [
        ('points', 'Points'),
        ('assists', 'Assists'),
        ('blocks', 'Blocks'),
        ('total_rebounds', 'Total Rebounds'),
        ('offensive_rebounds', 'Offensive Rebounds'),
        ('defensive_rebounds', 'Defensive Rebounds'),
        ('sent_off', 'Times Sent Off'),
    ],
    'Kickball': [
        ('home_runs', 'Home Runs'),
        ('cut_base', 'Cut Base'),
        ('foul_played', 'Fouls Played'),
        ('kickball_yellow_cards', 'Yellow Cards'),
        ('kickball_red_cards', 'Red Cards'),
    ],
    # Every sport.
    'All': [
        ('match_minutes_played', 'Match Minutes'),
        ('man_of_the_match', 'MOTM / QOTM'),
        ('mvp', 'MVP'),
    ],
}

SEARCH_STAT_COLUMNS = {
    field
    for stats in SEARCH_STATS.values()
    for field, _ in stats
}

SEARCH_STAT_OPERATORS = {
    'gte': ('≥', lambda value, target: value >= target),
    'lte': ('≤', lambda value, target: value <= target),
    'eq': ('=', lambda value, target: value == target),
}

MAX_STAT_CONDITIONS = 5

# Filters beyond the basic name/team/opponent/sport/year search. Kept in
# one list so they can later be limited to paid scout subscribers.
ADVANCED_SEARCH_FILTERS = (
    'gender', 'nationality', 'age_min', 'age_max', 'height_min',
    'height_max', 'weight_min', 'weight_max', 'preferred_foot',
    'competition', 'club_division', 'age_group', 'competition_team',
    'position', 'trophy', 'date_from', 'date_to', 'motm', 'mvp',
    'stat_scope', 'min_games', 'stat',
)

# Locked for free users. Competition stays free: it's in the basic row.
LOCKED_SEARCH_FILTERS = (
    set(ADVANCED_SEARCH_FILTERS) - {'competition'}
) | {'stat_op', 'stat_value'}


def _search_int(value, minimum=None, maximum=None):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None

    if minimum is not None and number < minimum:
        return None

    if maximum is not None and number > maximum:
        return None

    return number


def _search_decimal(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None

    return number if 0 <= number <= 1000 else None


def _search_date(value):
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _strip_locked_filters(raw_args):
    """
    Free users: drop advanced filters. Returns (args, whether any advanced
    filter was actually used).
    """
    kept, used = [], False

    for key, value in raw_args.items(multi=True):
        if key in LOCKED_SEARCH_FILTERS:
            if (value or '').strip() and not (key == 'stat_scope' and value == 'game'):
                used = True
            continue
        kept.append((key, value))

    return MultiDict(kept), used


@app.route('/search')
def search():
    advanced_allowed = can_use_advanced_search(current_user)
    advanced_locked_used = False

    if advanced_allowed:
        args = request.args
    else:
        args, advanced_locked_used = _strip_locked_filters(request.args)

    name = args.get('name', '').strip()
    school = args.get('school', '').strip()
    team_played_against = args.get('team_played_against', '').strip()
    year = args.get('year', '').strip()
    sport = args.get('sport', '').strip()
    sort_by = args.get('sort_by', '').strip()

    # ---------- Advanced filters ----------
    gender = args.get('gender', '').strip()
    nationality = args.get('nationality', '').strip()
    age_min = _search_int(args.get('age_min'), 3, 99)
    age_max = _search_int(args.get('age_max'), 3, 99)
    height_min = _search_decimal(args.get('height_min'))
    height_max = _search_decimal(args.get('height_max'))
    weight_min = _search_decimal(args.get('weight_min'))
    weight_max = _search_decimal(args.get('weight_max'))
    preferred_foot = args.get('preferred_foot', '').strip()
    competition = args.get('competition', '').strip()
    club_division = args.get('club_division', '').strip()
    age_group = args.get('age_group', '').strip()
    competition_team = args.get('competition_team', '').strip()
    position = args.get('position', '').strip()
    trophy = args.get('trophy', '').strip()
    date_from = _search_date(args.get('date_from'))
    date_to = _search_date(args.get('date_to'))
    motm = args.get('motm') == '1'
    mvp = args.get('mvp') == '1'
    stat_scope = args.get('stat_scope', 'game')
    stat_scope = stat_scope if stat_scope in ('game', 'total') else 'game'
    min_games = _search_int(args.get('min_games'), 1, 500)

    # Stat conditions, e.g. goals >= 2. Only known columns and operators.
    stat_conditions = []

    for field, operator, value in zip(
        args.getlist('stat'),
        args.getlist('stat_op'),
        args.getlist('stat_value')
    ):
        field = field.strip()
        number = _search_int(value, 0, 100000)

        if (
            field in SEARCH_STAT_COLUMNS
            and operator in SEARCH_STAT_OPERATORS
            and number is not None
        ):
            stat_conditions.append((field, operator, number))

        if len(stat_conditions) >= MAX_STAT_CONDITIONS:
            break

    # ==========================================================
    # OPTIONS FOR THE FILTER FORM (from approved records only)
    # ==========================================================
    available_years = [
        row[0]
        for row in (
            db.session.query(SportRecord.year)
            .filter(SportRecord.status == 'approved')
            .distinct()
            .order_by(SportRecord.year.desc())
            .all()
        )
    ]

    available_nationalities = [
        row[0]
        for row in (
            db.session.query(User.nationality)
            .filter(
                User.role == 'Athlete',
                User.is_verified == True,
                User.nationality.isnot(None),
                User.nationality != ''
            )
            .distinct()
            .order_by(User.nationality)
            .all()
        )
    ]

    available_trophies = [
        row[0]
        for row in (
            db.session.query(SportRecord.trophy)
            .filter(
                SportRecord.status == 'approved',
                SportRecord.trophy.isnot(None),
                SportRecord.trophy != ''
            )
            .distinct()
            .order_by(SportRecord.trophy)
            .all()
        )
    ]

    # ==========================================================
    # BASE QUERY
    # ONLY VERIFIED ATHLETES + APPROVED RECORDS
    # ==========================================================
    query = (
        SportRecord.query
        .join(User)
        .filter(
            User.role == 'Athlete',
            User.is_verified == True,
            SportRecord.status == 'approved'
        )
    )

    # Free athletes: only their latest record is shown to others
    # (plans.apply_record_visibility).
    query = apply_record_visibility(query, current_user, 'search')

    # ---------- Basic ----------
    if name:
        query = query.filter(User.full_name.ilike(f'%{name}%'))

    # Team: record team or athlete school.
    if school:
        query = query.filter(
            or_(
                SportRecord.team.ilike(f'%{school}%'),
                User.school.ilike(f'%{school}%')
            )
        )

    if team_played_against:
        query = query.filter(
            SportRecord.team_played_against.ilike(f'%{team_played_against}%')
        )

    if year:
        try:
            query = query.filter(SportRecord.year == int(year))
        except ValueError:
            year = ''

    valid_sports = {'Football', 'Basketball', 'Kickball'}

    if sport in valid_sports:
        query = query.filter(SportRecord.sport == sport)
    else:
        sport = ''

    # ---------- Athlete profile ----------
    if gender in ('Male', 'Female'):
        query = query.filter(User.gender == gender)
    else:
        gender = ''

    if nationality:
        query = query.filter(User.nationality == nationality)

    if age_min is not None:
        query = query.filter(User.age >= age_min)

    if age_max is not None:
        query = query.filter(User.age <= age_max)

    if height_min is not None:
        query = query.filter(User.height_cm >= height_min)

    if height_max is not None:
        query = query.filter(User.height_cm <= height_max)

    if weight_min is not None:
        query = query.filter(User.weight_kg >= weight_min)

    if weight_max is not None:
        query = query.filter(User.weight_kg <= weight_max)

    if preferred_foot in ('Right', 'Left', 'Both'):
        query = query.filter(User.preferred_foot == preferred_foot)
    else:
        preferred_foot = ''

    # ---------- Competition ----------
    if competition in VALID_COMPETITIONS:
        query = query.filter(SportRecord.competition_category == competition)
    else:
        competition = ''

    if club_division:
        query = query.filter(SportRecord.club_division == club_division)

    if age_group:
        query = query.filter(SportRecord.age_group == age_group)

    if competition_team:
        query = query.filter(SportRecord.competition_team == competition_team)

    if position:
        query = query.filter(SportRecord.position == position)

    if trophy:
        query = query.filter(SportRecord.trophy == trophy)

    if date_from:
        query = query.filter(SportRecord.game_date >= date_from)

    if date_to:
        query = query.filter(SportRecord.game_date <= date_to)

    if motm:
        query = query.filter(SportRecord.man_of_the_match >= 1)

    if mvp:
        query = query.filter(SportRecord.mvp >= 1)

    # ---------- Stats in a single game ----------
    if stat_scope == 'game':
        for field, operator, number in stat_conditions:
            if field == 'total_rebounds':
                # Not stored: offensive + defensive.
                column = (
                    func.coalesce(SportRecord.offensive_rebounds, 0)
                    + func.coalesce(SportRecord.defensive_rebounds, 0)
                )
            else:
                column = func.coalesce(getattr(SportRecord, field), 0)

            if operator == 'gte':
                query = query.filter(column >= number)
            elif operator == 'lte':
                query = query.filter(column <= number)
            else:
                query = query.filter(column == number)

    records = query.all()

    # ---------- Stats as season / career totals ----------
    # Sum each stat over the athlete's matching games, then keep
    # athletes whose totals meet every condition.
    if stat_scope == 'total' and stat_conditions:
        totals = {}

        for record in records:
            athlete_totals = totals.setdefault(record.user_id, {})

            for field, _, _ in stat_conditions:
                athlete_totals[field] = (
                    athlete_totals.get(field, 0)
                    + (getattr(record, field) or 0)
                )

        qualified = {
            user_id
            for user_id, athlete_totals in totals.items()
            if all(
                SEARCH_STAT_OPERATORS[operator][1](athlete_totals.get(field, 0), number)
                for field, operator, number in stat_conditions
            )
        }

        records = [record for record in records if record.user_id in qualified]

    # ---------- Minimum number of matching games ----------
    if min_games:
        games_per_athlete = {}

        for record in records:
            games_per_athlete[record.user_id] = games_per_athlete.get(record.user_id, 0) + 1

        records = [
            record
            for record in records
            if games_per_athlete[record.user_id] >= min_games
        ]

    # ==========================================================
    # METRICS
    # ==========================================================

    def trophy_count(record):
        trophy_value = (record.trophy or '').strip()

        if not trophy_value or trophy_value.lower() == 'none':
            return 0

        return len([
            item for item in trophy_value.split(',')
            if item.strip()
            and item.strip().lower() != 'none'
        ])

    def stat(field):
        return lambda record: getattr(record, field) or 0

    games_metric = stat('games_played')
    motm_metric = stat('man_of_the_match')
    mvp_metric = stat('mvp')

    # ==========================================================
    # ONLY ALLOW VALID RANKING FOR THE SELECTED SPORT
    # ==========================================================

    # Rankings that work for any sport.
    ranking_functions = {
        'most_recent': lambda r: r.game_date or datetime.min.date(),
        'youngest': lambda r: -(r.user.age or 999),
        'most_minutes': stat('match_minutes_played'),
    }

    if sport == 'Football':

        ranking_functions.update({
            'highest_goals': stat('goals'),
            'highest_assists': stat('assists'),
            'highest_games': games_metric,
            'highest_trophies': trophy_count,
            'highest_yellow_cards': stat('yellow_cards'),
            'highest_red_cards': stat('red_cards'),
            'highest_clean_sheets': stat('clean_sheets'),
            'highest_saves': stat('saves'),
            'highest_motm': motm_metric,
            'highest_mvp': mvp_metric
        })

    elif sport == 'Basketball':

        ranking_functions.update({
            'highest_points': stat('points'),
            'highest_assists': stat('assists'),
            'highest_games': games_metric,
            'highest_trophies': trophy_count,
            'highest_blocks': stat('blocks'),
            'highest_rebounds': stat('total_rebounds'),
            'highest_offensive_rebounds': stat('offensive_rebounds'),
            'highest_defensive_rebounds': stat('defensive_rebounds'),
            'highest_sent_off': stat('sent_off'),
            'highest_motm': motm_metric,
            'highest_mvp': mvp_metric
        })

    elif sport == 'Kickball':

        ranking_functions.update({
            'highest_home_runs': stat('home_runs'),
            'highest_games': games_metric,
            'highest_trophies': trophy_count,
            'highest_red_cards': stat('kickball_red_cards'),
            'highest_yellow_cards': stat('kickball_yellow_cards'),
            'highest_cut_base': stat('cut_base'),
            'highest_foul_played': stat('foul_played'),
            'highest_motm': motm_metric,
            'highest_mvp': mvp_metric
        })

    # ==========================================================
    # APPLY RANKING
    # ==========================================================

    if sort_by in ranking_functions:

        records.sort(
            key=ranking_functions[sort_by],
            reverse=True
        )

    else:

        sort_by = ''
        records.sort(
            key=lambda r: (
                (r.user.full_name or '').lower(),
                -(r.year or 0),
                (r.sport or '').lower()
            )
        )

    # ==========================================================
    # GROUP BY ATHLETE
    # ==========================================================

    grouped_results = {}

    for record in records:

        student = record.user

        if student.id not in grouped_results:

            grouped_results[student.id] = {
                'student': student,
                'records': []
            }

        grouped_results[student.id]['records'].append(record)

    results = list(grouped_results.values())

    # ==========================================================
    # SUMMARY + ACTIVE FILTERS
    # ==========================================================
    summary = {
        'athletes': len(results),
        'records': len(records),
        'male': sum(1 for item in results if item['student'].gender == 'Male'),
        'female': sum(1 for item in results if item['student'].gender == 'Female'),
    }

    filters = {
        'name': name, 'school': school, 'team_played_against': team_played_against,
        'year': year, 'sport': sport, 'sort_by': sort_by,
        'gender': gender, 'nationality': nationality,
        'age_min': age_min, 'age_max': age_max,
        'height_min': height_min, 'height_max': height_max,
        'weight_min': weight_min, 'weight_max': weight_max,
        'preferred_foot': preferred_foot, 'competition': competition,
        'club_division': club_division, 'age_group': age_group,
        'competition_team': competition_team, 'position': position,
        'trophy': trophy,
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
        'motm': motm, 'mvp': mvp, 'stat_scope': stat_scope,
        'min_games': min_games,
        'stat_conditions': stat_conditions,
    }

    advanced_count = sum(
        1 for key in ADVANCED_SEARCH_FILTERS
        if key not in ('stat_scope', 'stat') and filters.get(key) not in (None, '', False)
    ) + len(stat_conditions)

    search_active = bool(
        name or school or team_played_against or year or sport
        or sort_by or advanced_count
    )

    return render_template(
        'search.html',
        results=results,
        name=name,
        school=school,
        team_played_against=team_played_against,
        year=year,
        sport=sport,
        sort_by=sort_by,
        available_years=available_years,
        available_nationalities=available_nationalities,
        available_trophies=available_trophies,
        filters=filters,
        advanced_count=advanced_count,
        search_active=search_active,
        summary=summary,
        search_stats=SEARCH_STATS,
        stat_operators={key: symbol for key, (symbol, _) in SEARCH_STAT_OPERATORS.items()},
        max_stat_conditions=MAX_STAT_CONDITIONS,
        valid_positions={k: sorted(v) for k, v in VALID_POSITIONS.items()},
        current_query=clean_search_query(request.query_string.decode('utf-8', 'ignore')),
        saved_searches=saved_searches_for(current_user, 20) if current_user.is_authenticated else [],
        advanced_allowed=advanced_allowed,
        advanced_locked_used=advanced_locked_used,
    )
# ==========================================================
# SAVED SEARCHES
# ==========================================================
# Any logged-in user can save the current search filters and run them
# again later (search page + scout dashboard). Later this can be limited
# to premium users for the advanced filters.

SAVED_SEARCH_LIMIT = 50

# Search parameters that may be stored (everything the search form sends).
SAVED_SEARCH_KEYS = {
    'name', 'school', 'team_played_against', 'year', 'sport', 'sort_by',
    'stat_op', 'stat_value',
} | set(ADVANCED_SEARCH_FILTERS)


def clean_search_query(query_string):
    """Keep only known, non-empty search parameters (max 60 pairs)."""
    pairs = [
        (key, value.strip())
        for key, value in parse_qsl(query_string or '', keep_blank_values=False)
        if key in SAVED_SEARCH_KEYS and value.strip()
    ][:60]

    return urlencode(pairs)


def saved_search_summary(query_string):
    """Short human description, e.g. 'Football · Male · Goals ≥ 2'."""
    args = MultiDict(parse_qsl(query_string or ''))
    parts = []

    if args.get('name'):
        parts.append(f'"{args["name"]}"')

    for key in ('sport', 'gender', 'competition', 'position', 'year',
                'nationality', 'age_group', 'club_division', 'competition_team', 'trophy'):
        if args.get(key):
            parts.append(args[key])

    if args.get('school'):
        parts.append(f'Team: {args["school"]}')

    age_min, age_max = args.get('age_min'), args.get('age_max')
    if age_min and age_max:
        parts.append(f'Age {age_min}–{age_max}')
    elif age_min:
        parts.append(f'Age ≥ {age_min}')
    elif age_max:
        parts.append(f'Age ≤ {age_max}')

    stat_labels = {
        field: label
        for stats in SEARCH_STATS.values()
        for field, label in stats
    }

    for field, operator, value in zip(args.getlist('stat'), args.getlist('stat_op'), args.getlist('stat_value')):
        symbol = SEARCH_STAT_OPERATORS.get(operator, ('?',))[0]
        parts.append(f'{stat_labels.get(field, field)} {symbol} {value}')

    if args.get('stat_scope') == 'total':
        parts.append('season totals')

    if args.get('motm'):
        parts.append('MOTM winners')

    if args.get('mvp'):
        parts.append('MVP winners')

    return ' · '.join(parts) or 'All athletes'


app.jinja_env.globals['saved_search_summary'] = saved_search_summary


def saved_searches_for(user, limit=None):
    query = SavedSearch.query.filter_by(user_id=user.id).order_by(SavedSearch.created_at.desc())
    return query.limit(limit).all() if limit else query.all()


@app.route('/search/save', methods=['POST'])
@login_required
def save_search():
    query_string = clean_search_query(request.form.get('query', ''))
    back = url_for('search') + (f'?{query_string}' if query_string else '')

    if not query_string:
        flash('Choose at least one filter before saving a search.', 'warning')
        return redirect(back)

    existing = SavedSearch.query.filter_by(
        user_id=current_user.id,
        query_string=query_string
    ).first()

    if existing:
        flash(f'This search is already saved as "{existing.name}".', 'info')
        return redirect(back)

    if SavedSearch.query.filter_by(user_id=current_user.id).count() >= SAVED_SEARCH_LIMIT:
        flash(
            f'You can keep up to {SAVED_SEARCH_LIMIT} saved searches. '
            'Delete one to save another.',
            'warning'
        )
        return redirect(back)

    name = (request.form.get('search_name') or '').strip()[:80]

    if not name:
        name = saved_search_summary(query_string)[:80]

    db.session.add(SavedSearch(
        user_id=current_user.id,
        name=name,
        query_string=query_string
    ))
    db.session.commit()

    flash(f'Search saved as "{name}".', 'success')
    return redirect(back)


@app.route('/search/saved/<int:search_id>/delete', methods=['POST'])
@login_required
def delete_saved_search(search_id):
    saved = db.session.get(SavedSearch, search_id)

    if not saved or saved.user_id != current_user.id:
        abort(404)

    db.session.delete(saved)
    db.session.commit()

    flash('Saved search deleted.', 'info')
    return redirect(safe_next_url(request.form.get('next')) or url_for('search'))


# ========== AUTH ROUTES ==========
@app.route('/register', methods=['GET', 'POST'])
@app.route('/register/<registration_category>', methods=['GET', 'POST'])
def register(registration_category=None):

    category_map = {
        'all-athlete': 'All Athlete',
        'county-meet': 'County Meet Athlete',
        'club-league': 'Club League Athlete',
        'university': 'University Athlete',
        'community-league': 'Community/Area League Athlete',
        'high-school': 'High School Athlete'
    }

    # Determine selected registration category
    if registration_category:
        if registration_category not in category_map:
            flash('Invalid athlete registration category.', 'danger')
            return redirect(url_for('register'))

        selected_category = category_map[registration_category]
    else:
        selected_category = 'All Athlete'

    if request.method == 'POST':

        full_name = request.form['full_name'].strip()
        school = request.form['school'].strip()
        gender = request.form.get('gender', '').strip()
        nationality = request.form.get('nationality', '').strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        age_raw = request.form.get('age', '').strip()
        date_of_birth_raw = request.form.get('date_of_birth', '').strip()
        nationality = request.form.get('nationality', '').strip()
        height_cm_raw = request.form.get('height_cm', '').strip()
        weight_kg_raw = request.form.get('weight_kg', '').strip()

        # ==========================================================
        # PASSWORD STRENGTH
        # ==========================================================

        if not is_strong_password(password):
            flash(
                'Password must be at least 8 characters and include '
                'an Uppercase Letter, lowercase letter, number, '
                'and special character.',
                'danger'
            )
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        # ==========================================================
        # GENDER VALIDATION
        # ==========================================================

        if gender not in ['Male', 'Female']:
            flash('Please select a valid gender.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )
        if not nationality:
            flash(
                'Nationality is required.',
                'danger'
            )
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )
            
        # ==========================================================
        # ATHLETE PERSONAL INFORMATION VALIDATION
        # ==========================================================

        try:
            age = int(age_raw)
        except (TypeError, ValueError):
            flash('Please enter a valid age.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        try:
            date_of_birth = datetime.strptime(
                date_of_birth_raw,
                '%Y-%m-%d'
            ).date()
        except (TypeError, ValueError):
            flash('Please enter a valid Date of Birth.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        today = datetime.utcnow().date()

        if date_of_birth > today:
            flash('Date of Birth cannot be in the future.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        calculated_age = (
            today.year
            - date_of_birth.year
            - (
                (today.month, today.day)
                < (date_of_birth.month, date_of_birth.day)
            )
        )

        if age != calculated_age:
            flash(
                'Age does not match Date of Birth. '
                'Please enter the correct Age and Date of Birth.',
                'danger'
            )
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        if not nationality:
            flash('Nationality is required.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        try:
            height_cm = float(height_cm_raw)
        except (TypeError, ValueError):
            flash('Please enter a valid height in centimeters.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        try:
            weight_kg = float(weight_kg_raw)
        except (TypeError, ValueError):
            flash('Please enter a valid weight in kilograms.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        if height_cm <= 0:
            flash('Height must be greater than 0 cm.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        if weight_kg <= 0:
            flash('Weight must be greater than 0 kg.', 'danger')
            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )
        # ==========================================================
        # FIND EXISTING ACCOUNT BY EMAIL
        # ==========================================================

        existing = User.query.filter_by(email=email).first()

        # ==========================================================
        # EXISTING ACCOUNT VALIDATION
        # ==========================================================

        if existing:

            # Only student accounts can use athlete registration
            if existing.role != 'Athlete':
                flash(
                    'This email address is already registered to another account.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            # Name and school must match the existing account
            if (
                existing.full_name.strip().lower() != full_name.lower()
                or existing.school.strip().lower() != school.lower()
            ):
                flash(
                    'The name and school must match your existing account.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            # Existing account password must be confirmed
            if not existing.check_password(password):
                flash(
                    'The password does not match your existing account.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            # ======================================================
            # EXISTING USER MUST BE REGISTERED AS ALL ATHLETE
            # BEFORE ADDING ANOTHER CATEGORY
            # ======================================================

            current_year = datetime.utcnow().year

            all_athlete_registration = Registration.query.filter_by(
                user_id=existing.id,
                registration_type='athlete',
                category='All Athlete',
                registration_year=current_year,
                status='active'
            ).first()

            if not all_athlete_registration:

                flash(
                    'Your account is not registered as All Athlete. '
                    'Please go to My Profile and update your registration '
                    'category to All Athlete before registering for another category.',
                    'danger'
                )

                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            user = existing

        else:

            # ======================================================
            # NEW USER — PREVENT DUPLICATE NAME + SCHOOL
            # ======================================================

            duplicate_name_school = User.query.filter_by(
                full_name=full_name,
                school=school
            ).first()

            if duplicate_name_school:
                flash(
                    'A student with this name and school already exists.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            user = None

        # ==========================================================
        # ID DOCUMENT HANDLING
        #
        # RULE C:
        # - Existing + verified ID = reuse existing ID
        # - Existing + unverified ID = require new ID upload
        # - New user = require ID upload
        # ==========================================================

        filename = None

        

        if existing and existing.is_verified:

            # Existing verified ID is reused.
            filename = existing.id_document

        else:

            # New user or existing unverified user must provide ID.
            file = request.files.get('id_document')

            if not file or file.filename == '':
                flash(
                    'Student ID or Passport is required.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            if not allowed_file(file.filename):
                flash(
                    'Only PNG, JPG, JPEG or PDF files are allowed.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            actual_file_type = valid_file_content(file)

            if not actual_file_type:
                flash(
                    'The uploaded file is invalid or does not match its file type.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            extension = file.filename.rsplit('.', 1)[1].lower()

            if extension in ['jpg', 'jpeg'] and actual_file_type != 'jpg':
                flash(
                    'The uploaded image is not a valid JPEG file.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            if extension == 'png' and actual_file_type != 'png':
                flash(
                    'The uploaded image is not a valid PNG file.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            if extension == 'pdf' and actual_file_type != 'pdf':
                flash(
                    'The uploaded document is not a valid PDF file.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

            # ======================================================
            # SUPABASE ID UPLOAD
            # ======================================================

            filename = secure_filename(file.filename)

            storage_path = (
                f"students/{uuid.uuid4().hex}_{filename}"
            )

            try:
                upload_to_supabase(
                    file,
                    "id-documents",
                    storage_path
                )

            except Exception as e:
                print(
                    "Supabase ID document upload error:",
                    e
                )

                flash(
                    'There was a problem uploading your ID document. '
                    'Please try again.',
                    'danger'
                )

                return redirect(
                    url_for(
                        'register',
                        registration_category=registration_category
                    )
                )

        # ==========================================================
        # CREATE NEW USER
        # ==========================================================

        if not existing:

            user = User(
    full_name=full_name,
    school=school,
    gender=gender,
    age=age,
    date_of_birth=date_of_birth,
    nationality=nationality,
    height_cm=height_cm,
    weight_kg=weight_kg,
    email=email,
    id_document=storage_path,
    role='Athlete',
    athlete_category=selected_category
)

            user.set_password(password)
            attach_google_signup(user)
            db.session.add(user)
            db.session.flush()
            record_account_created(user)

        else: 

            # Existing unverified user uploaded a new ID.
            # Keep the account's existing password/name/school.
            if not existing.is_verified and filename:
                existing.id_document = storage_path

        # ==========================================================
        # UPDATE EXISTING ATHLETE PERSONAL INFORMATION
        # ==========================================================
            existing.gender = gender
            existing.age = age
            existing.date_of_birth = date_of_birth
            existing.nationality = nationality
            existing.height_cm = height_cm
            existing.weight_kg = weight_kg

        # ==========================================================
        # CHECK FOR DUPLICATE CATEGORY REGISTRATION
        # ==========================================================

        current_year = datetime.utcnow().year

        existing_registration = Registration.query.filter_by(
            user_id=user.id,
            registration_type='athlete',
            category=selected_category,
            registration_year=current_year
        ).first()

        if existing_registration:

            flash(
                'You are already registered for this athlete category '
                'for this year.',
                'danger'
            )

            return redirect(
                url_for(
                    'register',
                    registration_category=registration_category
                )
            )

        # ==========================================================
        # CREATE REGISTRATION
        # ==========================================================

        registration = Registration(
            user_id=user.id,
            registration_type='athlete',
            category=selected_category,
            registration_year=current_year,
            fee_amount=0,
            payment_status='unpaid',
            status='active'
        )

        db.session.add(registration)

        db.session.commit()

        # ==========================================================
        # SUCCESS MESSAGE
        # ==========================================================

        if existing:
            flash(
                f'You have successfully registered for '
                f'{selected_category}.',
                'success'
            )
        else:
            flash(
                'Registration successful! Wait for admin verification '
                'of your ID.',
                'success'
            )

        return redirect(url_for('login'))

    return render_template(
        'register.html',
        registration_category=registration_category,
        selected_category=selected_category
    )

#=====Login route=========================
# ==== forgot password route =========================
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        # Rate-limit by IP address
        ip_identifier = f"forgot-ip:{get_client_ip()}"

        if is_rate_limited(
            ip_identifier,
            MAX_FORGOT_ATTEMPTS,
            FORGOT_BLOCK_MINUTES
        ):
            flash(
                'Too many password reset requests. '
                'Please try again later.',
                'warning'
            )
            return redirect(url_for('forgot_password'))

        # Rate-limit by email address
        email_identifier = f"forgot-email:{email}"

        if is_rate_limited(
            email_identifier,
            MAX_FORGOT_EMAIL_ATTEMPTS,
            FORGOT_EMAIL_BLOCK_MINUTES
        ):
            flash(
                'Too many password reset requests. '
                'Please try again later.',
                'warning'
            )
            return redirect(url_for('forgot_password'))

        # Count this reset request
        record_failed_attempt(
            ip_identifier,
            MAX_FORGOT_ATTEMPTS,
            FORGOT_BLOCK_MINUTES
        )

        record_failed_attempt(
            email_identifier,
            MAX_FORGOT_EMAIL_ATTEMPTS,
            FORGOT_EMAIL_BLOCK_MINUTES
        )

        # Always show the same message whether the email exists or not.
        # This prevents account enumeration.
        user = User.query.filter_by(email=email).first()

        if user:
            # Generate a secure, unpredictable reset token
            user.reset_token = secrets.token_urlsafe(48)

            # Token expires after 1 hour
            from datetime import timedelta

            user.reset_token_expires = (
                datetime.utcnow() + timedelta(hours=1)
            )

            db.session.commit()

            # Build the password reset link
            reset_link = url_for(
                'reset_password',
                token=user.reset_token,
                _external=True
            )

            # Send the reset email
            try:
                msg = Message(
                    subject='Athlete Tracker - Password Reset',
                    sender=app.config['MAIL_DEFAULT_SENDER'],
                    recipients=[user.email]
                )

                msg.body = (
                    f'Hello {user.full_name},\n\n'
                    'We received a request to reset your Athlete Tracker '
                    'password.\n\n'
                    'Click the link below to reset your password:\n\n'
                    f'{reset_link}\n\n'
                    'This link will expire in 1 hour.\n\n'
                    'If you did not request a password reset, you can '
                    'safely ignore this email.\n\n'
                    'Athlete Tracker'
                )

                mail.send(msg)

            except Exception as e:
                # Do not leave a usable reset token behind
                # if the email could not be sent.
                user.reset_token = None
                user.reset_token_expires = None
                db.session.commit()

                print("PASSWORD RESET EMAIL ERROR:", e)

        flash(
            'If an account exists for that email address, '
            'a password reset link has been sent.',
            'info'
        )

        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')

# ==reset password route=========================
@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    # Find the user associated with this reset token
    user = User.query.filter_by(reset_token=token).first()

    # Reject invalid or expired tokens
    if not user or not user.reset_token_expires:
        flash(
            'This password reset link is invalid or has expired.',
            'danger'
        )
        return redirect(url_for('forgot_password'))

    if datetime.utcnow() > user.reset_token_expires:
        # Clear the expired token
        user.reset_token = None
        user.reset_token_expires = None
        db.session.commit()

        flash(
            'This password reset link has expired. '
            'Please request a new one.',
            'danger'
        )
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Check password strength
        if not is_strong_password(new_password):
            flash(
                'Password must be at least 10 characters and include '
                'an uppercase letter, lowercase letter, number, '
                'and special character.',
                'danger'
            )
            return render_template(
                'reset_password.html',
                token=token
            )

        # Confirm both passwords match
        if new_password != confirm_password:
            flash(
                'Passwords do not match.',
                'danger'
            )
            return render_template(
                'reset_password.html',
                token=token
            )

        # Set the new password
        user.set_password(new_password)

        # Make the reset token single-use
        user.reset_token = None
        user.reset_token_expires = None

        db.session.commit()

        flash(
            'Your password has been reset successfully. '
            'Please log in with your new password.',
            'success'
        )

        return redirect(url_for('login'))

    return render_template(
        'reset_password.html',
        token=token
    )

def safe_next_url(target):
    """
    Return `target` only if it is a path on this site, otherwise None.

    Protects the ?next= redirect after login (e.g. from an app shortcut
    to /inbox) from being used to send users to another website.
    """
    if not target:
        return None

    target = target.strip()

    # Must be a site-relative path. Reject protocol-relative ("//evil.com"),
    # backslash tricks ("/\\evil.com") and header-injection characters.
    if (
        not target.startswith('/')
        or target.startswith('//')
        or '\\' in target
        or '\r' in target
        or '\n' in target
    ):
        return None

    parsed = urlsplit(target)

    if parsed.scheme or parsed.netloc:
        return None

    return target


def redirect_after_login(default_endpoint):
    """Go to the page the user originally asked for, else their dashboard."""
    next_url = safe_next_url(session.pop('login_next', None))
    return redirect(next_url or url_for(default_endpoint))


# Roles that must use two-factor authentication (authenticator app).
TWO_FACTOR_ROLES = ('Coach', 'System', 'Scout', 'Organization')


def dashboard_endpoint_for(user):
    """The dashboard a signed-in user lands on (see ROLE_DASHBOARDS)."""
    return ROLE_DASHBOARDS.get(user.role, ('index',))[0]


def continue_login(user):
    """
    Finish signing in a user whose identity is confirmed (password or
    Google): approval checks, 2FA (Coach / System / Scout) and role-based
    redirects.
    Shared by the password login and Google sign-in so both follow exactly
    the same rules.
    """
    # Make the authenticated session permanent
    session.permanent = True

    # Coaches/admins must be verified before continuing
    if user.role == 'Coach' and not user.is_verified:
        flash(
            'Your coach account is waiting for Super Admin approval.',
            'warning'
        )
        return redirect(url_for('login'))

    # Scouts must be verified before they can access the
    # Scout dashboard.
    if user.role == 'Scout' and not user.is_verified:
        flash(
            'Your Scout account is pending D.A.R.T. administrator verification.',
            'warning'
        )
        return redirect(url_for('login'))

    # Organizations must be verified by the Super Admin first.
    if user.role == 'Organization' and not user.is_verified:
        flash(
            'Your organization is waiting for D.A.R.T. verification. We will '
            'review your document, usually within 2 working days.',
            'warning'
        )
        return redirect(url_for('login'))

    # Coaches, admins, scouts and organizations must complete 2FA before being logged in
    if user.role in TWO_FACTOR_ROLES:

        # If 2FA has not been enabled yet, require setup first
        if not user.two_factor_enabled:
            session['2fa_setup_user_id'] = user.id
            flash(
                'Please set up two-factor authentication before continuing.',
                'warning'
            )
            return redirect(url_for('setup_2fa'))

        # Store the user temporarily until the 2FA code is verified
        session['2fa_user_id'] = user.id

        return redirect(url_for('verify_2fa'))

    # Athletes do not require 2FA.
    if user.role == 'Athlete':
        login_user(user)
        return redirect_after_login('student_dashboard')

    # Safety fallback — do not allow unknown roles to authenticate
    # into another role's dashboard.
    flash(
        'Your account role is not authorized for application access.',
        'danger'
    )
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    # Remember where the user was going (Flask-Login adds ?next= when a
    # logged-out user opens a protected page). The login form posts back
    # to this same URL, so ?next= is available on POST too.
    requested_next = safe_next_url(request.args.get('next'))

    if requested_next:
        session['login_next'] = requested_next
    elif request.method == 'GET':
        session.pop('login_next', None)

    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        password = request.form['password']

        # Rate-limit identifiers
        identifier = f"login:{full_name.lower()}"
        ip_identifier = f"ip:{get_client_ip()}"

        # Check whether this IP address is temporarily blocked
        if is_rate_limited(
            ip_identifier,
            10,
            15
        ):
            flash(
                'Too many login attempts from this network. '
                'Please try again in 15 minutes.',
                'danger'
            )
            return redirect(url_for('login'))

        # Check whether this account is temporarily blocked
        if is_rate_limited(
            identifier,
            MAX_LOGIN_ATTEMPTS,
            LOGIN_BLOCK_MINUTES
        ):
            flash(
                'Too many failed login attempts. '
                'Please try again in 15 minutes.',
                'danger'
            )
            return redirect(url_for('login'))

        user = User.query.filter_by(full_name=full_name).first()

        # Invalid username or password
        if not user or not user.check_password(password):
            # Record the failed attempt against the account
            record_failed_attempt(
                identifier,
                MAX_LOGIN_ATTEMPTS,
                LOGIN_BLOCK_MINUTES
            )

            # Also record the failed attempt against the IP address
            record_failed_attempt(
                ip_identifier,
                10,
                15
            )

            flash('Invalid full name or password.', 'danger')
            return redirect(url_for('login'))

        # Password was correct — reset failed login attempts
        reset_failed_attempts(identifier)
        reset_failed_attempts(ip_identifier)
        return continue_login(user)

    return render_template('login.html')

# ==========================================================
# GOOGLE SIGN-IN (OpenID Connect via Authlib)
# ==========================================================
# Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (Google Cloud Console →
# APIs & Services → Credentials → OAuth client ID, type "Web application").
# Authorised redirect URI: https://<your-domain>/auth/google/callback
# Until both variables are set, the Google buttons show a "coming soon" message.
#
# - A Google account already linked to a D.A.R.T. account signs straight
#   in, through the same rules as a password login (approval checks and
#   Coach/System 2FA).
# - If an account already uses the same email, the owner must log in with
#   their password once to connect Google. Registration does not verify
#   email addresses, so linking automatically would let someone who
#   registered with another person's email take over their Google sign-in.
# - A new Google user completes the normal registration form (name and
#   verified email pre-filled); Google is linked when the account is created.

oauth = OAuth(app)

GOOGLE_SIGN_IN_ENABLED = bool(
    os.environ.get('GOOGLE_CLIENT_ID')
    and os.environ.get('GOOGLE_CLIENT_SECRET')
)

if GOOGLE_SIGN_IN_ENABLED:
    oauth.register(
        name='google',
        client_id=os.environ.get('GOOGLE_CLIENT_ID'),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

app.jinja_env.globals['google_sign_in_enabled'] = GOOGLE_SIGN_IN_ENABLED

# How long a Google sign-in waits for the user to finish registering or
# to log in with their password to connect Google.
GOOGLE_PENDING_SECONDS = 15 * 60


def _google_redirect_uri():
    configured = os.environ.get('GOOGLE_REDIRECT_URI')

    if configured:
        return configured

    host = request.host.split(':')[0]
    scheme = 'http' if host in ('localhost', '127.0.0.1') else 'https'

    return url_for('google_callback', _external=True, _scheme=scheme)


def pending_google(key):
    """A Google identity waiting in the session ('google_signup' or 'google_link')."""
    data = session.get(key)

    if not data:
        return None

    if time.time() - data.get('at', 0) > GOOGLE_PENDING_SECONDS:
        session.pop(key, None)
        return None

    return data


def attach_google_signup(user):
    """Link Google to a new account when registration started with Google."""
    pending = pending_google('google_signup')

    if (
        pending
        and (user.email or '').lower() == pending['email']
        and not User.query.filter_by(google_sub=pending['sub']).first()
    ):
        user.google_sub = pending['sub']
        session.pop('google_signup', None)


@user_logged_in.connect_via(app)
def link_pending_google_account(sender, user, **extra):
    """Connect Google after the account owner logs in with their password."""
    pending = pending_google('google_link')

    if not pending:
        return

    session.pop('google_link', None)

    if (
        user.google_sub
        or (user.email or '').lower() != pending['email']
        or User.query.filter_by(google_sub=pending['sub']).first()
    ):
        return

    user.google_sub = pending['sub']
    db.session.commit()

    flash('Google sign-in is now connected to your account.', 'success')


# ==========================================================
# CELEBRATIONS (party poppers on first login)
# ==========================================================
# - New account: registration records an 'account_created' audit event.
#   The first login after that shows the welcome celebration once.
#   Accounts created before this feature have no such event, so existing
#   users are not shown it.
# - Premium: each subscription that is active is celebrated once, on the
#   next login after it became active.
# Celebrations already shown are recorded in the audit log, so they never
# repeat (even on another device).

def queue_celebrations(user):
    """Work out which celebrations this login should show (at most once each)."""
    shown = []

    def already(action, target_type, target_id):
        return AuditLog.query.filter_by(
            actor_user_id=user.id,
            action=action,
            target_type=target_type,
            target_id=str(target_id)
        ).first() is not None

    if (
        already('account_created', 'User', user.id)
        and not already('welcome_celebrated', 'User', user.id)
    ):
        create_audit_log(
            action='welcome_celebrated',
            actor_user_id=user.id,
            target_type='User',
            target_id=str(user.id)
        )
        shown.append('welcome')

    active_subscriptions = Subscription.query.filter_by(
        user_id=user.id,
        status='active'
    ).all()

    for subscription in active_subscriptions:
        if not already('premium_celebrated', 'Subscription', subscription.id):
            create_audit_log(
                action='premium_celebrated',
                actor_user_id=user.id,
                target_type='Subscription',
                target_id=str(subscription.id)
            )
            if 'premium' not in shown:
                shown.append('premium')

    return shown


@user_logged_in.connect_via(app)
def celebrate_on_login(sender, user, **extra):
    try:
        celebrations = queue_celebrations(user)
        if celebrations:
            db.session.commit()
            session['celebrations'] = celebrations
    except Exception:
        # A celebration must never stop someone from logging in.
        db.session.rollback()
        app.logger.exception('Could not queue login celebration')


def pop_celebration():
    """Used once by base.html: the celebration to show on this page, if any."""
    celebrations = session.pop('celebrations', None) or []

    if not celebrations or not current_user.is_authenticated:
        return None

    first_name = (current_user.full_name or '').split(' ')[0]
    role_lines = {
        'Athlete': 'Your account is ready. Start building your sports CV.',
        'Coach': "Your coach account is ready. Manage and verify your team's records.",
        'Scout': 'Your scout account is ready. Start discovering athletes.',
    }

    # One-time id: the page script skips it if this exact celebration was
    # already played (e.g. the page is shown again from the offline cache).
    token = uuid.uuid4().hex

    if 'premium' in celebrations:
        return {
            'id': token,
            'kind': 'premium',
            'title': f'You\'re Premium, {first_name}!',
            'message': 'Your premium features are now unlocked. Enjoy!',
        }

    return {
        'id': token,
        'kind': 'welcome',
        'title': f'Welcome to D.A.R.T., {first_name}!',
        'message': role_lines.get(current_user.role, 'Your account is ready.'),
    }


app.jinja_env.globals['pop_celebration'] = pop_celebration


def record_account_created(user):
    """Mark a brand-new account so its first login is celebrated."""
    create_audit_log(
        action='account_created',
        actor_user_id=user.id,
        target_type='User',
        target_id=str(user.id),
        details={'role': user.role}
    )


# ==========================================================
# PLANS IN TEMPLATES + GOOGLE ADSENSE
# ==========================================================
# Ads appear only for visitors and free users, and only once the AdSense
# publisher ID is set: ADSENSE_CLIENT_ID=ca-pub-XXXXXXXXXXXXXXXX
# (optional: ADSENSE_SLOT_ID for a specific ad unit).

ADSENSE_CLIENT_ID = (os.environ.get('ADSENSE_CLIENT_ID') or '').strip()
ADSENSE_SLOT_ID = (os.environ.get('ADSENSE_SLOT_ID') or '').strip()

# ---------- SEO ----------
# SITE_URL: the public address, e.g. https://dart.example.com (used for
# canonical links and the sitemap). GOOGLE_SITE_VERIFICATION: the code
# from Google Search Console (HTML tag method).
SITE_URL = (os.environ.get('SITE_URL') or '').strip().rstrip('/')
GOOGLE_SITE_VERIFICATION = (os.environ.get('GOOGLE_SITE_VERIFICATION') or '').strip()

# Public pages search engines may index. Everything else is noindex;
# athlete profiles and shared records decide per athlete (seo_indexable).
SEO_INDEX_ENDPOINTS = {
    'index', 'pricing', 'register', 'register_coach', 'register_scout',
    'register_organization', 'privacy_page', 'terms_page', 'cookies_page',
    'legal_page', 'organization_terms',
}


def site_url(path='/'):
    base = SITE_URL or request.url_root.rstrip('/')
    return base + path


app.jinja_env.globals.update(
    site_url=site_url,
    seo_index_endpoints=SEO_INDEX_ENDPOINTS,
    google_site_verification=GOOGLE_SITE_VERIFICATION,
)


@app.context_processor
def inject_plan_context():
    plan = current_plan(current_user) if current_user.is_authenticated else None

    return {
        'my_plan': plan,
        'my_subscription': active_subscription(current_user) if plan else None,
        'my_eligible_plan': plan_for_user(current_user) if current_user.is_authenticated else None,
        'plans': PLANS,
        'show_ads': bool(ADSENSE_CLIENT_ID) and shows_ads(current_user),
        'adsense_client': ADSENSE_CLIENT_ID,
        'adsense_slot': ADSENSE_SLOT_ID,
        'upload_usage': lambda: usage_summary(current_user),
    }


@app.route('/ads.txt')
def ads_txt():
    """Authorizes Google to sell ads on this site (required by AdSense)."""
    if not ADSENSE_CLIENT_ID.startswith('ca-pub-'):
        abort(404)

    publisher = ADSENSE_CLIENT_ID.replace('ca-', '', 1)
    response = make_response(f'google.com, {publisher}, DIRECT, f08c47fec0942fa0\n')
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    return response


@app.context_processor
def inject_google_signup():
    if current_user.is_authenticated:
        return {'google_signup': None}

    return {'google_signup': pending_google('google_signup')}


@app.route('/auth/google')
def google_login():
    if not GOOGLE_SIGN_IN_ENABLED:
        flash('Google sign-in is coming soon. Please use the form for now.', 'info')
        return redirect(url_for('login'))

    if current_user.is_authenticated:
        return redirect(url_for('index'))

    return oauth.google.authorize_redirect(
        _google_redirect_uri(),
        prompt='select_account'
    )


@app.route('/auth/google/callback')
def google_callback():
    if not GOOGLE_SIGN_IN_ENABLED:
        return redirect(url_for('login'))

    try:
        # Checks state and nonce and validates Google's signed ID token.
        token = oauth.google.authorize_access_token()
    except Exception as error:
        app.logger.warning('Google sign-in failed: %s', error)
        flash('Google sign-in was cancelled or failed. Please try again.', 'danger')
        return redirect(url_for('login'))

    info = token.get('userinfo') or {}
    google_sub = info.get('sub')
    email = (info.get('email') or '').strip().lower()

    if not google_sub or not email or info.get('email_verified') is not True:
        flash(
            'Google sign-in needs a Google account with a verified email address.',
            'danger'
        )
        return redirect(url_for('login'))

    session.pop('google_signup', None)
    session.pop('google_link', None)

    # 1. Google already connected to an account.
    user = User.query.filter_by(google_sub=google_sub).first()

    if user:
        return continue_login(user)

    # 2. An account already uses this email: owner confirms with password.
    if User.query.filter(func.lower(User.email) == email).first():
        session['google_link'] = {
            'sub': google_sub,
            'email': email,
            'at': time.time()
        }
        flash(
            'A D.A.R.T. account already uses this email. Log in with your '
            'full name and password once to connect Google sign-in.',
            'info'
        )
        return redirect(url_for('login'))

    # 3. New user: complete the normal registration.
    session['google_signup'] = {
        'sub': google_sub,
        'email': email,
        'name': (info.get('name') or '').strip()[:150],
        'at': time.time()
    }
    return redirect(url_for('google_signup'))


@app.route('/auth/google/signup')
def google_signup():
    pending = pending_google('google_signup')

    if not pending:
        flash('Please continue with Google again.', 'warning')
        return redirect(url_for('login'))

    return render_template('google_signup.html', pending=pending)


@app.route('/auth/google/signup/cancel')
def google_signup_cancel():
    session.pop('google_signup', None)
    flash('Google sign-up cancelled.', 'info')
    return redirect(url_for('login'))

#=============logout route=====================
@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

from flask import send_from_directory

#=========================unread count helper========================
#=========================unread count helper========================
@app.context_processor
def inject_unread_message_count():

    if (
        current_user.is_authenticated
        and current_user.role in (
            'Scout',
            'Athlete',
            'Coach'
        )
    ):

        unread_count = ChatMessage.query.filter_by(
            recipient_id=current_user.id,
            is_read=False
        ).count()

    else:
        unread_count = 0

    return {
        'unread_message_count': unread_count
    }


# ==========================================================
# NAVIGATION: ROLE → DASHBOARD
# ==========================================================
# Used by the navbar and the mobile bottom tab bar so every role
# lands on the dashboard it is actually allowed to open.
ROLE_DASHBOARDS = {
    'Athlete': ('student_dashboard', 'My Records', 'Records', 'fa-solid fa-book'),
    'Scout': ('scout_dashboard', 'Scout Dashboard', 'Scout', 'fa-solid fa-binoculars'),
    'Coach': ('admin_dashboard', 'Dashboard', 'Dashboard', 'fa-solid fa-gauge-high'),
    'System': ('admin_dashboard', 'Admin', 'Admin', 'fa-solid fa-user-shield'),
    'Organization': ('organization_dashboard', 'Team HQ', 'Team', 'fa-solid fa-building-shield'),
}


@app.context_processor
def inject_dashboard_nav():
    dashboard = None

    if current_user.is_authenticated and current_user.role in ROLE_DASHBOARDS:
        endpoint, label, short_label, icon = ROLE_DASHBOARDS[current_user.role]
        dashboard = {
            'endpoint': endpoint,
            'label': label,
            'short_label': short_label,
            'icon': icon,
        }

    return {
        'dashboard_nav': dashboard
    }
# ==========================================================
# INBOX / MESSAGING
# ==========================================================

@app.route('/inbox')
@login_required
def inbox():

    # Only Scout, Athlete, and Coach accounts can use messaging.
    if current_user.role not in ('Scout', 'Athlete', 'Coach'):
        flash('Messaging is not available for this account.', 'danger')
        return redirect(url_for('index'))

    messages = ChatMessage.query.filter(
        or_(
            ChatMessage.sender_id == current_user.id,
            ChatMessage.recipient_id == current_user.id
        )
    ).order_by(
        ChatMessage.created_at.desc()
    ).all()

    threads = {}

    for message in messages:

        if message.sender_id == current_user.id:
            other_user_id = message.recipient_id
        else:
            other_user_id = message.sender_id

        other_user = User.query.get(other_user_id)

        if not other_user:
            continue

        # Do not display conversations the current account
        # is not permitted to participate in.
        if not can_message(current_user, other_user):
            continue

        if other_user_id not in threads:
            threads[other_user_id] = {
                'user': other_user,
                'latest_message': message,
                'unread_count': 0
            }

        # Count unread messages received by the current user.
        if (
            message.recipient_id == current_user.id
            and not message.is_read
        ):
            threads[other_user_id]['unread_count'] += 1

    conversations = list(threads.values())

    return render_template(
        'inbox.html',
        conversations=conversations
    )

# ==========================================================
# NEW MESSAGE / START CONVERSATION
# ==========================================================
@app.route('/new-message', methods=['GET', 'POST'])
@login_required
def new_message():

    print("========== NEW MESSAGE DEBUG ==========")
    print("Sender:", current_user.id, current_user.full_name)
    print("Sender role:", current_user.role)
    print("Submitted to_name:", request.form.get('to_name'))
    print("Submitted to_user_id:", request.form.get('to_user_id'))
    print("Message:", request.form.get('body'))
    print("======================================")

    # ==========================================================
    # ONLY COACH, ATHLETE AND SCOUT CAN USE MESSAGING
    # ==========================================================

    if current_user.role not in {
        'Coach',
        'Athlete',
        'Scout'
    }:
        flash(
            'You are not allowed to use messaging.',
            'danger'
        )

        return redirect(url_for('index'))


    # ==========================================================
    # GET
    # ==========================================================

    if request.method == 'GET':

        return render_template(
            'new_message.html'
        )


    # ==========================================================
    # POST
    # ==========================================================

    to_user_id = request.form.get(
        'to_user_id',
        ''
    ).strip()

    body = request.form.get(
        'body',
        ''
    ).strip()


    # ==========================================================
    # RECIPIENT MUST BE SELECTED FROM SUGGESTIONS
    # ==========================================================

    if not to_user_id:

        flash(
            'Please select a recipient from the suggestion list.',
            'danger'
        )

        return redirect(
            url_for('new_message')
        )


    # ==========================================================
    # CONVERT USER ID TO INTEGER
    # ==========================================================

    try:

        to_user_id = int(to_user_id)

    except (TypeError, ValueError):

        flash(
            'Invalid recipient selected.',
            'danger'
        )

        return redirect(
            url_for('new_message')
        )


    # ==========================================================
    # FIND RECIPIENT BY ID
    # ==========================================================

    recipient = User.query.get(
        to_user_id
    )


    if not recipient:

        flash(
            'The selected recipient could not be found.',
            'danger'
        )

        return redirect(
            url_for('new_message')
        )


    # ==========================================================
    # CHECK MESSAGING PERMISSION
    # ==========================================================

    if not can_message(
        current_user,
        recipient
    ):

        flash(
            'You are not allowed to message this user.',
            'danger'
        )

        return redirect(
            url_for('new_message')
        )


    # ==========================================================
    # MESSAGE VALIDATION
    # ==========================================================

    if not body:

        flash(
            'Please enter a message.',
            'danger'
        )

        return redirect(
            url_for('new_message')
        )


    if len(body) > 5000:

        flash(
            'Message cannot exceed 5000 characters.',
            'danger'
        )

        return redirect(
            url_for('new_message')
        )


    # ==========================================================
    # CREATE MESSAGE
    # ==========================================================

    message = ChatMessage(
        sender_id=current_user.id,
        recipient_id=recipient.id,
        body=body
    )

    db.session.add(message)


    # ==========================================================
    # SAVE
    # ==========================================================

    try:

        db.session.commit()

    except Exception as e:

        db.session.rollback()

        print(
            'MESSAGE SEND ERROR:',
            e
        )

        flash(
            'There was a problem sending your message.',
            'danger'
        )

        return redirect(
            url_for('new_message')
        )


    # ==========================================================
    # SUCCESS
    # ==========================================================

    flash(
        f'Message sent to {recipient.full_name}.',
        'success'
    )

    return redirect(
        url_for(
            'conversation',
            user_id=recipient.id
        )
    )

# ==========================================================
# MESSAGE RECIPIENT SEARCH
# ==========================================================

@app.route('/api/message-recipients')
@login_required
def message_recipients():

    if current_user.role not in ('Scout', 'Athlete', 'Coach'):
        return jsonify([])

    q = request.args.get('q', '').strip()

    if not q:
        return jsonify([])

    # Scout can contact Athletes and Coaches.
    if current_user.role == 'Scout':
        allowed_roles = ['Athlete', 'Coach']

    # Athletes and Coaches can contact Scouts.
    else:
        allowed_roles = ['Scout']

    users = User.query.filter(
        User.id != current_user.id,
        User.role.in_(allowed_roles),
        User.full_name.ilike(f'%{q}%')
    ).order_by(
        User.full_name.asc()
    ).limit(12).all()

    recipients = []

    for user in users:

        if not can_message(current_user, user):
            continue

        recipients.append({
            'id': user.id,
            'full_name': user.full_name,
            'role': user.role,
            'school': user.school or '',
            'nationality': user.nationality or '',
            'flag': country_flag(user.nationality)
        })

    return jsonify(recipients)
# ==========================================================
# INBOX / MESSAGING/ Convo--back2back
# ==========================================================
@app.route(
    '/conversation/<int:user_id>',
    methods=['GET', 'POST']
)
@login_required
def conversation(user_id):

    if current_user.role not in (
        'Scout',
        'Athlete',
        'Coach'
    ):
        return redirect(url_for('index'))

    recipient = User.query.get_or_404(user_id)

    if not can_message(
        current_user,
        recipient
    ):
        flash(
            'You are not allowed to message this user.',
            'danger'
        )
        return redirect(url_for('inbox'))

    if request.method == 'POST':

        body = request.form.get(
            'body',
            ''
        ).strip()

        if not body:
            flash(
                'Message cannot be empty.',
                'danger'
            )
            return redirect(
                url_for(
                    'conversation',
                    user_id=recipient.id
                )
            )

        message = ChatMessage(
            sender_id=current_user.id,
            recipient_id=recipient.id,
            body=body
        )

        db.session.add(message)
        db.session.commit()

        return redirect(
            url_for(
                'conversation',
                user_id=recipient.id
            )
        )

    ChatMessage.query.filter_by(
        sender_id=recipient.id,
        recipient_id=current_user.id,
        is_read=False
    ).update(
        {
            ChatMessage.is_read: True
        },
        synchronize_session=False
    )

    db.session.commit()

    messages = ChatMessage.query.filter(
        or_(
            (
                ChatMessage.sender_id
                == current_user.id
            )
            &
            (
                ChatMessage.recipient_id
                == recipient.id
            ),
            (
                ChatMessage.sender_id
                == recipient.id
            )
            &
            (
                ChatMessage.recipient_id
                == current_user.id
            )
        )
    ).order_by(
        ChatMessage.created_at.asc()
    ).all()

    return render_template(
        'conversation.html',
        recipient=recipient,
        messages=messages
    )

# ==========================================================
# ATHLETE HIGHLIGHTS (photos + videos up to 60 seconds)
# ==========================================================
# Files go straight from the athlete's browser to Supabase Storage with a
# one-time signed upload URL (videos are too big to pass through Vercel,
# which limits requests to 4.5 MB). Afterwards the server checks the
# stored file itself (type, size and, for videos, the length written in
# the MP4/MOV file) before the highlight is saved.
#
# Free for verified athletes for now. When Premium starts, restrict
# uploads in can_upload_highlights() (e.g. with has_entitlement()).

HIGHLIGHTS_BUCKET = 'athlete-highlights'

# content type -> (media type, file extension)
HIGHLIGHT_TYPES = {
    'image/jpeg': ('photo', 'jpg'),
    'image/png': ('photo', 'png'),
    'image/webp': ('photo', 'webp'),
    'video/mp4': ('video', 'mp4'),
    'video/quicktime': ('video', 'mov'),
}

MB = 1024 * 1024
HIGHLIGHT_PHOTO_MAX_BYTES = 10 * MB
# Supabase Free plan: 50 MB per file. On a paid plan raise the bucket's
# file_size_limit and set HIGHLIGHT_VIDEO_MAX_MB to the same value.
HIGHLIGHT_VIDEO_MAX_BYTES = int(os.environ.get('HIGHLIGHT_VIDEO_MAX_MB', '50')) * MB
HIGHLIGHT_VIDEO_MAX_SECONDS = 60
# Small allowance for rounding in phone recordings (60.4 s shows as 1:00).
HIGHLIGHT_DURATION_TOLERANCE = 0.5
HIGHLIGHT_CAPTION_MAX = 150
HIGHLIGHT_PENDING_SECONDS = 2 * 60 * 60


def highlight_limit_error(user, media_type):
    """Monthly photo / video limit of the athlete's plan (plans.py)."""
    return limit_reached(user, 'videos' if media_type == 'video' else 'photos')


def can_upload_highlights(user):
    """Verified athletes; how many per month depends on their plan."""
    return (
        user.is_authenticated
        and user.role == 'Athlete'
        and bool(user.is_verified)
    )


def highlights_for(user_id):
    return AthleteHighlight.query.filter_by(
        user_id=user_id
    ).order_by(
        AthleteHighlight.created_at.desc(),
        AthleteHighlight.id.desc()
    ).all()


def highlight_public_url(path):
    result = supabase.storage.from_(HIGHLIGHTS_BUCKET).get_public_url(path)

    if isinstance(result, dict):
        result = result.get('publicUrl') or result.get('public_url')

    # get_public_url can add a trailing "?" with no parameters.
    return (result or '').rstrip('?')


app.jinja_env.globals.update(
    highlight_url=highlight_public_url,
    highlight_photo_max_bytes=HIGHLIGHT_PHOTO_MAX_BYTES,
    highlight_video_max_bytes=HIGHLIGHT_VIDEO_MAX_BYTES,
    highlight_video_max_seconds=HIGHLIGHT_VIDEO_MAX_SECONDS,
)


def storage_object_info(path):
    """(size in bytes, content type) of a stored highlight, or None if missing."""
    response = http_requests.head(highlight_public_url(path), timeout=10, allow_redirects=True)

    if response.status_code != 200:
        return None

    size = response.headers.get('Content-Length')
    content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()

    return (int(size) if size and size.isdigit() else None), content_type


def read_storage_range(path, start, length):
    """Read `length` bytes from `start` of a stored highlight (HTTP Range)."""
    response = http_requests.get(
        highlight_public_url(path),
        headers={'Range': f'bytes={start}-{start + length - 1}'},
        timeout=10,
        stream=True
    )

    try:
        if response.status_code == 206:
            return response.content[:length]

        if response.status_code == 200:
            # Range ignored: read only what we need from the start.
            data = b''
            for chunk in response.iter_content(64 * 1024):
                data += chunk
                if len(data) >= start + length:
                    break
            return data[start:start + length]

        return b''
    finally:
        response.close()


def parse_mp4_duration(read_at, total_size):
    """
    Length in seconds of an MP4 / MOV (QuickTime) file, read from its
    'moov' → 'mvhd' header. read_at(offset, length) returns bytes.
    Returns None if the length can't be found.
    """
    def boxes(start, end):
        offset = start
        for _ in range(200):
            if offset + 8 > end:
                return
            header = read_at(offset, 16)
            if len(header) < 8:
                return
            size = int.from_bytes(header[0:4], 'big')
            box_type = header[4:8]
            header_size = 8
            if size == 1:
                if len(header) < 16:
                    return
                size = int.from_bytes(header[8:16], 'big')
                header_size = 16
            elif size == 0:
                size = end - offset
            if size < header_size:
                return
            yield box_type, offset, header_size, size
            offset += size

    for box_type, offset, header_size, size in boxes(0, total_size):
        if box_type != b'moov':
            continue

        for child_type, child_offset, child_header, child_size in boxes(
            offset + header_size, offset + size
        ):
            if child_type != b'mvhd':
                continue

            body = read_at(child_offset + child_header, 32)
            if len(body) < 20:
                return None

            if body[0] == 1:
                timescale = int.from_bytes(body[20:24], 'big')
                duration = int.from_bytes(body[24:32], 'big')
            else:
                timescale = int.from_bytes(body[12:16], 'big')
                duration = int.from_bytes(body[16:20], 'big')

            if not timescale:
                return None

            return duration / timescale

        return None

    return None


def remove_highlight_file(path):
    try:
        supabase.storage.from_(HIGHLIGHTS_BUCKET).remove([path])
    except Exception as e:
        app.logger.warning('Could not remove highlight file %s: %s', path, e)


def highlight_error(message, status=400):
    return jsonify({'ok': False, 'error': message}), status


@app.route('/highlights/upload-url', methods=['POST'])
@login_required
def highlight_upload_url():
    """Step 1: check the file details and hand out a one-time upload URL."""
    if not can_upload_highlights(current_user):
        return highlight_error(
            'Only verified athletes can upload highlights.', 403
        )

    data = request.get_json(silent=True) or {}
    content_type = str(data.get('content_type') or '').lower()

    if content_type not in HIGHLIGHT_TYPES:
        return highlight_error(
            'Please choose a JPG, PNG or WEBP photo, or an MP4 or MOV video.'
        )

    media_type, extension = HIGHLIGHT_TYPES[content_type]

    try:
        size = int(data.get('size') or 0)
    except (TypeError, ValueError):
        size = 0

    max_bytes = HIGHLIGHT_VIDEO_MAX_BYTES if media_type == 'video' else HIGHLIGHT_PHOTO_MAX_BYTES

    if size <= 0:
        return highlight_error('This file looks empty.')

    if size > max_bytes:
        return highlight_error(
            f'{media_type.title()}s can be up to {max_bytes // MB} MB. '
            'This file is too big.'
        )

    duration = None
    if media_type == 'video':
        try:
            duration = float(data.get('duration'))
        except (TypeError, ValueError):
            duration = None

        if duration is not None and duration > HIGHLIGHT_VIDEO_MAX_SECONDS + HIGHLIGHT_DURATION_TOLERANCE:
            return highlight_error('Videos can be 1 minute long at most. Please trim it and try again.')

    limit_error = highlight_limit_error(current_user, media_type)

    if limit_error:
        return highlight_error(limit_error)

    caption = str(data.get('caption') or '').strip()[:HIGHLIGHT_CAPTION_MAX]
    path = f'{current_user.id}/{uuid.uuid4().hex}.{extension}'

    try:
        signed = supabase.storage.from_(HIGHLIGHTS_BUCKET).create_signed_upload_url(path)
        upload_url = signed.get('signed_url') or signed.get('signedUrl')
    except Exception as e:
        app.logger.exception('Highlight upload URL error: %s', e)
        upload_url = None

    if not upload_url:
        return highlight_error('Uploads are unavailable right now. Please try again later.', 503)

    # Remember what was requested; step 2 only accepts these paths.
    now = time.time()
    pending = {
        key: value
        for key, value in (session.get('pending_highlights') or {}).items()
        if now - value.get('at', 0) < HIGHLIGHT_PENDING_SECONDS
    }
    pending[path] = {
        'content_type': content_type,
        'caption': caption,
        'duration': duration,
        'at': now,
    }
    # Keep the session small.
    session['pending_highlights'] = dict(list(pending.items())[-5:])

    return jsonify({'ok': True, 'upload_url': upload_url, 'path': path})


@app.route('/highlights/complete', methods=['POST'])
@login_required
def highlight_complete():
    """Step 2: after the browser uploaded the file, check it and save it."""
    if not can_upload_highlights(current_user):
        return highlight_error('Only verified athletes can upload highlights.', 403)

    data = request.get_json(silent=True) or {}
    path = str(data.get('path') or '')
    pending_all = session.get('pending_highlights') or {}
    pending = pending_all.get(path)

    if (
        not pending
        or not path.startswith(f'{current_user.id}/')
        or time.time() - pending.get('at', 0) > HIGHLIGHT_PENDING_SECONDS
    ):
        return highlight_error('This upload has expired. Please choose the file again.')

    pending_all.pop(path, None)
    session['pending_highlights'] = pending_all

    content_type = pending['content_type']
    media_type, _ = HIGHLIGHT_TYPES[content_type]
    max_bytes = HIGHLIGHT_VIDEO_MAX_BYTES if media_type == 'video' else HIGHLIGHT_PHOTO_MAX_BYTES

    try:
        info = storage_object_info(path)
    except Exception as e:
        app.logger.exception('Highlight check error: %s', e)
        info = None

    if not info or not info[0]:
        return highlight_error('We could not find your upload. Please try again.')

    size, stored_type = info

    if stored_type and stored_type != content_type:
        remove_highlight_file(path)
        return highlight_error('The uploaded file type does not match. Please try again.')

    if size > max_bytes:
        remove_highlight_file(path)
        return highlight_error(f'{media_type.title()}s can be up to {max_bytes // MB} MB.')

    duration = None
    if media_type == 'video':
        try:
            duration = parse_mp4_duration(
                lambda offset, length: read_storage_range(path, offset, length),
                size
            )
        except Exception as e:
            app.logger.warning('Could not read video length for %s: %s', path, e)
            duration = None

        # Some recorders write 0 in the header (streamed files); fall back
        # to the length the browser measured.
        if not duration:
            duration = pending.get('duration')

        if not duration:
            remove_highlight_file(path)
            return highlight_error(
                "We couldn't read this video's length. Please upload an MP4 or MOV video."
            )

        if duration > HIGHLIGHT_VIDEO_MAX_SECONDS + HIGHLIGHT_DURATION_TOLERANCE:
            remove_highlight_file(path)
            return highlight_error('Videos can be 1 minute long at most. Please trim it and try again.')

    # Checked again: another upload may have finished in the meantime.
    limit_error = highlight_limit_error(current_user, media_type)

    if limit_error:
        remove_highlight_file(path)
        return highlight_error(limit_error)

    highlight = AthleteHighlight(
        user_id=current_user.id,
        media_type=media_type,
        storage_path=path,
        content_type=content_type,
        size_bytes=size,
        duration_seconds=round(min(duration, HIGHLIGHT_VIDEO_MAX_SECONDS), 2) if duration else None,
        caption=pending.get('caption') or None
    )
    db.session.add(highlight)
    db.session.commit()

    flash(
        'Your video highlight is live! 🎬' if media_type == 'video'
        else 'Your photo highlight is live! 📸',
        'success'
    )

    return jsonify({'ok': True, 'id': highlight.id})


@app.route('/highlights/<int:highlight_id>/delete', methods=['POST'])
@login_required
def delete_highlight(highlight_id):
    highlight = db.session.get(AthleteHighlight, highlight_id)

    # The athlete, or the Super Admin (to remove inappropriate content).
    if not highlight or (
        highlight.user_id != current_user.id and current_user.role != 'System'
    ):
        abort(404)

    remove_highlight_file(highlight.storage_path)
    db.session.delete(highlight)
    db.session.commit()

    flash('Highlight deleted.', 'success')

    return redirect(
        safe_next_url(request.form.get('next'))
        or url_for('student_dashboard')
    )


# ==========================================================
# ENTERPRISE / INSTITUTION / ACADEMY ACCOUNTS (role 'Organization')
# ==========================================================
# - Register with an official document and accept the Organization Data
#   Use Terms. Nothing is visible until the Super Admin verifies them.
# - Login needs 2FA (TWO_FACTOR_ROLES) like coaches and scouts.
# - Roster: every verified athlete whose team name (profile team or an
#   approved record's team) matches the organization's name or one of its
#   Super-Admin-approved aliases, minus athletes the organization removed.
# - Data shown: sport profile only (name, photo, age, gender, nationality,
#   physicals, shirt #, positions, approved records, highlights). Never
#   email, ID document, date of birth or messages.
# - Free: roster size + a short preview. Organization Pro: everything.

ORG_TYPES = (
    'High School', 'University', 'Club', 'Academy', 'Community / Area Team',
    'County Team', 'Federation / Association', 'Other',
)
ORG_SPORTS = ('Football', 'Basketball', 'Kickball')
ORG_TERMS_VERSION = '2026-10'
ORG_MAX_ALIASES = 10
ORG_DOCUMENT_MAX_BYTES = 5 * MB
ORG_DOCUMENT_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}


def normalize_team_name(name):
    """'  L.P.R.C.  Oilers ' -> 'lprc oilers' (case, dots and spacing ignored)."""
    name = re.sub(r"[.'’`]", '', (name or '').lower())
    name = re.sub(r'[^a-z0-9]+', ' ', name)
    return name.strip()


def clean_alias_text(text):
    aliases, seen = [], set()

    for line in (text or '').splitlines():
        alias = re.sub(r'\s+', ' ', line).strip()[:150]
        key = normalize_team_name(alias)
        if alias and key and key not in seen:
            seen.add(key)
            aliases.append(alias)

    return aliases[:ORG_MAX_ALIASES]


def organization_for(user):
    return OrganizationProfile.query.filter_by(user_id=user.id).first()


def roster_athletes(org_profile):
    """Verified athletes on this organization's roster, A-Z."""
    names = {normalize_team_name(n) for n in org_profile.roster_names if normalize_team_name(n)}

    if not names:
        return []

    excluded = {
        row.athlete_id for row in OrganizationRosterExclusion.query.filter_by(
            organization_user_id=org_profile.user_id
        )
    }

    # Compare normalized names ("L.P.R.C." == "LPRC"). Only two small
    # columns are loaded, so this stays fast for thousands of athletes.
    candidates = db.session.query(User.id, User.school).filter(
        User.role == 'Athlete',
        User.is_verified == True,
    ).all()

    record_teams = (
        db.session.query(SportRecord.user_id, SportRecord.team)
        .join(User, User.id == SportRecord.user_id)
        .filter(
            User.role == 'Athlete',
            User.is_verified == True,
            SportRecord.status == 'approved',
        )
        .distinct()
        .all()
    )

    roster_ids = {uid for uid, school in candidates if normalize_team_name(school) in names}
    roster_ids |= {uid for uid, team in record_teams if normalize_team_name(team) in names}
    roster_ids -= excluded

    if not roster_ids:
        return []

    return User.query.filter(User.id.in_(roster_ids)).order_by(func.lower(User.full_name)).all()


def organizations_listing(athlete):
    """Verified organizations whose roster includes this athlete (for transparency)."""
    teams = {normalize_team_name(athlete.school)}
    teams |= {
        normalize_team_name(team) for (team,) in db.session.query(SportRecord.team).filter(
            SportRecord.user_id == athlete.id,
            SportRecord.status == 'approved'
        ).distinct()
    }
    teams.discard('')

    excluded = {
        row.organization_user_id for row in OrganizationRosterExclusion.query.filter_by(athlete_id=athlete.id)
    }

    return [
        org for org in OrganizationProfile.query.filter_by(verification_status='verified').all()
        if org.user_id not in excluded
        and any(normalize_team_name(n) in teams for n in org.roster_names)
    ]


def require_organization():
    """Verified organization accounts only (login already required 2FA)."""
    if not current_user.is_authenticated or current_user.role != 'Organization':
        abort(403)

    org = organization_for(current_user)

    if not org or org.verification_status != 'verified' or not current_user.is_verified:
        abort(403)

    return org


def athlete_sport_profile(athlete, records, highlight_counts):
    """The only athlete data an organization ever receives."""
    approved = [r for r in records if r.status == 'approved']
    last_game = max((r.game_date for r in approved if r.game_date), default=None)

    return {
        'id': athlete.id,
        'name': athlete.full_name,
        'picture': athlete.profile_picture,
        'age': athlete.age,
        'gender': athlete.gender,
        'nationality': athlete.nationality,
        'height_cm': athlete.height_cm,
        'weight_kg': athlete.weight_kg,
        'preferred_foot': athlete.preferred_foot,
        'shirt_number': athlete.shirt_number,
        'team': athlete.school,
        'sports': sorted({r.sport for r in approved}),
        'positions': sorted({r.position for r in approved if r.position}),
        'records': len(approved),
        'last_game': last_game,
        'goals': sum(r.goals or 0 for r in approved if r.sport == 'Football'),
        'points': sum(r.points or 0 for r in approved if r.sport == 'Basketball'),
        'home_runs': sum(r.home_runs or 0 for r in approved if r.sport == 'Kickball'),
        'awards': sum((r.man_of_the_match or 0) + (r.mvp or 0) for r in approved),
        'highlights': highlight_counts.get(athlete.id, 0),
    }


def organization_dashboard_data(org, full):
    athletes = roster_athletes(org)
    data = {'roster_size': len(athletes), 'full': full}

    if not full:
        data['preview'] = [
            {'id': a.id, 'name': a.full_name, 'picture': a.profile_picture}
            for a in athletes[:FREE_ORG_ROSTER_PREVIEW]
        ]
        return data

    ids = [a.id for a in athletes]
    records = SportRecord.query.filter(SportRecord.user_id.in_(ids)).all() if ids else []
    by_athlete = {}
    for r in records:
        by_athlete.setdefault(r.user_id, []).append(r)

    highlight_counts = dict(
        db.session.query(AthleteHighlight.user_id, func.count(AthleteHighlight.id))
        .filter(AthleteHighlight.user_id.in_(ids))
        .group_by(AthleteHighlight.user_id)
        .all()
    ) if ids else {}

    roster = [athlete_sport_profile(a, by_athlete.get(a.id, []), highlight_counts) for a in athletes]
    approved = [r for r in records if r.status == 'approved']
    season = max((r.year for r in approved), default=datetime.now().year)

    leaders = []
    for sport, field, label, icon in SCOUT_LEADERBOARDS:
        totals = {}
        for r in approved:
            if r.sport == sport and r.year == season:
                totals[r.user_id] = totals.get(r.user_id, 0) + (getattr(r, field) or 0)
        top = sorted(((v, uid) for uid, v in totals.items() if v), reverse=True)[:5]
        names = {a.id: a for a in athletes}
        leaders.append({
            'sport': sport, 'label': label, 'icon': icon,
            'rows': [{'athlete': names[uid], 'total': v} for v, uid in top],
        })

    sports = []
    for sport, icon in SCOUT_SPORT_ICONS.items():
        sport_records = [r for r in approved if r.sport == sport]
        sports.append({
            'name': sport, 'icon': icon, 'records': len(sport_records),
            'athletes': len({r.user_id for r in sport_records}),
        })

    data.update({
        'roster': roster,
        'season': season,
        'kpis': {
            'athletes': len(athletes),
            'records': len(approved),
            'season_records': sum(1 for r in approved if r.year == season),
            'highlights': sum(highlight_counts.values()),
        },
        'leaders': leaders,
        'sports': sports,
        'recent': sorted(approved, key=lambda r: (r.game_date or datetime.min.date(), r.id), reverse=True)[:8],
        'positions': sorted({p for item in roster for p in item['positions']}),
    })
    return data


# ---------- Registration ----------
@app.route('/register-organization', methods=['GET', 'POST'])
def register_organization():
    if current_user.is_authenticated:
        return redirect(url_for(dashboard_endpoint_for(current_user)))

    form = request.form

    if request.method == 'POST':
        org_name = re.sub(r'\s+', ' ', form.get('org_name', '')).strip()
        org_type = form.get('org_type', '').strip()
        sports = [s for s in form.getlist('sports') if s in ORG_SPORTS]
        country = form.get('country', '').strip()
        city = form.get('city', '').strip()
        website = form.get('website', '').strip()
        aliases = clean_alias_text(form.get('aliases', ''))
        contact_name = re.sub(r'\s+', ' ', form.get('contact_name', '')).strip()
        contact_title = form.get('contact_title', '').strip()
        contact_phone = form.get('contact_phone', '').strip()
        email = form.get('email', '').strip().lower()
        password = form.get('password', '')
        confirm_password = form.get('confirm_password', '')
        document = request.files.get('document')

        def back(message):
            flash(message, 'danger')
            return render_template('register_organization.html', org_types=ORG_TYPES, org_sports=ORG_SPORTS,
                                   form=form, selected_sports=form.getlist('sports')), 400

        if not org_name or len(org_name) > 150:
            return back('Please enter your organization name (up to 150 characters).')
        if org_type not in ORG_TYPES:
            return back('Please choose your organization type.')
        if not sports:
            return back('Please choose at least one sport.')
        if not country or len(country) > 100 or len(city) > 100:
            return back('Please enter your country (and city, optional).')
        if website and (len(website) > 255 or not re.match(r'^https?://[^\s]+\.[^\s]+$', website)):
            return back('Please enter a full website address starting with https://, or leave it empty.')
        if not contact_name or len(contact_name) > 150 or not contact_title or len(contact_title) > 100:
            return back("Please enter the contact person's name and title.")
        if not PAYER_PHONE_PATTERN.match(contact_phone):
            return back('Please enter a valid contact phone number, e.g. +231 77 000 0000.')
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email) or len(email) > 255:
            return back('Please enter a valid official email address.')
        if password != confirm_password:
            return back('Passwords do not match.')
        if not is_strong_password(password):
            return back('Password must be at least 8 characters and include upper and lower case letters, a number and a symbol.')
        if form.get('accept_terms') != 'yes' or form.get('authorized') != 'yes':
            return back('Please confirm you are authorized and accept the Organization Data Use Terms.')
        if User.query.filter(func.lower(User.email) == email).first():
            return back('An account with this email address already exists.')
        # Organizations log in with their organization name.
        if User.query.filter(func.lower(User.full_name) == org_name.lower()).first():
            return back('An account with this name already exists. Please contact D.A.R.T. if this is your organization.')
        if any(
            normalize_team_name(o.org_name) == normalize_team_name(org_name)
            for o in OrganizationProfile.query.all()
        ):
            return back('This organization is already registered. Please contact D.A.R.T. if you need access.')

        if not document or not document.filename:
            return back('Please upload an official document (registration certificate or signed letterhead).')

        extension = document.filename.rsplit('.', 1)[-1].lower() if '.' in document.filename else ''
        document.stream.seek(0, os.SEEK_END)
        document_size = document.stream.tell()
        document.stream.seek(0)

        if extension not in ORG_DOCUMENT_EXTENSIONS or document_size > ORG_DOCUMENT_MAX_BYTES:
            return back('The document must be a PNG, JPG or PDF up to 5 MB.')

        document_path = f'organizations/{uuid.uuid4().hex}.{extension}'

        try:
            upload_to_supabase(document, 'id-documents', document_path)
        except Exception as e:
            app.logger.exception('Organization document upload failed: %s', e)
            return back('We could not upload your document. Please try again.')

        user = User(
            full_name=org_name,
            school=org_name,
            email=email,
            nationality=country,
            role='Organization',
            is_verified=False,
            id_document=document_path,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        db.session.add(OrganizationProfile(
            user_id=user.id,
            org_name=org_name,
            org_type=org_type,
            sports=', '.join(sports),
            country=country,
            city=city or None,
            website=website or None,
            contact_name=contact_name,
            contact_title=contact_title,
            contact_phone=contact_phone,
            document_path=document_path,
            requested_aliases='\n'.join(aliases) or None,
            verification_status='pending',
            terms_version=ORG_TERMS_VERSION,
            terms_accepted_at=datetime.utcnow(),
        ))

        # Started with "Sign up with Google": link Google to this account.
        attach_google_signup(user)
        record_account_created(user)
        create_audit_log(
            action='organization_registered',
            actor_user_id=user.id,
            target_type='User',
            target_id=str(user.id),
            details={'org_type': org_type, 'terms_version': ORG_TERMS_VERSION}
        )
        db.session.commit()

        flash(
            'Thank you! Your organization was registered. D.A.R.T. will verify your '
            'document, usually within 2 working days. You can log in with your '
            'organization name once it is approved.',
            'success'
        )
        return redirect(url_for('login'))

    return render_template('register_organization.html', org_types=ORG_TYPES, org_sports=ORG_SPORTS,
                           form={}, selected_sports=[])


# ---------- Organization dashboard ----------
@app.route('/organization')
@login_required
def organization_dashboard():
    org = require_organization()
    full = is_premium(current_user)
    data = organization_dashboard_data(org, full)

    create_audit_log(
        action='organization_roster_viewed',
        actor_user_id=current_user.id,
        target_type='OrganizationProfile',
        target_id=str(org.id),
        details={'roster_size': data['roster_size'], 'full': full}
    )
    db.session.commit()

    return render_template('organization_dashboard.html', org=org, data=data)


@app.route('/organization/roster.csv')
@login_required
def organization_roster_export():
    org = require_organization()

    if not is_premium(current_user):
        flash('Roster export is part of Organization Pro.', 'warning')
        return redirect(url_for('pricing') + '#organizations')

    data = organization_dashboard_data(org, True)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Athlete', 'Team', 'Age', 'Gender', 'Nationality', 'Height (cm)', 'Weight (kg)',
        'Preferred foot', 'Shirt #', 'Sports', 'Positions', 'Approved records',
        'Goals', 'Points', 'Home runs', 'Awards', 'Highlights', 'Last game', 'D.A.R.T. profile'
    ])

    def safe(value):
        # Stop spreadsheet formula injection.
        text = '' if value is None else str(value)
        return "'" + text if text[:1] in ('=', '+', '-', '@') else text

    for a in data['roster']:
        writer.writerow([safe(v) for v in (
            a['name'], a['team'], a['age'], a['gender'], a['nationality'], a['height_cm'],
            a['weight_kg'], a['preferred_foot'], a['shirt_number'], ', '.join(a['sports']),
            ', '.join(a['positions']), a['records'], a['goals'], a['points'], a['home_runs'],
            a['awards'], a['highlights'], a['last_game'].isoformat() if a['last_game'] else '',
            site_url(url_for('user_profile', user_id=a['id'])),
        )])

    create_audit_log(
        action='organization_roster_exported',
        actor_user_id=current_user.id,
        target_type='OrganizationProfile',
        target_id=str(org.id),
        details={'rows': len(data['roster'])}
    )
    db.session.commit()

    filename = f"{secure_filename(org.org_name) or 'roster'}-roster-{utc_today().isoformat()}.csv"
    response = make_response('﻿' + output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/organization/roster/<int:athlete_id>/remove', methods=['POST'])
@login_required
def organization_remove_athlete(athlete_id):
    org = require_organization()

    if not OrganizationRosterExclusion.query.filter_by(
        organization_user_id=current_user.id, athlete_id=athlete_id
    ).first():
        db.session.add(OrganizationRosterExclusion(organization_user_id=current_user.id, athlete_id=athlete_id))
        create_audit_log(
            action='organization_roster_athlete_removed',
            actor_user_id=current_user.id,
            target_type='User',
            target_id=str(athlete_id),
            details={'organization_id': org.id}
        )
        db.session.commit()

    flash('Athlete removed from your roster. You can restore them in Settings.', 'success')
    return redirect(url_for('organization_dashboard') + '#roster')


@app.route('/organization/roster/<int:athlete_id>/restore', methods=['POST'])
@login_required
def organization_restore_athlete(athlete_id):
    require_organization()
    OrganizationRosterExclusion.query.filter_by(
        organization_user_id=current_user.id, athlete_id=athlete_id
    ).delete()
    db.session.commit()
    flash('Athlete restored to your roster.', 'success')
    return redirect(url_for('organization_settings'))


@app.route('/organization/settings', methods=['GET', 'POST'])
@login_required
def organization_settings():
    org = require_organization()

    if request.method == 'POST':
        website = request.form.get('website', '').strip()
        contact_phone = request.form.get('contact_phone', '').strip()
        contact_name = re.sub(r'\s+', ' ', request.form.get('contact_name', '')).strip()
        contact_title = request.form.get('contact_title', '').strip()
        aliases = clean_alias_text(request.form.get('aliases', ''))

        if website and (len(website) > 255 or not re.match(r'^https?://[^\s]+\.[^\s]+$', website)):
            flash('Please enter a full website address starting with https://, or leave it empty.', 'danger')
            return redirect(url_for('organization_settings'))
        if not PAYER_PHONE_PATTERN.match(contact_phone):
            flash('Please enter a valid contact phone number.', 'danger')
            return redirect(url_for('organization_settings'))
        if not contact_name or len(contact_name) > 150 or not contact_title or len(contact_title) > 100:
            flash("Please enter the contact person's name and title.", 'danger')
            return redirect(url_for('organization_settings'))

        org.website = website or None
        org.contact_phone = contact_phone
        org.contact_name = contact_name
        org.contact_title = contact_title

        # Alias changes wait for the Super Admin (they decide who appears on the roster).
        approved = OrganizationProfile.split_names(org.approved_aliases)
        if [a.lower() for a in aliases] != [a.lower() for a in approved]:
            org.requested_aliases = '\n'.join(aliases) or ''
            flash('Saved. Your team name changes will apply once D.A.R.T. approves them.', 'success')
        else:
            org.requested_aliases = None
            flash('Saved.', 'success')

        create_audit_log(
            action='organization_settings_updated',
            actor_user_id=current_user.id,
            target_type='OrganizationProfile',
            target_id=str(org.id),
        )
        db.session.commit()
        return redirect(url_for('organization_settings'))

    removed = (
        User.query.join(OrganizationRosterExclusion, OrganizationRosterExclusion.athlete_id == User.id)
        .filter(OrganizationRosterExclusion.organization_user_id == current_user.id)
        .order_by(User.full_name).all()
    )

    return render_template('organization_settings.html', org=org, removed=removed)


# ---------- Super Admin: verify organizations ----------
@app.route('/admin/organizations')
@login_required
def admin_organizations():
    require_super_admin()

    pending = OrganizationProfile.query.filter_by(verification_status='pending').order_by(OrganizationProfile.created_at).all()
    alias_requests = OrganizationProfile.query.filter(
        OrganizationProfile.verification_status == 'verified',
        OrganizationProfile.requested_aliases.isnot(None)
    ).all()
    verified = OrganizationProfile.query.filter_by(verification_status='verified').order_by(OrganizationProfile.org_name).all()

    return render_template(
        'admin_organizations.html',
        pending=pending,
        alias_requests=alias_requests,
        verified=verified,
        roster_size=lambda org: len(roster_athletes(org)),
    )


@app.route('/admin/organizations/<int:org_id>/<action>', methods=['POST'])
@login_required
def admin_organization_action(org_id, action):
    require_super_admin()
    org = db.session.get(OrganizationProfile, org_id)

    if not org or action not in ('verify', 'reject', 'approve_aliases', 'decline_aliases'):
        abort(404)

    user = db.session.get(User, org.user_id)

    if action == 'verify' and org.verification_status == 'pending':
        org.verification_status = 'verified'
        org.verified_at = datetime.utcnow()
        user.is_verified = True
        if request.form.get('approve_aliases') == 'yes':
            org.approved_aliases = org.requested_aliases
        org.requested_aliases = None
        message = f'{org.org_name} is verified. Its roster is now live.'

    elif action == 'reject' and org.verification_status == 'pending':
        org.verification_status = 'rejected'
        user.is_verified = False
        message = f'{org.org_name} was rejected.'

    elif action == 'approve_aliases' and org.requested_aliases is not None:
        org.approved_aliases = org.requested_aliases or None
        org.requested_aliases = None
        message = f'Team names approved for {org.org_name}.'

    elif action == 'decline_aliases' and org.requested_aliases is not None:
        org.requested_aliases = None
        message = f'Team name changes declined for {org.org_name}.'

    else:
        flash('Nothing to do for this organization.', 'warning')
        return redirect(url_for('admin_organizations'))

    create_audit_log(
        action=f'organization_{action}',
        actor_user_id=current_user.id,
        target_type='OrganizationProfile',
        target_id=str(org.id),
        details={'organization': org.org_name}
    )
    db.session.commit()
    flash(message, 'success')
    return redirect(url_for('admin_organizations'))


@app.context_processor
def inject_admin_organization_count():
    if current_user.is_authenticated and current_user.role == 'System':
        return {
            'organizations_awaiting': OrganizationProfile.query.filter(or_(
                OrganizationProfile.verification_status == 'pending',
                db.and_(
                    OrganizationProfile.verification_status == 'verified',
                    OrganizationProfile.requested_aliases.isnot(None)
                )
            )).count()
        }
    return {'organizations_awaiting': 0}


# ==========================================================
# PUBLIC ATHLETE PAGES, SEO, SHARING AND PDF
# ==========================================================
# Google listing (seo_indexable):
#   - only verified athletes;
#   - adults (18+) are listed unless they switch it off;
#   - under-18s (or unknown age) only after they switch it on.
# Shared records use signed links (/r/<token>), so record numbers can't be
# guessed to see records an athlete hasn't shared.

ATHLETE_CATEGORY_SPORTS = ('Football', 'Basketball', 'Kickball')
record_share_signer = URLSafeSerializer(app.config['SECRET_KEY'], salt='dart-record-share-v1')


def athlete_age(user):
    if user.date_of_birth:
        today = utc_today()
        dob = user.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return user.age


def seo_indexable(user):
    if not user or user.role != 'Athlete' or not user.is_verified:
        return False
    if user.search_visibility == 'off':
        return False
    if user.search_visibility == 'on':
        return True
    age = athlete_age(user)
    return age is not None and age >= 18


def slugify(text):
    text = re.sub(r"[^a-z0-9]+", '-', (text or '').lower()).strip('-')
    return text[:80] or 'athlete'


def athlete_public_path(user):
    return url_for('athlete_public', user_id=user.id, slug=slugify(user.full_name))


def record_share_token(record):
    return record_share_signer.dumps(record.id)


def record_share_path(record):
    return url_for('record_share', token=record_share_token(record))


def record_headline(record):
    """e.g. '2 goals, 1 assist vs Rival Academy'."""
    if record.sport == 'Football':
        stats = f'{record.goals or 0} goal{"s" if (record.goals or 0) != 1 else ""}, {record.assists or 0} assist{"s" if (record.assists or 0) != 1 else ""}'
    elif record.sport == 'Basketball':
        stats = f'{record.points or 0} points, {record.total_rebounds or 0} rebounds'
    else:
        stats = f'{record.home_runs or 0} home run{"s" if (record.home_runs or 0) != 1 else ""}'
    return f'{stats} vs {record.team_played_against or "-"}'


app.jinja_env.globals.update(
    athlete_public_path=athlete_public_path,
    athlete_age=athlete_age,
    record_share_path=record_share_path,
    record_headline=record_headline,
    seo_indexable=seo_indexable,
)


def public_records_for(athlete, viewer):
    """Approved records of `athlete` that `viewer` may see on public pages (search rules)."""
    query = SportRecord.query.filter(
        SportRecord.user_id == athlete.id,
        SportRecord.status == 'approved'
    )
    query = apply_record_visibility(query, viewer, 'search')
    return query.order_by(SportRecord.game_date.desc(), SportRecord.id.desc()).all()


def career_summary(records):
    """Totals per sport for the public profile and its description."""
    summary = {}
    for r in records:
        s = summary.setdefault(r.sport, {
            'sport': r.sport, 'games': 0, 'goals': 0, 'assists': 0, 'points': 0,
            'rebounds': 0, 'home_runs': 0, 'awards': 0, 'teams': set(),
        })
        # Each sport has its own totals, so plain sums are right.
        s['games'] += 1
        s['goals'] += r.goals or 0
        s['assists'] += r.assists or 0
        s['points'] += r.points or 0
        s['rebounds'] += r.total_rebounds or 0
        s['home_runs'] += r.home_runs or 0
        s['awards'] += (r.man_of_the_match or 0) + (r.mvp or 0)
        if r.team:
            s['teams'].add(r.team)
    return [summary[s] for s in ATHLETE_CATEGORY_SPORTS if s in summary]


def athlete_seo(athlete, records):
    """Title, description and schema.org data for a public athlete page."""
    sports = career_summary(records)
    sport_names = ' & '.join(s['sport'] for s in sports) or 'Sports'
    title = f'{athlete.full_name} - Official {sport_names} Records'

    parts = []
    for s in sports:
        if s['sport'] == 'Football':
            parts.append(f"{s['games']} football games, {s['goals']} goals, {s['assists']} assists")
        elif s['sport'] == 'Basketball':
            parts.append(f"{s['games']} basketball games, {s['points']} points, {s['rebounds']} rebounds")
        else:
            parts.append(f"{s['games']} kickball games, {s['home_runs']} home runs")

    where = ', '.join(x for x in (athlete.school, athlete.nationality) if x)
    description = (
        f"Verified {sport_names.lower()} record of {athlete.full_name}"
        + (f" ({where})" if where else '')
        + (': ' + '; '.join(parts) if parts else '')
        + '. Official coach-approved athlete records on D.A.R.T.'
    )

    url = site_url(athlete_public_path(athlete))
    person = {
        '@type': 'Person',
        'name': athlete.full_name,
        'url': url,
        'description': description,
    }
    if athlete.profile_picture:
        person['image'] = site_url(url_for('profile_picture', filename=athlete.profile_picture))
    if athlete.nationality:
        person['nationality'] = athlete.nationality
    if athlete.school:
        person['affiliation'] = {'@type': 'SportsTeam', 'name': athlete.school}
    if sports:
        person['knowsAbout'] = [s['sport'] for s in sports]

    structured = {
        '@context': 'https://schema.org',
        '@type': 'ProfilePage',
        'name': title,
        'url': url,
        'mainEntity': person,
        'isPartOf': {'@type': 'WebSite', 'name': 'D.A.R.T.', 'url': site_url('/')},
    }

    return {'title': title, 'description': description[:300], 'structured': structured, 'url': url}


@app.route('/athletes/<int:user_id>/<slug>')
def athlete_public(user_id, slug):
    user = db.session.get(User, user_id)

    if not user or user.role != 'Athlete':
        abort(404)

    correct = slugify(user.full_name)
    if slug != correct:
        return redirect(url_for('athlete_public', user_id=user.id, slug=correct), 301)

    return render_user_profile(user)


# ---------- Shared records ----------
def _record_from_token(token):
    try:
        record_id = record_share_signer.loads(token)
    except BadSignature:
        abort(404)

    record = db.session.get(SportRecord, record_id)

    if (
        not record or record.status != 'approved'
        or not record.user or record.user.role != 'Athlete' or not record.user.is_verified
    ):
        abort(404)

    return record


@app.route('/r/<token>')
def record_share(token):
    record = _record_from_token(token)
    athlete = record.user
    share_url = site_url(url_for('record_share', token=token))

    return render_template(
        'record_share.html',
        record=record,
        athlete=athlete,
        share_url=share_url,
        pdf_url=url_for('record_share_pdf', token=token),
        indexable=seo_indexable(athlete),
        title=f'{athlete.full_name}: {record_headline(record)}',
    )


@app.route('/r/<token>.pdf')
def record_share_pdf(token):
    record = _record_from_token(token)
    athlete = record.user
    share_url = site_url(url_for('record_share', token=token))
    logo = os.path.join(app.static_folder or '', 'images', 'dartlogo.png')

    pdf_bytes = build_record_pdf(record, athlete, share_url, logo_path=logo)

    date_part = record.game_date.isoformat() if record.game_date else str(record.year)
    filename = f"DART-{slugify(athlete.full_name)}-{record.sport.lower()}-{date_part}.pdf"

    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.headers['Cache-Control'] = 'private, max-age=300'
    if not seo_indexable(athlete):
        response.headers['X-Robots-Tag'] = 'noindex'
    return response


# ---------- Crawlers ----------
@app.route('/robots.txt')
def robots_txt():
    lines = [
        'User-agent: *',
        'Allow: /',
        'Disallow: /admin',
        'Disallow: /api/',
        'Disallow: /student',
        'Disallow: /scout',
        'Disallow: /organization',
        'Disallow: /profile',
        'Disallow: /billing',
        'Disallow: /checkout',
        'Disallow: /search',
        'Disallow: /inbox',
        'Disallow: /2fa',
        'Disallow: /highlights/',
        'Disallow: /edit_record',
        'Disallow: /id-document/',
        '',
        f"Sitemap: {site_url('/sitemap.xml')}",
    ]
    response = make_response('\n'.join(lines) + '\n')
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    return response


@app.route('/sitemap.xml')
def sitemap_xml():
    from xml.sax.saxutils import escape

    last_games = dict(
        db.session.query(SportRecord.user_id, func.max(SportRecord.game_date))
        .filter(SportRecord.status == 'approved')
        .group_by(SportRecord.user_id)
        .all()
    )

    urls = [(site_url('/'), None, '1.0'), (site_url(url_for('pricing')), None, '0.6')]
    urls += [(site_url(url_for(e)), None, '0.4') for e in ('register', 'register_scout', 'register_organization')]

    athletes = User.query.filter_by(role='Athlete', is_verified=True).all()
    for athlete in athletes:
        if seo_indexable(athlete):
            last = last_games.get(athlete.id)
            urls.append((site_url(athlete_public_path(athlete)), last.isoformat() if last else None, '0.8'))

    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, priority in urls[:50000]:
        body.append('  <url>')
        body.append(f'    <loc>{escape(loc)}</loc>')
        if lastmod:
            body.append(f'    <lastmod>{lastmod}</lastmod>')
        body.append(f'    <priority>{priority}</priority>')
        body.append('  </url>')
    body.append('</urlset>')

    response = make_response('\n'.join(body))
    response.headers['Content-Type'] = 'application/xml; charset=utf-8'
    return response


# ---------- Legal pages ----------
LEGAL_UPDATED = 'October 6, 2026'


@app.route('/privacy')
def privacy_page():
    return render_template('legal/privacy.html', updated=LEGAL_UPDATED)


@app.route('/terms')
def terms_page():
    return render_template('legal/terms.html', updated=LEGAL_UPDATED)


@app.route('/cookies')
def cookies_page():
    return render_template('legal/cookies.html', updated=LEGAL_UPDATED)


@app.route('/legal')
def legal_page():
    return render_template('legal/legal.html', updated=LEGAL_UPDATED)


@app.route('/legal/organizations')
def organization_terms():
    return render_template('legal/organizations.html', updated=LEGAL_UPDATED, version=ORG_TERMS_VERSION)


# ==========================================================
# PROFILE CARD: share, card image (PNG) and PDF
# ==========================================================
# Athletes, coaches and scouts get a D.A.R.T. profile card (profile_card.py
# draws it). The same data shows as the card on My Profile / the public
# profile, as the link-preview image, as "Download card" and in the PDF.
# Only public sport information is used (age, never the date of birth).

PROFILE_CARD_ROLES = ('Athlete', 'Coach', 'Scout')
PROFILE_PHOTO_MAX_BYTES = 6 * MB


def country_code(nationality):
    """'Liberian' -> 'LR' (from the same table as the flag emoji)."""
    flag = country_flag(nationality)
    letters = [chr(ord(ch) - 0x1F1E6 + ord('A')) for ch in flag if 0x1F1E6 <= ord(ch) <= 0x1F1FF]
    return ''.join(letters) if len(letters) == 2 else ''


def profile_share_path(user):
    """Public address of a user's profile (athletes: the SEO address)."""
    if user.role == 'Athlete':
        return athlete_public_path(user)
    return url_for('user_profile', user_id=user.id)


def profile_card_info(user):
    """Everything the card shows, as plain data (template + image + PDF)."""
    approved = SportRecord.query.filter_by(user_id=user.id, status='approved').all() if user.role == 'Athlete' else []
    info = {
        'id': user.id,
        'name': user.full_name,
        'role': user.role,
        'verified': bool(user.is_verified),
        'picture': user.profile_picture,
        'nationality': user.nationality or '',
        'flag': country_flag(user.nationality) if user.nationality else '',
        'code': country_code(user.nationality) if user.nationality else '',
        'stats': [],
        'extra': [],
    }

    if user.role == 'Athlete':
        sports = [s for s in ('Football', 'Basketball', 'Kickball') if any(r.sport == s for r in approved)]
        positions = {}
        for r in approved:
            if r.position:
                positions[r.position] = positions.get(r.position, 0) + 1
        main_position = max(positions, key=positions.get) if positions else ''
        sub = [' · '.join(sports) or 'Athlete']
        if main_position:
            sub.append(main_position)
        if user.shirt_number:
            sub.append(f'#{user.shirt_number}')
        age = athlete_age(user)
        info.update({
            'eyebrow': 'Athlete Profile',
            'subtitle': ' · '.join(sub),
            'pill': user.athlete_category or 'Athlete',
            'stats': [
                ('Age', f'{age} yrs' if age is not None else '—'),
                ('Weight', f'{float(user.weight_kg):g} kg' if user.weight_kg else '—'),
                ('Height', f'{float(user.height_cm):g} cm' if user.height_cm else '—'),
            ],
            'extra': [('Team', user.school)] if user.school else [],
            'career': career_summary(public_records_for(user, current_user)),
        })
    elif user.role == 'Coach':
        info.update({
            'eyebrow': 'Coach Profile',
            'subtitle': user.school or 'Coach',
            'pill': user.coach_category or 'Coach',
            'stats': [('Team', user.school or '—'), ('Role', 'Coach / Admin')],
        })
    else:  # Scout
        sp = ScoutProfile.query.filter_by(user_id=user.id).first()
        info.update({
            'eyebrow': 'Scout Profile',
            'subtitle': (sp.organization if sp and sp.organization else user.school) or 'Scout',
            'pill': (sp.job_title if sp and sp.job_title else 'Scout'),
            'stats': [
                ('Sports', (sp.sports if sp and sp.sports else '—')),
                ('Experience', f'{sp.years_experience} yrs' if sp and sp.years_experience is not None else '—'),
                ('Based in', ', '.join(x for x in ((sp.city if sp else None), (sp.country if sp else None)) if x) or '—'),
            ],
        })

    info['share_path'] = profile_share_path(user)
    return info


def _fetch_profile_photo(user):
    """The profile picture's bytes for the card image, or None."""
    if not user.profile_picture:
        return None
    try:
        result = supabase.storage.from_('profile-pictures').get_public_url(user.profile_picture)
        if isinstance(result, dict):
            result = result.get('publicUrl') or result.get('public_url')
        url = (result or '').rstrip('?')
        if not url:
            return None
        response = http_requests.get(url, timeout=5)
        if response.status_code == 200 and len(response.content) <= PROFILE_PHOTO_MAX_BYTES:
            return response.content
    except Exception as e:
        app.logger.warning('Profile photo for card failed: %s', e)
    return None


def profile_card_png(user):
    info = profile_card_info(user)
    return render_profile_card(CardData(
        name=info['name'],
        eyebrow=info['eyebrow'].upper(),
        subtitle=info['subtitle'],
        pill=info['pill'],
        stats=info['stats'],
        nationality=info['nationality'],
        country_code=info['code'],
        extra=info['extra'],
        verified=info['verified'],
        photo=_fetch_profile_photo(user),
    ))


def _card_user_or_404(user_id):
    user = db.session.get(User, user_id)
    if not user or user.role not in PROFILE_CARD_ROLES:
        abort(404)
    return user


@app.route('/profile-card/<int:user_id>.png')
def profile_card_image(user_id):
    user = _card_user_or_404(user_id)
    response = make_response(profile_card_png(user))
    response.headers['Content-Type'] = 'image/png'
    # Link previews fetch this often; a short cache keeps it fresh after edits.
    response.headers['Cache-Control'] = 'public, max-age=600'
    if request.args.get('download'):
        response.headers['Content-Disposition'] = f'attachment; filename="DART-{slugify(user.full_name)}-card.png"'
    if not seo_indexable(user):
        response.headers['X-Robots-Tag'] = 'noindex'
    return response


@app.route('/profile-card/<int:user_id>.pdf')
def profile_card_pdf(user_id):
    user = _card_user_or_404(user_id)
    info = profile_card_info(user)
    records = public_records_for(user, current_user)[:12] if user.role == 'Athlete' else []
    pdf_bytes = build_profile_pdf(
        info,
        card_png=profile_card_png(user),
        records=records,
        profile_url=site_url(info['share_path']),
    )
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="DART-{slugify(user.full_name)}-profile.pdf"'
    response.headers['Cache-Control'] = 'private, max-age=300'
    if not seo_indexable(user):
        response.headers['X-Robots-Tag'] = 'noindex'
    return response


app.jinja_env.globals.update(profile_card_info=profile_card_info, profile_share_path=profile_share_path)


# ========== SUPABASE STORAGE ROUTES ==========

@app.route('/profile-picture/<path:filename>')
def profile_picture(filename):
    try:
        result = supabase.storage.from_('profile-pictures').get_public_url(filename)

        if isinstance(result, str):
            return redirect(result)

        if isinstance(result, dict):
            public_url = result.get('publicUrl') or result.get('public_url')

            if public_url:
                return redirect(public_url)

        flash('Profile picture could not be loaded.', 'danger')
        return redirect(url_for('index'))

    except Exception as e:
        print("Profile picture error:", e)
        return redirect(url_for('index'))


@app.route('/id-document/<path:filename>')
@login_required
def id_document(filename):
    # Only coaches/admins can view student ID documents
    if current_user.role not in ('Coach', 'System'):
        flash('You are not authorized to view ID documents.', 'danger')
        return redirect(url_for('student_dashboard'))

    # Find the student who owns this ID document
    owner = User.query.filter_by(id_document=filename).first()

    if not owner:
        flash('ID document not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Super Admin can view documents from all schools
    is_super_admin = current_user.role == 'System'

    # Regular coaches can only view documents belonging to their school
    if not is_super_admin:
        if current_user.school.strip().casefold() != owner.school.strip().casefold():
            flash(
                'You are not authorized to view this ID document.',
                'danger'
            )
            return redirect(url_for('admin_dashboard'))

    try:
        result = supabase.storage.from_('id-documents').create_signed_url(
            filename,
            3600
        )

        if isinstance(result, dict):
            signed_url = (
                result.get('signedURL')
                or result.get('signedUrl')
                or result.get('signed_url')
            )

            if signed_url:
                return redirect(signed_url)

        flash('ID document could not be loaded.', 'danger')
        return redirect(url_for('admin_dashboard'))

    except Exception as e:
        print("ID document error:", e)
        flash('Unable to open the ID document.', 'danger')
        return redirect(url_for('admin_dashboard'))

# ========== STUDENT DASHBOARD ==========
from datetime import datetime

@app.route('/student')
@login_required
def student_dashboard():
    if current_user.role != 'Athlete':
        return redirect(url_for(dashboard_endpoint_for(current_user)))

    records = SportRecord.query.filter_by(
        user_id=current_user.id
    ).all()

    current_year = datetime.now().year

    athlete_registrations = Registration.query.filter_by(
        user_id=current_user.id,
        registration_type='athlete',
        registration_year=current_year,
        status='active'
    ).all()

    registered_categories = [
        registration.category
        for registration in athlete_registrations
    ]

    response = make_response(render_template(
        'student_dashboard.html',
        records=records,
        current_year=current_year,
        registered_categories=registered_categories,
        allowed_competitions=athlete_allowed_competitions(current_user, current_year),
        highlights=highlights_for(current_user.id)
    ))

    # PWA: let the service worker keep a copy of this page on the athlete's
    # device so they can open the record form and save records offline.
    # Only verified athletes can submit records, so only they opt in.
    if current_user.is_verified:
        response.headers['X-DART-Offline-Cacheable'] = '1'

    return response
@app.route('/student/record')
@login_required
def record_form_page():
    """
    Just the "Submit a sports record" form (for the athlete's own
    categories). The service worker keeps this page on the device so
    athletes can record games with no internet; records saved offline
    upload automatically when the device is back online.
    """
    if current_user.role != 'Athlete':
        return redirect(url_for(dashboard_endpoint_for(current_user)))

    response = make_response(render_template(
        'record_form.html',
        allowed_competitions=athlete_allowed_competitions(current_user),
    ))

    # Verified athletes can submit records, so only they get an offline copy.
    if current_user.is_verified:
        response.headers['X-DART-Offline-Cacheable'] = '1'

    return response


# ==========================================================
# SCOUT DASHBOARD
# ==========================================================

# ==========================================================
# SCOUT DASHBOARD DATA
# ==========================================================
# Everything is computed live from approved records of verified athletes.

SCOUT_LEADERBOARDS = (
    # sport, stat column, label, icon
    ('Football', 'goals', 'Goals', 'fa-futbol'),
    ('Basketball', 'points', 'Points', 'fa-basketball'),
    ('Kickball', 'home_runs', 'Home Runs', 'fa-baseball'),
)

SCOUT_SPORT_ICONS = {
    'Football': 'fa-futbol',
    'Basketball': 'fa-basketball',
    'Kickball': 'fa-baseball',
}


def _approved_records_query():
    return (
        SportRecord.query
        .join(User, User.id == SportRecord.user_id)
        .filter(
            User.role == 'Athlete',
            User.is_verified == True,
            SportRecord.status == 'approved'
        )
    )


def scout_dashboard_data(scout):
    # Free athletes show only their latest record (plans.py).
    approved = apply_record_visibility(_approved_records_query(), scout, 'dashboard')
    visible_ids = select(approved.with_entities(SportRecord.id).subquery().c.id)
    full_dashboard = scout_has_full_dashboard(scout)

    # Season = latest year with approved records (else this year).
    season = (
        db.session.query(func.max(SportRecord.year))
        .filter(SportRecord.status == 'approved')
        .scalar()
        or datetime.now().year
    )

    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)

    kpis = {
        'athletes': User.query.filter_by(role='Athlete', is_verified=True).count(),
        'records': approved.count(),
        'new_this_week': approved.filter(SportRecord.created_at >= week_ago).count(),
        'saved_searches': SavedSearch.query.filter_by(user_id=scout.id).count(),
    }

    # Free scout accounts: welcome, quick search and the numbers only.
    if not full_dashboard:
        return {'season': season, 'kpis': kpis, 'full': False}

    # Athletes and records per sport, for the discovery tiles.
    sports = []

    for sport, icon in SCOUT_SPORT_ICONS.items():
        sport_records = approved.filter(SportRecord.sport == sport)
        sports.append({
            'name': sport,
            'icon': icon,
            'records': sport_records.count(),
            'athletes': sport_records.with_entities(
                func.count(func.distinct(SportRecord.user_id))
            ).scalar() or 0,
        })

    # Season leaders per sport.
    leaderboards = []

    for sport, field, label, icon in SCOUT_LEADERBOARDS:
        total = func.sum(func.coalesce(getattr(SportRecord, field), 0)).label('total')
        rows = (
            db.session.query(User, total, func.count(SportRecord.id).label('games'))
            .join(SportRecord, SportRecord.user_id == User.id)
            .filter(
                User.role == 'Athlete',
                User.is_verified == True,
                SportRecord.status == 'approved',
                SportRecord.sport == sport,
                SportRecord.year == season,
                SportRecord.id.in_(visible_ids)
            )
            .group_by(User.id)
            .order_by(total.desc(), func.count(SportRecord.id).asc())
            .limit(5)
            .all()
        )
        leaderboards.append({
            'sport': sport,
            'label': label,
            'icon': icon,
            'field': field,
            'rows': [
                {'athlete': user, 'total': int(value or 0), 'games': games}
                for user, value, games in rows
                if value
            ],
        })

    # Rising talent: athletes 18 and under, ranked by awards then games.
    awards = (
        func.sum(func.coalesce(SportRecord.man_of_the_match, 0))
        + func.sum(func.coalesce(SportRecord.mvp, 0))
    ).label('awards')
    rising = (
        db.session.query(User, awards, func.count(SportRecord.id).label('games'))
        .join(SportRecord, SportRecord.user_id == User.id)
        .filter(
            User.role == 'Athlete',
            User.is_verified == True,
            User.age.isnot(None),
            User.age <= 18,
            SportRecord.status == 'approved',
            SportRecord.id.in_(visible_ids)
        )
        .group_by(User.id)
        .order_by(awards.desc(), func.count(SportRecord.id).desc())
        .limit(5)
        .all()
    )

    recent = (
        approved
        .order_by(SportRecord.created_at.desc(), SportRecord.id.desc())
        .limit(6)
        .all()
    )

    return {
        'full': True,
        'season': season,
        'kpis': kpis,
        'sports': sports,
        'leaderboards': leaderboards,
        'rising': [
            {'athlete': user, 'awards': int(value or 0), 'games': games}
            for user, value, games in rising
        ],
        'recent': recent,
        'saved_searches': saved_searches_for(scout),
        'competitions': competitions_for('Football'),
    }


@app.route('/scout')
@login_required
def scout_dashboard():

    # Only verified Scout accounts can access this dashboard.
    if current_user.role != 'Scout':
        flash(
            'You are not authorized to access the Scout dashboard.',
            'danger'
        )

        if current_user.role == 'Athlete':
            return redirect(url_for('student_dashboard'))

        if current_user.role in ('Coach', 'System'):
            return redirect(url_for('admin_dashboard'))

        return redirect(url_for('index'))

    if not current_user.is_verified:
        logout_user()
        flash(
            'Your Scout account is pending D.A.R.T. administrator verification.',
            'warning'
        )
        return redirect(url_for('login'))

    return render_template(
        'scout_dashboard.html',
        dash=scout_dashboard_data(current_user)
    )
class RecordSubmissionError(Exception):
    """A submitted sports record failed validation."""


class DuplicateRecordError(RecordSubmissionError):
    """The same game record was already submitted (pending or approved)."""


def utc_today():
    return datetime.now(timezone.utc).date()


def game_date_future_error(game_date):
    """A game can only be recorded on the day it was played or later."""
    if game_date > utc_today():
        return (
            "The game date can't be in the future. Choose the day the game "
            "was played (today or earlier)."
        )
    return None


app.jinja_env.globals['today_iso'] = lambda: utc_today().isoformat()
app.jinja_env.globals.update(athlete_categories=ATHLETE_CATEGORIES, coach_categories=COACH_CATEGORIES)
app.jinja_env.globals['athlete_allowed_competitions'] = lambda user: athlete_allowed_competitions(user)


SHIRT_NUMBER_PATTERN = re.compile(r'^\d{1,2}$')


def clean_shirt_number(value):
    """
    Optional shirt (jersey) number: 0-99, kept as text so basketball's
    "00" stays different from "0". Returns (number or None, error).
    """
    value = (value or '').strip().lstrip('#').strip()

    if not value:
        return None, None

    if not SHIRT_NUMBER_PATTERN.match(value):
        return None, 'Shirt number must be a number from 0 to 99.'

    return value, None


def build_sport_record_from_form(form):
    """
    Validate a sports-record submission for the current athlete and
    return an unsaved SportRecord.

    Shared by the regular form post and the PWA offline-sync API so both
    paths apply exactly the same rules. Raises RecordSubmissionError
    (or DuplicateRecordError) with a user-facing message.
    """
    # Monthly upload limit of the athlete's plan (plans.py).
    limit_error = limit_reached(current_user, 'records')

    if limit_error:
        raise RecordSubmissionError(limit_error)

    club_division = form.get(
        'club_division',
        ''
    ).strip()

    VALID_CLUB_DIVISIONS = {
        '1st Division',
        '2nd Division',
        '3rd Division'
    }

    competition_category = form.get('competition_category', '').strip()

    if competition_category == 'Club League':
        if club_division not in VALID_CLUB_DIVISIONS:
            raise RecordSubmissionError('Please select a valid Club League Division.')
    else:
        club_division = None

    sport = form.get('sport')
    year = form.get('year')
    position = form.get('position', '').strip()
    games_played = form.get('games_played') or 0
    trophies = form.getlist('trophy')
    man_of_the_match = form.get('man_of_the_match')

    try:
        games_played = int(games_played)
        man_of_the_match = int(man_of_the_match)
    except (TypeError, ValueError):
        raise RecordSubmissionError('Games Played or MOTM/QOTM(if given) must both be 1 for every game record.')

    if games_played != 1:
        raise RecordSubmissionError('Games Played must be exactly 1 because records are submitted game-by-game.')

    if man_of_the_match == 2:
        raise RecordSubmissionError('MOTM/QOTM must be exactly 1 for every submitted game record.')

    # ============================================================
    # SPORT ↔ COMPETITION ↔ AGE GROUP ↔ TROPHY
    # ============================================================
    # Rules live in competitions.py (LFA / LBA / LKF competitions).
    if sport not in VALID_POSITIONS:
        raise RecordSubmissionError('Please select a valid sport.')

    if not competition_category:
        raise RecordSubmissionError('Please select a competition.')

    if not competition_allowed(sport, competition_category):
        raise RecordSubmissionError(
            f'{competition_category} is not a {sport} competition.'
        )

    age_group, age_group_error = clean_age_group(form, competition_category)

    if age_group_error:
        raise RecordSubmissionError(age_group_error)

    trophy_value, trophy_error = validate_trophy(
        trophies,
        sport,
        competition_category,
        club_division or age_group
    )

    if trophy_error:
        raise RecordSubmissionError(trophy_error)

    team = form.get('team', '').strip()
    team_played_against = form.get('team_played_against', '').strip()
    match_minutes_played = form.get('match_minutes_played') or 0
    clean_sheets = form.get('clean_sheets') or 0
    saves = form.get('saves') or 0
    rebound_type = form.get('rebound_type','').strip()

    # Preferred foot is only changed when the form actually sends it
    # (the record form doesn't, so submitting a record must not wipe the
    # value the athlete saved in My Profile).
    if 'preferred_foot' in form:
        preferred_foot = form.get('preferred_foot', '').strip()

        if preferred_foot not in ['', 'Right', 'Left', 'Both']:
            raise RecordSubmissionError('Invalid preferred foot selection.')

        current_user.preferred_foot = preferred_foot or None

    shirt_number, shirt_number_error = clean_shirt_number(form.get('shirt_number'))

    if shirt_number_error:
        raise RecordSubmissionError(shirt_number_error)

    if not competition_category:
        raise RecordSubmissionError('Please select a competition.')

    if competition_category not in VALID_COMPETITIONS:
        raise RecordSubmissionError('Invalid competition category selected.')

    # ===== AFCON / WAFU / WORLD CUP TEAM =====
    competition_team, competition_team_error = clean_competition_team(
        form,
        sport,
        competition_category
    )

    if competition_team_error:
        raise RecordSubmissionError(competition_team_error)

    # ===== POSITION VALIDATION =====
    if sport not in VALID_POSITIONS:
        raise RecordSubmissionError('Please select a valid sport.')

    if not position:
        raise RecordSubmissionError('Please select a position.')

    if position not in VALID_POSITIONS[sport]:
        raise RecordSubmissionError(f'Invalid position selected for {sport}.')
    
    # ===== CATEGORY PERMISSION CHECK =====
    current_year = datetime.now().year

    athlete_registrations = Registration.query.filter_by(
        user_id=current_user.id,
        registration_type='athlete',
        registration_year=current_year,
        status='active'
    ).all()

    registered_categories = [
        registration.category
        for registration in athlete_registrations
    ]

    allowed_to_submit = any(
        athlete_can_submit_competition(
            category,
            competition_category
        )
        for category in registered_categories
    )

    if not allowed_to_submit:
        raise RecordSubmissionError('You are not allowed to submit a record for this competition category.')

    # ===== PREVENT DUPLICATE: Same Sport + Same Year =====
    # Only block if there is already an approved or pending record
    # =====================================================
    # GAME DATE
    # =====================================================

    game_date_raw = form.get('game_date', '').strip()

    if not game_date_raw:
        raise RecordSubmissionError('Please enter the date the game was played.')

    try:
        game_date = datetime.strptime(
            game_date_raw,
            '%Y-%m-%d'
        ).date()

    except ValueError:
        raise RecordSubmissionError('Invalid game date. Please select a valid date.')

    future_error = game_date_future_error(game_date)

    if future_error:
        raise RecordSubmissionError(future_error)


    # =====================================================
    # RECORD YEAR MUST MATCH GAME DATE YEAR
    # =====================================================

    try:
        year = int(year)
    except (TypeError, ValueError):
        raise RecordSubmissionError('Invalid record year.')

    if game_date.year != year:
        raise RecordSubmissionError(f'The game date belongs to {game_date.year}, '
            f'but the selected record year is {year}. '
            f'Please make them match.')


# =====================================================
# PREVENT DUPLICATE GAME
# =====================================================
#
# Multiple games are allowed.
#
# We only block the SAME game when the identifying
# information is the same.
#
# Different game date = different game = allowed.
# =====================================================

    existing = SportRecord.query.filter(
        SportRecord.user_id == current_user.id,
        SportRecord.sport == sport,
        SportRecord.year == year,
        SportRecord.game_date == game_date,
        SportRecord.competition_category == competition_category,
        SportRecord.team == team,
        SportRecord.status.in_(['approved', 'pending'])
    ).first()

    if existing:
        raise DuplicateRecordError(
            f'You already submitted a {existing.sport} record '
            f'for {existing.competition_category} against '
            f'{existing.team or "this team"} on '
            f'{existing.game_date.strftime("%B %d, %Y")}.'
        )

    # Safe conversion helper
    def safe_int(value):
        try:
            return int(value) if value not in [None, ''] else 0
        except:
            return 0

    # The latest shirt number also becomes the athlete's current number
    # (shown with their personal info). Leaving it blank keeps the old one.
    if shirt_number:
        current_user.shirt_number = shirt_number

    record = SportRecord(
    user_id=current_user.id,
    sport=sport,
    year=year,
    game_date=game_date,
    position=position,
    shirt_number=shirt_number,
    games_played=1,
    match_minutes_played=safe_int(match_minutes_played),
    man_of_the_match=safe_int(form.get('man_of_the_match')),
    trophy=trophy_value,
    team=team,
    team_played_against=team_played_against,
    competition_category=competition_category,
    club_division=club_division,
    competition_team=competition_team,
    age_group=age_group,
    mvp=safe_int(form.get('mvp')),
    status='pending'
)

    if sport == 'Football':

        record.goals = safe_int(
            form.get('goals')
        )

        record.assists = safe_int(
            form.get('assists')
        )

        record.yellow_cards = safe_int(
            form.get('yellow_cards')
        )

        record.red_cards = safe_int(
            form.get('red_cards')
        )

        # ======================================================
        # GOALKEEPER-ONLY STATISTICS
        # ======================================================

        if position == 'GK':

            record.clean_sheets = safe_int(
                form.get('clean_sheets')
            )

            record.saves = safe_int(
                form.get('saves')
            )

        else:

            record.clean_sheets = 0
            record.saves = 0

        record.rebound_type = None

    elif sport == 'Basketball':
        record.points = safe_int(form.get('points'))

        # The form's Football and Basketball sections used to share the
        # name 'assists' (Football first), so Basketball assists were read
        # from the hidden Football field. Basketball now sends
        # 'basketball_assists'; the fallback covers records saved offline
        # with the older form.
        basketball_assists = form.get('basketball_assists')

        if basketball_assists is None:
            assists_values = form.getlist('assists')
            basketball_assists = assists_values[-1] if assists_values else 0

        record.assists = safe_int(basketball_assists)
        record.blocks = safe_int(form.get('blocks'))
        record.sent_off = safe_int(form.get('sent_off'))

        # Rebound counts (never negative).
        record.offensive_rebounds = max(safe_int(form.get('offensive_rebounds')), 0)
        record.defensive_rebounds = max(safe_int(form.get('defensive_rebounds')), 0)

        # Older form (e.g. a record saved offline before rebound counts)
        # only sent the kind of rebound.
        record.rebound_type = (rebound_type
        if rebound_type in [
            'Offensive rebound',
            'Defensive rebound'
        ]
        else None
    )

        record.clean_sheets = 0

    elif sport == 'Kickball':
        record.home_runs = safe_int(form.get('home_runs'))
        record.kickball_red_cards = safe_int(form.get('kickball_red_cards'))
        record.kickball_yellow_cards = safe_int(form.get('kickball_yellow_cards'))
        record.cut_base = safe_int(form.get('cut_base'))
        record.foul_played = safe_int(form.get('foul_played'))
        record.clean_sheets = 0
        record.rebound_type = None

    return record


@app.route('/submit_record', methods=['POST'])
@login_required
def submit_record():
    if current_user.role != 'Athlete' or not current_user.is_verified:
        flash('You are not allowed to submit records.', 'danger')
        return redirect(url_for('student_dashboard'))

    try:
        record = build_sport_record_from_form(request.form)
    except RecordSubmissionError as error:
        flash(str(error), 'danger')
        return redirect(url_for('student_dashboard'))

    db.session.add(record)
    db.session.commit()

    flash('Record submitted successfully and is pending approval.', 'success')
    return redirect(url_for('student_dashboard'))

# ==========================================================
# PWA: OFFLINE RECORD SYNC API
# ==========================================================
# Athletes can save records on their device when they have no
# internet/data. public/static/js/offline-records.js uploads them here once
# they are back online. Records still go through the same validation
# and arrive as 'pending' for coach/admin approval.

def _no_store_json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/records/sync-status')
def record_sync_status():
    """Tell the sync script who is logged in, plus a fresh CSRF token."""
    if not current_user.is_authenticated:
        return _no_store_json({'authenticated': False})

    return _no_store_json({
        'authenticated': True,
        'user_id': current_user.id,
        'can_submit_records': (
            current_user.role == 'Athlete' and bool(current_user.is_verified)
        ),
        'csrf_token': generate_csrf()
    })


@app.route('/api/records', methods=['POST'])
def api_submit_record():
    """JSON version of submit_record, used for online and offline-synced records."""
    # Not @login_required: that would redirect to the login page, and the
    # sync script needs a clear 401 so it keeps the record queued.
    if not current_user.is_authenticated:
        return _no_store_json({
            'ok': False,
            'reason': 'login_required',
            'error': 'Please log in to upload your saved records.'
        }, 401)

    if current_user.role != 'Athlete' or not current_user.is_verified:
        return _no_store_json({
            'ok': False,
            'reason': 'not_allowed',
            'error': 'You are not allowed to submit records.'
        }, 403)

    try:
        record = build_sport_record_from_form(request.form)
    except DuplicateRecordError as error:
        return _no_store_json({
            'ok': False,
            'reason': 'duplicate',
            'error': str(error)
        }, 409)
    except RecordSubmissionError as error:
        return _no_store_json({
            'ok': False,
            'reason': 'invalid',
            'error': str(error)
        }, 422)

    db.session.add(record)
    db.session.commit()

    if request.headers.get('X-DART-Submit-Mode') == 'online':
        # Regular "Submit Record" click: show the usual message after
        # the page redirects back to the dashboard.
        flash('Record submitted successfully and is pending approval.', 'success')

    return _no_store_json({
        'ok': True,
        'status': 'created',
        'record_id': record.id
    }, 201)

# ========== Delete route ==========#
@app.route('/delete_record/<int:record_id>', methods=['POST'])
@login_required
def delete_record(record_id):
    record = SportRecord.query.get_or_404(record_id)

    # Security checks
    if record.user_id != current_user.id:
        flash('You can only delete your own records.', 'danger')
        return redirect(url_for('student_dashboard'))

    if record.status != 'rejected':
        flash('You can only delete rejected records.', 'danger')
        return redirect(url_for('student_dashboard'))

    # Deleted rows are gone, so record the deletion for the
    # Super Admin dashboard's "records deleted" count.
    create_audit_log(
        RECORD_DELETED_ACTION,
        actor_user_id=current_user.id,
        target_type='sport_record',
        target_id=str(record.id),
        details={
            'sport': record.sport,
            'competition_category': record.competition_category,
            'status': record.status
        }
    )

    db.session.delete(record)
    db.session.commit()
    flash('Rejected record deleted successfully. You can now submit a new one.', 'success')
    return redirect(url_for('student_dashboard'))

# resubmit route============
@app.route('/resubmit_record/<int:record_id>', methods=['POST'])
@login_required
def resubmit_record(record_id):
    record = SportRecord.query.get_or_404(record_id)

    # Only the owner can resubmit
    if record.user_id != current_user.id:
        flash('You can only resubmit your own records.', 'danger')
        return redirect(url_for('student_dashboard'))

    if record.status != 'rejected':
        flash('Only rejected records can be resubmitted.', 'danger')
        return redirect(url_for('student_dashboard'))

    record.status = 'pending'
    db.session.commit()
    flash('Record resubmitted successfully. Waiting for coach approval.', 'success')
    return redirect(url_for('student_dashboard'))

# ========== Edit route ==========#
@app.route('/edit_record/<int:record_id>', methods=['GET', 'POST'])
@login_required
def edit_record(record_id):
    record = SportRecord.query.get_or_404(record_id)

    # =========================================================
    # SECURITY: ONLY THE OWNER CAN EDIT
    # =========================================================
    if record.user_id != current_user.id:
        flash('You can only edit your own records.', 'danger')
        return redirect(url_for('student_dashboard'))

    current_year = str(datetime.now().year)

    # =========================================================
    # ATHLETE REGISTRATION
    # =========================================================
    athlete_registrations = Registration.query.filter_by(
        user_id=current_user.id,
        registration_type='athlete',
        registration_year=int(current_year),
        status='active'
    ).all()

    registered_categories = [
        registration.category
        for registration in athlete_registrations
    ]

    # =========================================================
    # CURRENT-YEAR EDIT RESTRICTION
    # =========================================================
    if str(record.year) != current_year:
        flash(
            f'You can only edit records for the {current_year} season.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    # =========================================================
    # POST
    # =========================================================
    if request.method == 'POST':

        # =====================================================
        # POSITION
        # =====================================================
        position = request.form.get(
            'position',
            ''
        ).strip()

        if not position:
            flash(
                'Please select a position.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        if record.sport not in VALID_POSITIONS:
            flash(
                'Invalid sport for this record.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        if position not in VALID_POSITIONS[record.sport]:
            flash(
                f'Invalid position selected for {record.sport}.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        record.position = position

        # =====================================================
        # GAME-BY-GAME VALUES
        # =====================================================

        # IMPORTANT:
        # Do NOT put a comma after int(...).
        #
        # Wrong:
        # games_played = int(...) ,
        #
        # That creates a tuple: (1,)
        #
        # Correct:
        # games_played = int(...)

        try:
            games_played = int(
                request.form.get('games_played') or 0
            )

            man_of_the_match = int(
                request.form.get('man_of_the_match') or 0
            )

        except (TypeError, ValueError):
            flash(
                'Games Played and MOTM/QOTM must both be exactly 1 per game.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        # =====================================================
        # GAMES PLAYED MUST BE EXACTLY 1
        # =====================================================
        if games_played != 1:
            flash(
                'Games Played must be exactly 1 per game.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        # =====================================================
        # MOTM / QOTM: 0 OR 1 PER GAME (same rule as new records)
        # =====================================================
        if man_of_the_match not in (0, 1):
            flash(
                'MOTM/QOTM must be 0 or 1 per game.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        # Records are game-by-game: always exactly 1 game.
        record.games_played = 1

        # MOTM / QOTM for this game: 0 or 1, as on the new-record form.
        try:
            man_of_the_match = int(request.form.get('man_of_the_match') or 0)
        except (TypeError, ValueError):
            man_of_the_match = 0

        record.man_of_the_match = 1 if man_of_the_match >= 1 else 0

        # =====================================================
        # GAME DATE (must stay in this record's season)
        # =====================================================
        try:
            game_date = datetime.strptime(
                request.form.get('game_date', '').strip(),
                '%Y-%m-%d'
            ).date()
        except ValueError:
            flash('Please enter a valid game date.', 'danger')
            return redirect(url_for('edit_record', record_id=record.id))

        if game_date.year != record.year:
            flash(
                f'The game date must be in the {record.year} season.',
                'danger'
            )
            return redirect(url_for('edit_record', record_id=record.id))

        future_error = game_date_future_error(game_date)

        if future_error:
            flash(future_error, 'danger')
            return redirect(url_for('edit_record', record_id=record.id))

        # Same rule as new records: the same game can't be on file twice.
        edited_team = request.form.get('team', '').strip()
        edited_competition = request.form.get('competition_category', '').strip()
        duplicate = SportRecord.query.filter(
            SportRecord.id != record.id,
            SportRecord.user_id == record.user_id,
            SportRecord.sport == record.sport,
            SportRecord.year == record.year,
            SportRecord.game_date == game_date,
            SportRecord.competition_category == edited_competition,
            SportRecord.team == edited_team,
            SportRecord.status.in_(['approved', 'pending'])
        ).first()

        if duplicate:
            flash(
                f'You already have a {duplicate.sport} record for '
                f'{duplicate.competition_category} with {edited_team or "this team"} '
                f'on {game_date.strftime("%B %d, %Y")}.',
                'danger'
            )
            return redirect(url_for('edit_record', record_id=record.id))

        record.game_date = game_date

        # =====================================================
        # OTHER BASIC INFORMATION
        # =====================================================
        record.match_minutes_played = int(
            request.form.get(
                'match_minutes_played'
            ) or 0
        )

        record.team = request.form.get(
            'team',
            ''
        ).strip()

        # Optional shirt number for this game. A number entered here also
        # becomes the athlete's current number; clearing it only clears
        # this record.
        shirt_number, shirt_number_error = clean_shirt_number(
            request.form.get('shirt_number')
        )

        if shirt_number_error:
            db.session.rollback()
            flash(shirt_number_error, 'danger')
            return redirect(url_for('edit_record', record_id=record.id))

        record.shirt_number = shirt_number

        if shirt_number:
            current_user.shirt_number = shirt_number

        record.team_played_against = request.form.get(
            'team_played_against',
            ''
        ).strip()

        # =====================================================
        # COMPETITION CATEGORY
        # =====================================================
        competition_category = request.form.get(
            'competition_category',
            ''
        ).strip()

        # Competitions offered for this sport (competitions.py). An old
        # record keeps its existing competition even if no longer offered.
        if not competition_allowed(
            record.sport,
            competition_category,
            current_category=record.competition_category
        ):
            flash(
                f'{competition_category or "That"} is not a '
                f'{record.sport} competition.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        # =====================================================
        # CATEGORY PERMISSION CHECK
        # =====================================================
        # Same rule as submitting a new record: any of the athlete's
        # active registrations for this season must allow the competition.
        if not any(
            athlete_can_submit_competition(
                category,
                competition_category
            )
            for category in registered_categories
        ):
            flash(
                'You are not allowed to use this competition category.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        record.competition_category = competition_category

        # =====================================================
        # CLUB LEAGUE DIVISION
        # =====================================================

        club_division = request.form.get(
            'club_division',
            ''
        ).strip()

        VALID_CLUB_DIVISIONS = {
            '1st Division',
            '2nd Division',
            '3rd Division'
        }

        if competition_category == 'Club League':

            if club_division not in VALID_CLUB_DIVISIONS:
                flash(
                    'Please select a valid Club League Division.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'edit_record',
                        record_id=record.id
                    )
                )

        else:
            club_division = None

        record.club_division = club_division

        # =====================================================
        # AFCON / WAFU / WORLD CUP TEAM
        # =====================================================

        competition_team, competition_team_error = clean_competition_team(
            request.form,
            record.sport,
            competition_category,
            current_team=(
                record.competition_team
                if competition_category == record.competition_category
                else None
            )
        )

        if competition_team_error:
            flash(competition_team_error, 'danger')
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        record.competition_team = competition_team

        # =====================================================
        # GRASSROOTS AGE GROUP
        # =====================================================

        age_group, age_group_error = clean_age_group(
            request.form,
            competition_category
        )

        if age_group_error:
            flash(age_group_error, 'danger')
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        record.age_group = age_group

        # =====================================================
        # TROPHY
        # =====================================================
        # Rules live in competitions.py (LFA competitions for Football).
        # Checked after the division because Club League trophies depend
        # on it. An existing record may keep a retired trophy name.
        trophy_value, trophy_error = validate_trophy(
            request.form.getlist('trophy'),
            record.sport,
            competition_category,
            club_division or age_group,
            current_trophy=record.trophy
        )

        if trophy_error:
            flash(trophy_error, 'danger')
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        record.trophy = trophy_value

        # =====================================================
        # MVP
        # =====================================================

        record.mvp = int(
            request.form.get('mvp') or 0
        )

        # =====================================================
        # SPORT-SPECIFIC INFORMATION
        # =====================================================

        if record.sport == 'Football':

            record.goals = int(
                request.form.get('goals') or 0
            )

            record.assists = int(
                request.form.get('assists') or 0
            )

            record.yellow_cards = int(
                request.form.get('yellow_cards') or 0
            )

            record.red_cards = int(
                request.form.get('red_cards') or 0
            )

            if position == 'GK':

                record.clean_sheets = int(
                    request.form.get('clean_sheets') or 0
                )

                record.saves = int(
                    request.form.get('saves') or 0
                )

            else:

                record.clean_sheets = 0
                record.saves = 0

            record.rebound_type = None

        elif record.sport == 'Kickball':

            record.home_runs = int(
                request.form.get('home_runs') or 0
            )

            record.kickball_red_cards = int(
                request.form.get(
                    'kickball_red_cards'
                ) or 0
            )

            record.kickball_yellow_cards = int(
                request.form.get(
                    'kickball_yellow_cards'
                ) or 0
            )

            record.cut_base = int(
                request.form.get('cut_base') or 0
            )

            record.foul_played = int(
                request.form.get('foul_played') or 0
            )

            record.clean_sheets = 0
            record.saves = 0
            record.rebound_type = None

        else:
            # =============================================
            # BASKETBALL
            # =============================================

            record.points = int(
                request.form.get('points') or 0
            )

            record.assists = int(
                request.form.get('assists') or 0
            )

            record.blocks = int(
                request.form.get('blocks') or 0
            )

            record.sent_off = int(
                request.form.get('sent_off') or 0
            )

            # Rebound counts (never negative).
            def rebound_count(field):
                try:
                    return max(int(request.form.get(field) or 0), 0)
                except ValueError:
                    return 0

            record.offensive_rebounds = rebound_count('offensive_rebounds')
            record.defensive_rebounds = rebound_count('defensive_rebounds')

            # Older records only stored the kind of rebound. Keep it until
            # real counts are entered, which then replace it.
            if record.total_rebounds:
                record.rebound_type = None

            record.clean_sheets = 0
            record.saves = 0

        # =====================================================
        # EDITED RECORD GOES BACK TO PENDING
        # =====================================================

        record.status = 'pending'

        # =====================================================
        # SAVE
        # =====================================================

        db.session.commit()

        flash(
            'Record updated successfully and sent for coach approval.',
            'success'
        )

        return redirect(
            url_for('student_dashboard')
        )

    # =========================================================
    # GET
    # =========================================================

    return render_template(
        'edit_record.html',
        record=record,
        registered_categories=registered_categories,
        sport_competitions=competitions_for(record.sport),
        trophy_legacy=legacy_trophy_options(
            record.sport,
            record.competition_category
        )
    )

# ==========================================================
# DASHBOARD STATISTICS (Super Admin + Coach)
# ==========================================================
# Computed live from the database on every page load, and refreshed
# every 60 seconds on the page through /admin/dashboard-stats.

RECORD_DELETED_ACTION = 'sport_record.deleted'

# Subscription statuses that count as a paying subscriber.
PAID_SUBSCRIPTION_STATUSES = ('active',)


def paid_subscriber_ids():
    """IDs of users with a paid (active) subscription."""
    return {
        user_id
        for (user_id,) in db.session.query(Subscription.user_id)
        .filter(Subscription.status.in_(PAID_SUBSCRIPTION_STATUSES))
        .distinct()
    }


def super_admin_dashboard_stats():
    records_by_status = dict(
        db.session.query(SportRecord.status, func.count(SportRecord.id))
        .group_by(SportRecord.status)
        .all()
    )
    records_existing = sum(records_by_status.values())
    records_deleted = AuditLog.query.filter_by(action=RECORD_DELETED_ACTION).count()

    users_by_role = dict(
        db.session.query(User.role, func.count(User.id))
        .group_by(User.role)
        .all()
    )
    total_users = sum(users_by_role.values())

    paid_ids = paid_subscriber_ids()
    paid_users = User.query.filter(User.id.in_(paid_ids)).count() if paid_ids else 0

    def role_count(role, **filters):
        return User.query.filter_by(role=role, **filters).count()

    total_scouts = users_by_role.get('Scout', 0)
    paid_scouts = (
        User.query.filter(User.role == 'Scout', User.id.in_(paid_ids)).count()
        if paid_ids else 0
    )

    return {
        'records_uploaded': records_existing + records_deleted,
        'records_approved': records_by_status.get('approved', 0),
        'records_pending': records_by_status.get('pending', 0),
        'records_rejected': records_by_status.get('rejected', 0),
        'records_deleted': records_deleted,
        'users_total': total_users,
        'users_free': total_users - paid_users,
        'users_paid': paid_users,
        'athletes_total': users_by_role.get('Athlete', 0),
        'coaches_approved': role_count('Coach', is_verified=True),
        'coaches_pending': role_count('Coach', is_verified=False),
        'scouts_approved': role_count('Scout', is_verified=True),
        'scouts_pending': role_count('Scout', is_verified=False),
        'scouts_free': total_scouts - paid_scouts,
        'scouts_paid': paid_scouts,
    }


def coach_scope_records(coach):
    """
    Records a coach manages: the same rule as the approval queue —
    team played for matches the coach's school/team (ignoring case and
    spaces) and the coach's category allows the competition.
    """
    team = (coach.school or '').strip().casefold()

    if not team:
        return []

    candidates = SportRecord.query.filter(
        func.lower(func.trim(SportRecord.team)) == team
    ).all()

    return [
        record
        for record in candidates
        if (record.team or '').strip().casefold() == team
        and coach_can_manage_competition(
            coach.coach_category,
            record.competition_category
        )
    ]


def coach_dashboard_data(coach):
    records = coach_scope_records(coach)

    by_status = {'approved': 0, 'pending': 0, 'rejected': 0}

    for record in records:
        by_status[record.status] = by_status.get(record.status, 0) + 1

    # Verified athletes who played for the coach's team(s), grouped by
    # the team name as entered on their records.
    teams = {}

    for record in records:
        athlete = record.user

        if not athlete or athlete.role != 'Athlete' or not athlete.is_verified:
            continue

        team_key = (record.team or '').strip().casefold()
        team = teams.setdefault(team_key, {
            'name': (record.team or '').strip(),
            'athletes': {}
        })

        row = team['athletes'].setdefault(athlete.id, {
            'athlete': athlete,
            'sports': set(),
            'positions': set(),
            'competitions': set(),
            'records': 0,
            'approved': 0,
            'pending': 0,
            'last_game': None
        })

        row['sports'].add(record.sport)
        if record.position:
            row['positions'].add(record.position)
        if record.competition_category:
            row['competitions'].add(record.competition_category)
        row['records'] += 1
        if record.status == 'approved':
            row['approved'] += 1
        elif record.status == 'pending':
            row['pending'] += 1
        if record.game_date and (row['last_game'] is None or record.game_date > row['last_game']):
            row['last_game'] = record.game_date

    managed_teams = []

    for team in sorted(teams.values(), key=lambda t: t['name'].casefold()):
        athletes = sorted(
            team['athletes'].values(),
            key=lambda row: (row['athlete'].full_name or '').casefold()
        )
        for row in athletes:
            row['sports'] = sorted(row['sports'])
            row['positions'] = sorted(row['positions'])
            row['competitions'] = sorted(row['competitions'])
        managed_teams.append({'name': team['name'], 'athletes': athletes})

    athletes_managed = len({
        row['athlete'].id
        for team in managed_teams
        for row in team['athletes']
    })

    stats = {
        'records_uploaded': len(records),
        'records_approved': by_status.get('approved', 0),
        'records_pending': by_status.get('pending', 0),
        'records_rejected': by_status.get('rejected', 0),
        'athletes_managed': athletes_managed,
    }

    # Approved records the coach can view (read-only), newest game first.
    approved_records = sorted(
        (record for record in records if record.status == 'approved'),
        key=lambda record: (record.game_date or datetime.min.date(), record.id),
        reverse=True
    )

    return stats, managed_teams, approved_records


@app.route('/admin/dashboard-stats')
@login_required
def admin_dashboard_stats():
    """Live dashboard numbers for the auto-refresh on /admin."""
    if current_user.role == 'System':
        stats = super_admin_dashboard_stats()
    elif current_user.role == 'Coach':
        stats, _, _ = coach_dashboard_data(current_user)
    else:
        return _no_store_json({'error': 'not_allowed'}, 403)

    return _no_store_json({
        'stats': stats,
        'updated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    })


@app.route('/admin')
@login_required
def admin_dashboard():
    # Only Coaches and Super Admins can access this dashboard; everyone
    # else goes to their own (no redirect loops between dashboards).
    if current_user.role not in ('Coach', 'System'):
        return redirect(url_for(dashboard_endpoint_for(current_user)))

    # ==========================================================
    # PENDING ATHLETE VERIFICATIONS
    # ==========================================================
    if current_user.role == 'System':
        pending_users = User.query.filter_by(
            role='Athlete',
            is_verified=False
        ).all()
    else:
        pending_users = User.query.filter_by(
            role='Athlete',
            is_verified=False,
            school=current_user.school
        ).all()

    # ==========================================================
    # PENDING COACH APPROVALS
    # ==========================================================
    pending_coaches = []

    if current_user.role == 'System':
        pending_coaches = User.query.filter_by(
            role='Coach',
            is_verified=False
        ).all()

    # ==========================================================
    # PENDING SCOUT APPROVALS
    # SUPER ADMIN ONLY
    # ==========================================================
    pending_scouts = []

    if current_user.role == 'System':
        pending_scouts = User.query.filter_by(
            role='Scout',
            is_verified=False
        ).all()

    # ==========================================================
    # PENDING SPORTS RECORDS
    # ==========================================================
    all_pending_records = SportRecord.query.join(User).filter(
        SportRecord.status.in_(['pending', 'rejected'])
    ).order_by(
        SportRecord.status.desc()
    ).all()

    # System sees every pending record.
    # Coaches are restricted by exact team/school matching
    # and their coach competition category.
    if current_user.role == 'System':
        pending_records = all_pending_records

    else:
        pending_records = [
            record
            for record in all_pending_records
            if current_user.school.strip().casefold()
            == (record.team or '').strip().casefold()
        ]

        pending_records = [
            record
            for record in pending_records
            if coach_can_manage_competition(
                current_user.coach_category,
                record.competition_category
            )
        ]

    # ==========================================================
    # DASHBOARD
    # ==========================================================
    managed_teams = []
    approved_records = []

    if current_user.role == 'System':
        dashboard_stats = super_admin_dashboard_stats()
    else:
        dashboard_stats, managed_teams, approved_records = coach_dashboard_data(current_user)

    return render_template(
        'admin_dashboard.html',
        pending_users=pending_users,
        pending_coaches=pending_coaches,
        pending_scouts=pending_scouts,
        pending_records=pending_records,
        dashboard_stats=dashboard_stats,
        managed_teams=managed_teams,
        approved_records=approved_records
    )

@app.route('/approve_coach/<int:user_id>', methods=['POST'])
@login_required
def approve_coach(user_id):
    # Only Super Admin can approve coaches
    if current_user.role != 'System':
        flash('Only Super Admin can approve coaches.', 'danger')
        return redirect(url_for('admin_dashboard'))

    coach = User.query.get_or_404(user_id)

    if coach.role != 'Coach':
        flash('Invalid request.', 'danger')
        return redirect(url_for('admin_dashboard'))

    coach.is_verified = True
    db.session.commit()
    flash(f'Coach {coach.full_name} ({coach.school}) has been approved.', 'success')
    return redirect(url_for('admin_dashboard'))

# ==========================================================
# RECRUITMENT HIGHLIGHTS
# Calculates recruiter-facing statistics from pinned records.
# Only approved records should ever be passed into this helper.
# ==========================================================

def build_recruitment_highlights(pinned_records):
    highlights = []

    if not pinned_records:
        return highlights

    records = [
        pinned.sport_record
        for pinned in pinned_records
        if pinned.sport_record
        and pinned.sport_record.status == 'approved'
    ]

    if not records:
        return highlights

    # Group records by sport so statistics from different sports
    # are never incorrectly combined.
    sports = {}

    for record in records:
        sport_name = (record.sport or 'Other').strip()

        if sport_name not in sports:
            sports[sport_name] = []

        sports[sport_name].append(record)

    for sport_name, sport_records in sports.items():

        highlight = {
            'sport': sport_name,
            'games': sum(
                (record.games_played or 0)
                for record in sport_records
            ),
            'records': len(sport_records),
            'minutes': sum(
                (record.match_minutes_played or 0)
                for record in sport_records
            ),
            'goals': 0,
            'assists': 0,
            'points': 0,
            'blocks': 0,
            'clean_sheets': 0,
            'saves': 0,
            'home_runs': 0,
            'cut_base': 0,
            'foul_played': 0,
            'man_of_the_match': 0,
            'mvp': 0,
            'yellow_cards': 0,
            'red_cards': 0,
            'trophies': []
        }

        for record in sport_records:

            highlight['goals'] += record.goals or 0
            highlight['assists'] += record.assists or 0
            highlight['points'] += record.points or 0
            highlight['blocks'] += record.blocks or 0
            highlight['clean_sheets'] += record.clean_sheets or 0
            highlight['saves'] += record.saves or 0
            highlight['home_runs'] += record.home_runs or 0
            highlight['cut_base'] += record.cut_base or 0
            highlight['foul_played'] += record.foul_played or 0
            highlight['man_of_the_match'] += (
                record.man_of_the_match or 0
            )
            highlight['mvp'] += record.mvp or 0
            highlight['yellow_cards'] += record.yellow_cards or 0
            highlight['red_cards'] += record.red_cards or 0

            if record.trophy:
                trophy_name = record.trophy.strip()

                if (
                    trophy_name
                    and trophy_name not in highlight['trophies']
                ):
                    highlight['trophies'].append(trophy_name)

        highlights.append(highlight)

    return highlights
#==============Approve Scout===================================
@app.route('/approve_scout/<int:user_id>', methods=['POST'])
@login_required
def approve_scout(user_id):

    # Only Super Admin can approve Scouts.
    if current_user.role != 'System':
        flash(
            'Only Super Admin can approve Scout accounts.',
            'danger'
        )
        return redirect(url_for('admin_dashboard'))

    scout = User.query.get_or_404(user_id)

    # Never allow this route to approve another role.
    if scout.role != 'Scout':
        flash(
            'Invalid Scout approval request.',
            'danger'
        )
        return redirect(url_for('admin_dashboard'))

    # Do not approve an account that is already verified.
    if scout.is_verified:
        flash(
            f'{scout.full_name} is already verified.',
            'info'
        )
        return redirect(url_for('admin_dashboard'))

    try:
        scout.is_verified = True

        create_audit_log(
            action='scout_account_approved',
            actor_user_id=current_user.id,
            target_type='User',
            target_id=scout.id,
            details={
                'role': 'Scout',
                'scout_name': scout.full_name
            }
        )

        db.session.commit()

    except Exception:
        db.session.rollback()

        app.logger.exception(
            'Scout approval failed.'
        )

        flash(
            'Scout approval could not be completed. Please try again.',
            'danger'
        )

        return redirect(url_for('admin_dashboard'))

    flash(
        f'Scout {scout.full_name} has been approved successfully.',
        'success'
    )

    return redirect(url_for('admin_dashboard'))
#============Profile route================================
#============Profile route================================
@app.route('/profile')
@login_required
def profile():
    # Organizations manage their details in Team HQ settings.
    if current_user.role == 'Organization':
        return redirect(url_for('organization_settings'))

    current_year = datetime.utcnow().year

    if current_user.role == 'Athlete':
        current_registration = Registration.query.filter_by(
            user_id=current_user.id,
            registration_type='athlete',
            registration_year=current_year,
            status='active'
        ).order_by(
            Registration.id.asc()
        ).first()

        if current_registration:
            if not current_user.athlete_category:
                current_user.athlete_category = (
                    current_registration.category
                )
                db.session.commit()

    elif current_user.role == 'Coach':
        current_registration = Registration.query.filter_by(
            user_id=current_user.id,
            registration_type='coach',
            registration_year=current_year,
            status='active'
        ).order_by(
            Registration.id.asc()
        ).first()

        if current_registration:
            if not current_user.coach_category:
                current_user.coach_category = (
                    current_registration.category
                )
                db.session.commit()

    premium_access = False
    premium_profile = None
    pinned_records = []
    approved_records = []
    recruitment_highlights = []

    if current_user.role == 'Athlete':
        premium_access = has_premium_profile_access(
            current_user.id
        )

        if premium_access:
            premium_profile = get_or_create_premium_profile(
                current_user.id
            )

            pinned_records = PinnedSportRecord.query.filter_by(
                user_id=current_user.id
            ).join(
                SportRecord,
                PinnedSportRecord.sport_record_id == SportRecord.id
            ).filter(
                SportRecord.status == 'approved'
            ).order_by(
                PinnedSportRecord.display_order.asc()
            ).all()
            recruitment_highlights = build_recruitment_highlights(
            pinned_records)

            approved_records = SportRecord.query.filter_by(
            user_id=current_user.id,status='approved').order_by(SportRecord.game_date.desc(),SportRecord.id.desc()).all()


    return render_template(
    'profile.html',
    premium_access=premium_access,
    premium_profile=premium_profile,
    pinned_records=pinned_records,
    approved_records=approved_records,
    recruitment_highlights=recruitment_highlights,
    highlights=highlights_for(current_user.id) if current_user.role == 'Athlete' else [],
    listed_by=organizations_listing(current_user) if current_user.role == 'Athlete' else []
)

#========Read-only user profile route=========
@app.route('/user/<int:user_id>')
def user_profile(user_id):
    user = db.session.get(User, user_id)

    if not user:
        abort(404)

    # Athletes' canonical public address is /athletes/<id>/<name> (SEO);
    # this page shows the same content and points there.
    return render_user_profile(user)


def render_user_profile(user):
    # If the account owner is logged in and viewing their own profile,
    # send them to their normal editable My Profile page.
    if current_user.is_authenticated and user.id == current_user.id:
        return redirect(url_for('profile'))

    current_year = datetime.utcnow().year

    current_category = None
    registration_type = None

    if user.role == 'Athlete':
        registration_type = 'athlete'

    elif user.role == 'Coach':
        registration_type = 'coach'

    if registration_type:
        current_registration = Registration.query.filter_by(
            user_id=user.id,
            registration_type=registration_type,
            registration_year=current_year,
            status='active'
        ).order_by(
            Registration.id.asc()
        ).first()

        if current_registration:
            current_category = current_registration.category

    elif user.role == 'System':
        current_category = 'System'

    premium_profile = None
    pinned_records = []
    recruitment_highlights = []
    public_records = public_records_for(user, current_user) if user.role == 'Athlete' else []

    if user.role == 'Athlete':

        if has_premium_profile_access(user.id):

            premium_profile = PremiumProfile.query.filter_by(
                user_id=user.id,
                is_enabled=True,
                is_public=True
            ).first()

            if premium_profile:

                pinned_records = PinnedSportRecord.query.filter_by(
                    user_id=user.id
                ).join(
                    SportRecord,
                    PinnedSportRecord.sport_record_id == SportRecord.id
                ).filter(
                    SportRecord.status == 'approved'
                ).order_by(
                    PinnedSportRecord.display_order.asc()
                ).all()
                recruitment_highlights = build_recruitment_highlights(
                pinned_records)
    return render_template(
    'user_profile.html',
    user=user,
    current_category=current_category,
    premium_profile=premium_profile,
    pinned_records=pinned_records,
    recruitment_highlights=recruitment_highlights,
    highlights=highlights_for(user.id) if user.role == 'Athlete' else [],
    public_records=public_records,
    career=career_summary(public_records),
    seo=athlete_seo(user, public_records) if user.role == 'Athlete' else None,
    indexable=seo_indexable(user),
)
# ==========================================================

# PUBLIC RECRUIT-READY ATHLETE PROFILE

# ==========================================================

@app.route('/athlete/<int:user_id>/recruit-ready')
def recruit_ready_profile(user_id):
    user = User.query.get_or_404(user_id)

    if user.role != 'Athlete':
        abort(404)

    if not has_premium_profile_access(user.id):
        abort(404)

    premium_profile = PremiumProfile.query.filter_by(
        user_id=user.id,
        is_enabled=True,
        is_public=True
    ).first()

    if not premium_profile:
        abort(404)

    pinned_records = (
        PinnedSportRecord.query
        .filter_by(user_id=user.id)
        .join(
            SportRecord,
            PinnedSportRecord.sport_record_id == SportRecord.id
        )
        .filter(
            SportRecord.status == 'approved'
        )
        .order_by(
            PinnedSportRecord.display_order.asc()
        )
        .all()
    )

    recruitment_highlights = build_recruitment_highlights(
        pinned_records
    )

    current_year = datetime.now(timezone.utc).year

    current_registration = Registration.query.filter_by(
        user_id=user.id,
        registration_type='athlete',
        registration_year=current_year,
        status='active'
    ).order_by(
        Registration.id.asc()
    ).first()

    current_category = None

    if current_registration:
        current_category = current_registration.category

    return render_template(
        'recruit_ready_profile.html',
        user=user,
        premium_profile=premium_profile,
        pinned_records=pinned_records,
        recruitment_highlights=recruitment_highlights,
        current_category=current_category
    )
#=========Update profile route========================================
@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    # Update school/team
    new_school = request.form.get('school', '').strip()
    if new_school:
        current_user.school = new_school

    # Update gender
    new_gender = request.form.get('gender', '').strip()
    if new_gender in ['Male', 'Female']:
        current_user.gender = new_gender

    # ==========================================================
    # UPDATE ATHLETE PERSONAL INFORMATION
    # ==========================================================

    new_age = request.form.get('age', '').strip()
    new_dob = request.form.get('date_of_birth', '').strip()
    new_nationality = request.form.get('nationality', '').strip()
    new_height = request.form.get('height_cm', '').strip()
    new_weight = request.form.get('weight_kg', '').strip()
    new_preferred_foot = request.form.get('preferred_foot', '').strip()

    # ---------- AGE ----------
    if new_age:
        try:
            new_age = int(new_age)
        except ValueError:
            flash('Age must be a valid number.', 'danger')
            return redirect(url_for('profile'))

        if new_age < 1 or new_age > 120:
            flash('Please enter a valid age.', 'danger')
            return redirect(url_for('profile'))

        current_user.age = new_age
    else:
        current_user.age = None

    # ---------- DATE OF BIRTH ----------
    if new_dob:
        try:
            parsed_dob = datetime.strptime(
                new_dob,
                '%Y-%m-%d'
            ).date()
        except ValueError:
            flash('Invalid date of birth.', 'danger')
            return redirect(url_for('profile'))

        if parsed_dob > datetime.utcnow().date():
            flash('Date of birth cannot be in the future.', 'danger')
            return redirect(url_for('profile'))

        current_user.date_of_birth = parsed_dob
    else:
        current_user.date_of_birth = None

    # ---------- NATIONALITY ----------
    if new_nationality:
        current_user.nationality = new_nationality
    else:
        current_user.nationality = None

    # ---------- HEIGHT ----------
    if new_height:
        try:
            height_value = float(new_height)
        except ValueError:
            flash('Height must be a valid number.', 'danger')
            return redirect(url_for('profile'))

        if height_value <= 0:
            flash('Height must be greater than zero.', 'danger')
            return redirect(url_for('profile'))

        current_user.height_cm = height_value
    else:
        current_user.height_cm = None

    # ---------- WEIGHT ----------
    if new_weight:
        try:
            weight_value = float(new_weight)
        except ValueError:
            flash('Weight must be a valid number.', 'danger')
            return redirect(url_for('profile'))

        if weight_value <= 0:
            flash('Weight must be greater than zero.', 'danger')
            return redirect(url_for('profile'))

        current_user.weight_kg = weight_value
    else:
        current_user.weight_kg = None

    # ---------- PREFERRED FOOT ----------
    if new_preferred_foot not in ['', 'Right', 'Left', 'Both']:
        flash('Invalid preferred foot selection.', 'danger')
        return redirect(url_for('profile'))

    current_user.preferred_foot = (
        new_preferred_foot or None
    )

    # ---------- GOOGLE LISTING (athletes) ----------
    if current_user.role == 'Athlete' and 'search_visibility' in request.form:
        choice = request.form.get('search_visibility', 'default')

        if choice not in ('default', 'on', 'off'):
            flash('Invalid Google listing choice.', 'danger')
            return redirect(url_for('profile'))

        current_user.search_visibility = None if choice == 'default' else choice

    # ---------- SHIRT NUMBER (athletes; optional, blank clears it) ----------
    if current_user.role == 'Athlete' and 'shirt_number' in request.form:
        new_shirt_number, shirt_number_error = clean_shirt_number(
            request.form.get('shirt_number')
        )

        if shirt_number_error:
            flash(shirt_number_error, 'danger')
            return redirect(url_for('profile'))

        current_user.shirt_number = new_shirt_number

    # ==========================================================
    # UPDATE ATHLETE CATEGORY
    # ==========================================================
    if current_user.role == 'Athlete':
        new_athlete_category = request.form.get('athlete_category', '').strip()

        valid_athlete_categories = {
            'All Athlete',
            'County Meet Athlete',
            'Club League Athlete',
            'University Athlete',
            'Community/Area League Athlete',
            'High School Athlete'
        }

        if new_athlete_category:
            if new_athlete_category not in valid_athlete_categories:
                flash('Invalid athlete registration category.', 'danger')
                return redirect(url_for('profile'))

            # Premium is priced by category, so the category can't change
            # while a plan bought for it is running.
            running_plan = current_plan(current_user)

            if (
                running_plan
                and running_plan.category
                and new_athlete_category != current_user.athlete_category
            ):
                flash(
                    f'Your category can be changed after your {running_plan.name} '
                    'plan ends, because Premium is priced by category.',
                    'warning'
                )
                return redirect(url_for('profile'))

            current_year = datetime.utcnow().year
            active_athlete_registrations = Registration.query.filter_by(
                user_id=current_user.id,
                registration_type='athlete',
                registration_year=current_year,
                status='active'
            ).all()

            selected_active = next(
                (r for r in active_athlete_registrations
                 if r.category == new_athlete_category),
                None
            )

            # Keep exactly one current-year athlete category active.
            for registration in active_athlete_registrations:
                if registration is not selected_active:
                    registration.status = 'inactive'

            if not selected_active:
                existing_registration = Registration.query.filter_by(
                    user_id=current_user.id,
                    registration_type='athlete',
                    category=new_athlete_category,
                    registration_year=current_year
                ).first()

                if existing_registration:
                    existing_registration.status = 'active'
                else:
                    db.session.add(Registration(
                        user_id=current_user.id,
                        registration_type='athlete',
                        category=new_athlete_category,
                        registration_year=current_year,
                        fee_amount=0,
                        payment_status='unpaid',
                        status='active'
                    ))

            current_user.athlete_category = new_athlete_category

    # ==========================================================
    # UPDATE COACH CATEGORY
    # ==========================================================
    if current_user.role == 'Coach':
        new_coach_category = request.form.get('coach_category', '').strip()

        valid_coach_categories = {
            'All Coach',
            'County Meet Coach',
            'Club League Coach',
            'University Coach',
            'Community/Area League Coach',
            'High School Coach'
        }

        if new_coach_category:
            if new_coach_category not in valid_coach_categories:
                flash('Invalid coach registration category.', 'danger')
                return redirect(url_for('profile'))

            current_year = datetime.utcnow().year
            active_coach_registrations = Registration.query.filter_by(
                user_id=current_user.id,
                registration_type='coach',
                registration_year=current_year,
                status='active'
            ).all()

            selected_active = next(
                (r for r in active_coach_registrations
                 if r.category == new_coach_category),
                None
            )

            # Keep exactly one current-year coach category active.
            for registration in active_coach_registrations:
                if registration is not selected_active:
                    registration.status = 'inactive'

            if not selected_active:
                existing_registration = Registration.query.filter_by(
                    user_id=current_user.id,
                    registration_type='coach',
                    category=new_coach_category,
                    registration_year=current_year
                ).first()

                if existing_registration:
                    existing_registration.status = 'active'
                else:
                    db.session.add(Registration(
                        user_id=current_user.id,
                        registration_type='coach',
                        category=new_coach_category,
                        registration_year=current_year,
                        fee_amount=0,
                        payment_status='unpaid',
                        status='active'
                    ))

            current_user.coach_category = new_coach_category

    # ==========================================================
    # HANDLE PROFILE PICTURE UPLOAD
    # ==========================================================
    file = request.files.get('profile_picture')
    if file and file.filename != '':
        if allowed_file(file.filename):
            actual_file_type = valid_file_content(file)

            if actual_file_type not in ['png', 'jpg']:
                flash('The uploaded profile picture is not a valid image.', 'danger')
                return redirect(url_for('profile'))

            extension = file.filename.rsplit('.', 1)[1].lower()

            if extension == 'png' and actual_file_type != 'png':
                flash('The uploaded image is not a valid PNG file.', 'danger')
                return redirect(url_for('profile'))

            if extension in ['jpg', 'jpeg'] and actual_file_type != 'jpg':
                flash('The uploaded image is not a valid JPEG file.', 'danger')
                return redirect(url_for('profile'))

            filename = secure_filename(file.filename)
            old_profile_picture_path = current_user.profile_picture
            storage_path = f"profiles/{current_user.id}_{uuid.uuid4().hex}_{filename}"

            try:
                upload_to_supabase(file, 'profile-pictures', storage_path)
                current_user.profile_picture = storage_path

                if old_profile_picture_path:
                    try:
                        supabase.storage.from_('profile-pictures').remove([old_profile_picture_path])
                    except Exception as e:
                        print('Old profile picture deletion error:', e)

            except Exception as e:
                print('Supabase profile picture upload error:', e)
                flash('There was a problem uploading your profile picture. Please try again.', 'danger')
                return redirect(url_for('profile'))
        else:
            flash('Only PNG, JPG or JPEG allowed.', 'danger')
            return redirect(url_for('profile'))

    db.session.commit()
    flash('Profile updated successfully!', 'success')
    return redirect(url_for('profile'))

@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    # Verify current password
    if not current_user.check_password(current_password):
        flash('Your current password is incorrect.', 'danger')
        return redirect(url_for('profile'))

    # Check password strength
    if not is_strong_password(new_password):
        flash(
            'New password must be at least 10 characters and include '
            'an uppercase letter, lowercase letter, number, '
            'and special character.',
            'danger'
        )
        return redirect(url_for('profile'))

    # Prevent reusing the current password
    if current_user.check_password(new_password):
        flash(
            'Your new password must be different from your current password.',
            'danger'
        )
        return redirect(url_for('profile'))

    # Confirm new password
    if new_password != confirm_password:
        flash('New passwords do not match.', 'danger')
        return redirect(url_for('profile'))

    # Set and save the new password
    current_user.set_password(new_password)
    db.session.commit()

    # Force the user to log in again
    logout_user()

    flash(
        'Your password has been changed successfully. Please log in again.',
        'success'
    )

    return redirect(url_for('login'))


# ==========================================================
# PERMANENT ACCOUNT DELETION
# ==========================================================

@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    current_password = request.form.get('current_password', '')

    if not current_user.check_password(current_password):
        flash('Your password is incorrect. Your account was not deleted.', 'danger')
        return redirect(url_for('profile'))

    if request.form.get('confirm_delete') != 'yes':
        flash(
            'Please confirm that you understand the account deletion is permanent.',
            'danger'
        )
        return redirect(url_for('profile'))

    profile_picture_path = current_user.profile_picture
    id_document_path = current_user.id_document
    user_id = current_user.id
    highlight_paths = [
        h.storage_path
        for h in AthleteHighlight.query.filter_by(user_id=user_id).all()
    ]

    # Remove associated Storage objects before deleting database data.
    # If Storage reports an error, keep the account intact.
    try:
        if profile_picture_path:
            supabase.storage.from_('profile-pictures').remove([profile_picture_path])

        if id_document_path:
            supabase.storage.from_('id-documents').remove([id_document_path])

        if highlight_paths:
            supabase.storage.from_(HIGHLIGHTS_BUCKET).remove(highlight_paths)

    except Exception as e:
        print('ACCOUNT STORAGE DELETION ERROR:', e)
        flash(
            'We could not remove your profile files. Your account was not deleted.',
            'danger'
        )
        return redirect(url_for('profile'))

    try:
        SportRecord.query.filter_by(
            user_id=user_id
        ).delete(synchronize_session=False)

        Registration.query.filter_by(
            user_id=user_id
        ).delete(synchronize_session=False)

        SavedSearch.query.filter_by(
            user_id=user_id
        ).delete(synchronize_session=False)

        AthleteHighlight.query.filter_by(
            user_id=user_id
        ).delete(synchronize_session=False)

        user = db.session.get(User, user_id)
        if user:
            db.session.delete(user)

        db.session.commit()

        logout_user()
        flash(
            'Your account and all associated data have been permanently deleted.',
            'success'
        )
        return redirect(url_for('login'))

    except Exception as e:
        db.session.rollback()
        print('ACCOUNT DATABASE DELETION ERROR:', e)
        flash(
            'Your files were removed, but we could not delete the account data. '
            'Please contact an administrator before trying again.',
            'danger'
        )
        return redirect(url_for('profile'))

@app.route('/verify_user/<int:user_id>', methods=['POST'])
@login_required
def verify_user(user_id):
    if current_user.role not in ('Coach', 'System'):
        return redirect(url_for('index'))

    user = User.query.get_or_404(user_id)

    # Security check
    if current_user.role != 'System' and user.school != current_user.school:
        flash('You can only verify athletes from your own school.', 'danger')
        return redirect(url_for('admin_dashboard'))

    user.is_verified = True
    db.session.commit()
    flash(f'{user.full_name} has been verified.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/approve_record/<int:record_id>', methods=['POST'])
@login_required
def approve_record(record_id):
    if current_user.role not in ('Coach', 'System'):
        return redirect(url_for('index'))

    record = SportRecord.query.get_or_404(record_id)

    # System can manage every record; Coaches are restricted to their school/team and category.
    if current_user.role != 'System':
        if current_user.school.strip().casefold() != (record.team or '').strip().casefold():
            flash('You can only manage records from your own school/team.', 'danger')
            return redirect(url_for('admin_dashboard'))

        if not coach_can_manage_competition(
            current_user.coach_category,
            record.competition_category
        ):
            flash(
                'You are not authorized to manage records from this competition category.',
                'danger'
            )
            return redirect(url_for('admin_dashboard'))

    record.status = 'approved'
    db.session.commit()
    flash('Record approved.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/reject_record/<int:record_id>', methods=['POST'])
@login_required
def reject_record(record_id):
    if current_user.role not in ('Coach', 'System'):
        return redirect(url_for('index'))

    record = SportRecord.query.get_or_404(record_id)

    # System can manage every record.
    # Coaches can only manage records from their own school/team
    # and their authorized competition category.
    if current_user.role != 'System':
        if current_user.school.strip().casefold() != (record.team or '').strip().casefold():
            flash(
                'You can only manage records from your own school/team.',
                'danger'
            )
            return redirect(url_for('admin_dashboard'))

        if not coach_can_manage_competition(
            current_user.coach_category,
            record.competition_category
        ):
            flash(
                'You are not authorized to manage records from this competition category.',
                'danger'
            )
            return redirect(url_for('admin_dashboard'))

        # Approved records are final for coaches: they can view them on
        # their dashboard but not reject them (which would also let the
        # athlete delete them).
        if record.status == 'approved':
            flash("Approved records can't be rejected.", 'warning')
            return redirect(url_for('admin_dashboard'))

    record.status = 'rejected'
    db.session.commit()

    flash('Record rejected.', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/reject_user/<int:user_id>', methods=['POST'])
@login_required
def reject_user(user_id):

    # Only Coaches and System can reject athletes
    if current_user.role not in ('Coach', 'System'):
        return redirect(url_for('index'))

    user = User.query.get_or_404(user_id)

    # Only Athlete accounts can be rejected here
    if user.role != 'Athlete':
        flash('Invalid athlete account.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Regular Coaches can only reject athletes
    # from their own school/team.
    if current_user.role == 'Coach':
        if current_user.school.strip().casefold() != user.school.strip().casefold():
            flash(
                'You can only reject athletes from your own school/team.',
                'danger'
            )
            return redirect(url_for('admin_dashboard'))

    # Already verified athletes cannot be rejected from the
    # pending-registration section.
    if user.is_verified:
        flash(
            'This athlete is already verified and cannot be rejected.',
            'warning'
        )
        return redirect(url_for('admin_dashboard'))

    try:
        # Delete the athlete's sports records first.
        SportRecord.query.filter_by(
            user_id=user.id
        ).delete(synchronize_session=False)

        # Delete the athlete's registrations before deleting the User.
        Registration.query.filter_by(
            user_id=user.id
        ).delete(synchronize_session=False)

        # Delete the athlete account.
        db.session.delete(user)

        db.session.commit()

        flash(
            f'Athlete {user.full_name} has been rejected and removed.',
            'info'
        )

    except Exception as e:
        db.session.rollback()

        print('REJECT ATHLETE ERROR:', e)

        flash(
            'The athlete could not be rejected. Please try again.',
            'danger'
        )

    return redirect(url_for('admin_dashboard'))

@app.route('/register-coach', methods=['GET', 'POST'])
@app.route('/register-coach/<registration_category>', methods=['GET', 'POST'])
def register_coach(registration_category=None):
    coach_category_map = {
        'all-coach': 'All Coach',
        'county-meet': 'County Meet Coach',
        'club-league': 'Club League Coach',
        'university': 'University Coach',
        'community-league': 'Community/Area League Coach',
        'high-school': 'High School Coach'
    }

    if registration_category:
        if registration_category not in coach_category_map:
            flash('Invalid coach registration category.', 'danger')
            return redirect(url_for('register_coach'))
        selected_category = coach_category_map[registration_category]
    else:
        selected_category = 'All Coach'

    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        school = request.form['school'].strip()
        gender = request.form.get('gender', '').strip()
        nationality = request.form.get('nationality', '').strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']

        # ======== PASSWORD STRENGTH CHECK ========== #
        if not is_strong_password(password):
            flash(
                'Password must be at least 8 characters and include '
                'an uppercase letter, lowercase letter, number, '
                'and special character.',
                'danger'
            )
            return redirect(url_for(
                'register_coach',
                registration_category=registration_category
            ))

        secret_code = request.form['secret_code'].strip()

        # Email must be unique.
        existing_email = User.query.filter_by(email=email).first()
        if existing_email:
            flash('An account with this email address already exists.', 'danger')
            return redirect(url_for(
                'register_coach',
                registration_category=registration_category
            ))

        if gender not in ['Male', 'Female']:
            flash('Please select a valid gender.', 'danger')
            return redirect(url_for(
                'register_coach',
                registration_category=registration_category
            ))

        if not nationality:
            flash('Nationality is required.', 'danger')
            return redirect(url_for(
                'register_coach',
                registration_category=registration_category
            ))

        # Check secret code
        expected_code = app.config.get('COACH_SECRET_CODE')
        if not expected_code:
            flash('Coach registration is temporarily unavailable.', 'danger')
            return redirect(url_for(
                'register_coach',
                registration_category=registration_category
            ))

        if secret_code != expected_code:
            flash('Invalid Coach Secret Code.', 'danger')
            return redirect(url_for(
                'register_coach',
                registration_category=registration_category
            ))

        # Check duplicate coach name + school/team.
        existing = User.query.filter_by(
            full_name=full_name,
            school=school,
            role='Coach'
        ).first()

        if existing:
            flash('A coach with this name and school already exists.', 'danger')
            return redirect(url_for(
                'register_coach',
                registration_category=registration_category
            ))

        coach = User(
            full_name=full_name,
            school=school,
            gender=gender,
            nationality=nationality,
            email=email,
            role='Coach',
            coach_category=selected_category,
            is_verified=False          # Pending Super Admin approval
        )
        coach.set_password(password)
        attach_google_signup(coach)
        db.session.add(coach)
        db.session.flush()
        record_account_created(coach)

        # Save the selected coach category for the current registration year.
        current_year = datetime.utcnow().year
        registration = Registration(
            user_id=coach.id,
            registration_type='coach',
            category=selected_category,
            registration_year=current_year,
            fee_amount=0,
            payment_status='unpaid',
            status='active'
        )
        db.session.add(registration)
        db.session.commit()

        flash(
            f'Coach account created as {selected_category}! '
            'Waiting for Super Admin approval.',
            'success'
        )
        return redirect(url_for('login'))

    return render_template(
        'register_coach.html',
        registration_category=registration_category,
        selected_category=selected_category
    )

@app.route('/register-scout', methods=['GET', 'POST'])
def register_scout():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        organization = request.form.get('organization', '').strip()
        job_title = request.form.get('job_title', '').strip()
        sports = request.form.get('sports', '').strip()
        country = request.form.get('country', '').strip()
        city = request.form.get('city', '').strip()
        years_experience_raw = request.form.get(
            'years_experience',
            ''
        ).strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        gender = request.form.get('gender', '').strip()
        nationality = request.form.get('nationality', '').strip()

        # -----------------------------
        # Required-field validation
        # -----------------------------
        if not full_name:
            flash('Full name is required.', 'danger')
            return redirect(url_for('register_scout'))

        if not organization:
            flash('Organization or scouting agency is required.', 'danger')
            return redirect(url_for('register_scout'))

        if not email:
            flash('Email address is required.', 'danger')
            return redirect(url_for('register_scout'))

        if not password:
            flash('Password is required.', 'danger')
            return redirect(url_for('register_scout'))

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('register_scout'))

        # -----------------------------
        # Length validation
        # -----------------------------
        if len(full_name) > 150:
            flash('Full name is too long.', 'danger')
            return redirect(url_for('register_scout'))

        if len(organization) > 200:
            flash('Organization name is too long.', 'danger')
            return redirect(url_for('register_scout'))

        if len(job_title) > 150:
            flash('Job title is too long.', 'danger')
            return redirect(url_for('register_scout'))

        if len(country) > 100:
            flash('Country name is too long.', 'danger')
            return redirect(url_for('register_scout'))

        if len(city) > 100:
            flash('City name is too long.', 'danger')
            return redirect(url_for('register_scout'))

        # -----------------------------
        # Password validation
        # -----------------------------
        if not is_strong_password(password):
            flash(
                'Password must meet the required security requirements.',
                'danger'
            )
            return redirect(url_for('register_scout'))

        # -----------------------------
        # Email uniqueness
        # -----------------------------
        existing_email = User.query.filter_by(
            email=email
        ).first()

        if existing_email:
            flash(
                'An account with this email address already exists.',
                'warning'
            )
            return redirect(url_for('login'))

        # -----------------------------
        # Years of experience
        # -----------------------------
        years_experience = None

        if years_experience_raw:
            try:
                years_experience = int(years_experience_raw)
            except ValueError:
                flash(
                    'Years of experience must be a valid number.',
                    'danger'
                )
                return redirect(url_for('register_scout'))

            if years_experience < 0 or years_experience > 80:
                flash(
                    'Years of experience must be between 0 and 80.',
                    'danger'
                )
                return redirect(url_for('register_scout'))

        # -----------------------------
        # Create Scout account
        # -----------------------------
        scout = User(
            full_name=full_name,
            school=organization,
            gender=gender or None,
            nationality=nationality or None,
            email=email,
            role='Scout',
            is_verified=False
        )

        scout.set_password(password)
        attach_google_signup(scout)

        try:
            db.session.add(scout)
            db.session.flush()

            scout_profile = ScoutProfile(
                user_id=scout.id,
                organization=organization,
                job_title=job_title or None,
                sports=sports or None,
                country=country or None,
                city=city or None,
                years_experience=years_experience,
                verification_status='pending'
            )

            registration = Registration(
                user_id=scout.id,
                registration_type='scout',
                category='Scout',
                registration_year=datetime.utcnow().year,
                fee_amount=0,
                payment_status='unpaid',
                status='active'
            )

            db.session.add(scout_profile)
            db.session.add(registration)
            record_account_created(scout)

            create_audit_log(
                action='scout_registration_created',
                actor_user_id=scout.id,
                target_type='User',
                target_id=scout.id,
                details={
                    'role': 'Scout',
                    'organization': organization,
                    'verification_status': 'pending'
                }
            )

            db.session.commit()

        except Exception:
            db.session.rollback()

            app.logger.exception(
                'Scout registration failed.'
            )

            flash(
                'Scout registration could not be completed. '
                'Please try again.',
                'danger'
            )

            return redirect(url_for('register_scout'))

        flash(
            'Scout account created successfully. '
            'Your account is pending verification by D.A.R.T. administration.',
            'success'
        )

        return redirect(url_for('login'))

    return render_template('register_scout.html')

# Two-Factor Authentication (2FA) Recovery Codes Route
@app.route('/admin/2fa/recovery-codes', methods=['GET', 'POST'])
@login_required
def recovery_codes():
    # Only accounts that use 2FA (admins, coaches, scouts) have recovery codes
    if current_user.role not in TWO_FACTOR_ROLES:
        flash('You are not authorized to access this page.', 'danger')
        return redirect(url_for('student_dashboard'))

    # 2FA must already be enabled
    if not current_user.two_factor_enabled:
        flash('Please enable two-factor authentication first.', 'warning')
        return redirect(url_for('setup_2fa'))

    if request.method == 'POST':
        codes = generate_recovery_codes()

        # Hash every code before storing it
        hashed_codes = hash_recovery_codes(codes)

        # Store hashes as JSON text
        import json
        current_user.two_factor_recovery_codes = json.dumps(hashed_codes)
        db.session.commit()

        # Show the plaintext codes only in this response
        return render_template(
            'recovery_codes.html',
            codes=codes,
            generated=True
        )

    return render_template(
        'recovery_codes.html',
        codes=None,
        generated=False
    )
# ========== Two-Factor Authentication (2FA) Routes ==========
@app.route('/2fa', methods=['GET', 'POST'])
def verify_2fa():
    user_id = session.get('2fa_user_id')

    if not user_id:
        flash('Your 2FA session has expired. Please log in again.', 'warning')
        return redirect(url_for('login'))

    user = db.session.get(User, user_id)

    if not user or user.role not in TWO_FACTOR_ROLES or not user.two_factor_enabled:
        session.pop('2fa_user_id', None)
        flash('Unable to verify two-factor authentication.', 'danger')
        return redirect(url_for('login'))

    # Rate-limit 2FA attempts for this account
    identifier = f"2fa:{user.id}"

    if is_rate_limited(
        identifier,
        MAX_2FA_ATTEMPTS,
        TWO_FA_BLOCK_MINUTES
    ):
        flash(
            'Too many failed two-factor authentication attempts. '
            'Please try again in 15 minutes.',
            'danger'
        )
        return redirect(url_for('login'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        recovery_code = request.form.get('recovery_code', '').strip()

        # Authenticator app code
        if code:
            if not code.isdigit() or len(code) != 6:
                record_failed_attempt(
                    identifier,
                    MAX_2FA_ATTEMPTS,
                    TWO_FA_BLOCK_MINUTES
                )

                flash(
                    'Please enter the 6-digit code from your authenticator app.',
                    'danger'
                )
                return render_template('verify_2fa.html')

            totp = pyotp.TOTP(user.two_factor_secret)

            if totp.verify(code, valid_window=1):
                reset_failed_attempts(identifier)
                session.pop('2fa_user_id', None)
                login_user(user)

                return redirect_after_login(dashboard_endpoint_for(user))

            record_failed_attempt(
                identifier,
                MAX_2FA_ATTEMPTS,
                TWO_FA_BLOCK_MINUTES
            )

            flash(
                'Invalid authenticator code. Please try again.',
                'danger'
            )

        # Recovery code
        elif recovery_code:
            import json

            stored_codes = []

            if user.two_factor_recovery_codes:
                try:
                    stored_codes = json.loads(
                        user.two_factor_recovery_codes
                    )
                except (json.JSONDecodeError, TypeError):
                    stored_codes = []

            matching_index = verify_recovery_code(
                recovery_code,
                stored_codes
            )

            if matching_index is not None:
                stored_codes.pop(matching_index)

                user.two_factor_recovery_codes = json.dumps(
                    stored_codes
                )

                db.session.commit()

                reset_failed_attempts(identifier)
                session.pop('2fa_user_id', None)
                login_user(user)

                flash(
                    'Recovery code accepted. Remember that recovery codes '
                    'can only be used once.',
                    'success'
                )

                return redirect_after_login(dashboard_endpoint_for(user))

            record_failed_attempt(
                identifier,
                MAX_2FA_ATTEMPTS,
                TWO_FA_BLOCK_MINUTES
            )

            flash(
                'Invalid or already-used recovery code.',
                'danger'
            )

        else:
            record_failed_attempt(
                identifier,
                MAX_2FA_ATTEMPTS,
                TWO_FA_BLOCK_MINUTES
            )

            flash(
                'Please enter an authenticator code or recovery code.',
                'danger'
            )

    return render_template('verify_2fa.html')

 # 2FA Setup Route
@app.route('/admin/2fa/setup', methods=['GET', 'POST'])
def setup_2fa():
    # Allow either an already logged-in admin
    # or an admin who has just authenticated with their password.
    setup_user_id = session.get('2fa_setup_user_id')

    if current_user.is_authenticated and current_user.role in TWO_FACTOR_ROLES:
        user = current_user

    elif setup_user_id:
        user = db.session.get(User, setup_user_id)

        if not user or user.role not in TWO_FACTOR_ROLES:
            session.pop('2fa_setup_user_id', None)
            flash('Unable to set up two-factor authentication.', 'danger')
            return redirect(url_for('login'))

    else:
        flash('Please log in before setting up two-factor authentication.', 'warning')
        return redirect(url_for('login'))

    # Generate a secret if the account does not have one yet
    if not user.two_factor_secret:
        user.two_factor_secret = pyotp.random_base32()
        db.session.commit()

    totp = pyotp.TOTP(user.two_factor_secret)

    # Create the authenticator-app setup URI
    provisioning_uri = totp.provisioning_uri(
        name=user.full_name,
        issuer_name='Athlete Tracker'
    )

    # Generate QR code
    qr_image = qrcode.make(provisioning_uri)
    buffer = BytesIO()
    qr_image.save(buffer, format='PNG')
    qr_code = base64.b64encode(buffer.getvalue()).decode('utf-8')

    if request.method == 'POST':
        code = request.form.get('code', '').strip()

        if not code.isdigit() or len(code) != 6:
            flash(
                'Please enter the 6-digit code from your authenticator app.',
                'danger'
            )

            return render_template(
                'setup_2fa.html',
                qr_code=qr_code,
                secret=user.two_factor_secret
            )

        if totp.verify(code, valid_window=1):
            user.two_factor_enabled = True
            db.session.commit()

            # If this was a first-time setup during login,
            # complete the login now.
            if session.get('2fa_setup_user_id'):
                session.pop('2fa_setup_user_id', None)
                login_user(user)

            flash(
                'Two-factor authentication has been enabled successfully.',
                'success'
            )

            return redirect(url_for(dashboard_endpoint_for(user)))

        flash(
            'Invalid authenticator code. Please try again.',
            'danger'
        )

    return render_template(
        'setup_2fa.html',
        qr_code=qr_code,
        secret=user.two_factor_secret
    )

if __name__ == '__main__':
    app.run(debug=False)
