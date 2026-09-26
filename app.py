import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, send_from_directory, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from models import (db,User,SportRecord,LoginAttempt,Registration,ChatMessage) 
from dotenv import load_dotenv
load_dotenv(override=True)  # This forces Python to read your local .env file
from config import Config
from datetime import datetime
from flask_migrate import Migrate
from supabase import create_client
import uuid
import pycountry
import base64
from io import BytesIO
import pyotp
import qrcode
import secrets
from flask_mail import Mail, Message
from sqlalchemy import or_


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


app = Flask(__name__)
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
    return User.query.get(int(user_id))

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

VALID_COMPETITIONS = {
    'County Meet',
    'Club League',
    'University League',
    'Community/Area League',
    'High School',

    # Additional competition categories
        # AFCON
    "AFCON - Lonestar Men's Team",

    # WAFU
    'WAFU - Male-U15',
    'WAFU - Female-U15',
    'WAFU - Male-U17',
    'WAFU - Female-U17',
    'WAFU - Male-U20',
    'WAFU - Female-U20',
    'WAFU - Male-U23',
    'WAFU - Female-U23',

    # World Cup
    "World Cup - Lonestar Men's Team"
}
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

# =================SEARCH ROUTE=========================================
@app.route('/search')
def search():
    name = request.args.get('name', '').strip()
    school = request.args.get('school', '').strip()
    team_played_against = request.args.get('team_played_against', '').strip()
    year = request.args.get('year', '').strip()
    sport = request.args.get('sport', '').strip()
    sort_by = request.args.get('sort_by', '').strip()

    # ==========================================================
    # AVAILABLE YEARS
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

    # ==========================================================
    # ATHLETE NAME
    # ==========================================================
    if name:
        query = query.filter(
            User.full_name.ilike(f'%{name}%')
        )

    # ==========================================================
    # TEAM NAME
    # Search both record.team and athlete school
    # ==========================================================
    if school:
        query = query.filter(
            or_(
                SportRecord.team.ilike(f'%{school}%'),
                User.school.ilike(f'%{school}%')
            )
        )

    # ==========================================================
    # OPPONENT TEAM
    # ==========================================================
    if team_played_against:
        query = query.filter(
            SportRecord.team_played_against.ilike(
                f'%{team_played_against}%'
            )
        )

    # ==========================================================
    # YEAR
    # ==========================================================
    if year:
        try:
            query = query.filter(
                SportRecord.year == int(year)
            )
        except ValueError:
            pass

    # ==========================================================
    # SPORT
    # ==========================================================
    valid_sports = {
        'Football',
        'Basketball',
        'Kickball'
    }

    if sport in valid_sports:
        query = query.filter(
            SportRecord.sport == sport
        )
    else:
        sport = ''

    records = query.all()

    # ==========================================================
    # METRICS
    # ==========================================================

    def trophy_count(record):
        trophy = (record.trophy or '').strip()

        if not trophy or trophy.lower() == 'none':
            return 0

        return len([
            item for item in trophy.split(',')
            if item.strip()
            and item.strip().lower() != 'none'
        ])

    def games_metric(record):
        return record.games_played or 0

    def motm_metric(record):
        return record.man_of_the_match or 0

    def mvp_metric(record):
        return record.mvp or 0

    # ==========================================================
    # SPORT-SPECIFIC METRICS
    # ==========================================================

    def football_goals(record):
        return record.goals or 0

    def football_assists(record):
        return record.assists or 0

    def football_yellow_cards(record):
        return record.yellow_cards or 0

    def football_red_cards(record):
        return record.red_cards or 0

    def basketball_points(record):
        return record.points or 0

    def basketball_assists(record):
        return record.assists or 0

    def basketball_blocks(record):
        return record.blocks or 0

    def basketball_sent_off(record):
        return record.sent_off or 0

    def kickball_home_runs(record):
        return record.home_runs or 0

    def kickball_red_cards(record):
        return record.kickball_red_cards or 0

    def kickball_yellow_cards(record):
        return record.kickball_yellow_cards or 0

    def kickball_cut_base(record):
        return record.cut_base or 0

    def kickball_foul_played(record):
        return record.foul_played or 0

    # ==========================================================
    # ONLY ALLOW VALID RANKING FOR THE SELECTED SPORT
    # ==========================================================

    ranking_functions = {}

    if sport == 'Football':

        ranking_functions = {
            'highest_goals': football_goals,
            'highest_assists': football_assists,
            'highest_games': games_metric,
            'highest_trophies': trophy_count,
            'highest_yellow_cards': football_yellow_cards,
            'highest_red_cards': football_red_cards,
            'highest_motm': motm_metric,
            'highest_mvp': mvp_metric
        }

    elif sport == 'Basketball':

        ranking_functions = {
            'highest_points': basketball_points,
            'highest_assists': basketball_assists,
            'highest_games': games_metric,
            'highest_trophies': trophy_count,
            'highest_blocks': basketball_blocks,
            'highest_sent_off': basketball_sent_off,
            'highest_motm': motm_metric,
            'highest_mvp': mvp_metric
        }

    elif sport == 'Kickball':

        ranking_functions = {
            'highest_home_runs': kickball_home_runs,
            'highest_games': games_metric,
            'highest_trophies': trophy_count,
            'highest_red_cards': kickball_red_cards,
            'highest_yellow_cards': kickball_yellow_cards,
            'highest_cut_base': kickball_cut_base,
            'highest_foul_played': kickball_foul_played,
            'highest_motm': motm_metric,
            'highest_mvp': mvp_metric
        }

    # ==========================================================
    # APPLY RANKING
    # ==========================================================

    if sort_by in ranking_functions:

        records.sort(
            key=ranking_functions[sort_by],
            reverse=True
        )

    else:

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

    return render_template(
        'search.html',
        results=results,
        name=name,
        school=school,
        team_played_against=team_played_against,
        year=year,
        sport=sport,
        sort_by=sort_by,
        available_years=available_years
    )
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
            db.session.add(user)
            db.session.flush()

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

@app.route('/login', methods=['GET', 'POST'])
def login():
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
        # Make the authenticated session permanent
        session.permanent = True

        # Coaches/admins must be verified before continuing
        if user.role == 'Coach' and not user.is_verified:
            flash(
                'Your coach account is waiting for Super Admin approval.',
                'warning'
            )
            return redirect(url_for('login'))

        # Coaches/admins must complete 2FA before being logged in
        if user.role in ('Coach', 'System'):

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

        # Students do not require 2FA
        login_user(user)
        return redirect(url_for('student_dashboard'))

    return render_template('login.html')

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
        return redirect(url_for('admin_dashboard'))

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

    return render_template(
        'student_dashboard.html',
        records=records,
        current_year=current_year,
        registered_categories=registered_categories
    )

@app.route('/submit_record', methods=['POST'])
@login_required
def submit_record():
    if current_user.role != 'Athlete' or not current_user.is_verified:
        flash('You are not allowed to submit records.', 'danger')
        return redirect(url_for('student_dashboard'))

    club_division = request.form.get(
        'club_division',
        ''
    ).strip()

    VALID_CLUB_DIVISIONS = {
        '1st Division',
        '2nd Division',
        '3rd Division'
    }

    competition_category = request.form.get('competition_category', '').strip()

    if competition_category == 'Club League':
        if club_division not in VALID_CLUB_DIVISIONS:
            flash(
                'Please select a valid Club League Division.',
                'danger'
            )
            return redirect(url_for('student_dashboard'))
    else:
        club_division = None

    sport = request.form.get('sport')
    year = request.form.get('year')
    position = request.form.get('position', '').strip()
    games_played = request.form.get('games_played') or 0
    trophies = request.form.getlist('trophy')
    man_of_the_match = request.form.get('man_of_the_match')

    try:
        games_played = int(games_played)
        man_of_the_match = int(man_of_the_match)
    except (TypeError, ValueError):
        flash(
            'Games Played or MOTM/QOTM(if given) must both be 1 for every game record.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    if games_played != 1:
        flash(
            'Games Played must be exactly 1 because records are submitted game-by-game.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    if man_of_the_match == 2:
        flash(
            'MOTM/QOTM must be exactly 1 for every submitted game record.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    # ============================================================
    # COMPETITION CATEGORY ↔ TROPHY VALIDATION
    # ============================================================
    allowed_trophies = {
        'High School': {'Classes League'},
        'County Meet': {'County Meet'},
        'Club League': {'Club Trophy'},
        'University League': {'University Championship'},
        'Community/Area League': {'Community Trophy'},
        'AFCON': {'AFCON'},
        'WAFU': {'WAFU'},
        'World Cup': {'World Cup'},
    }

    BASKETBALL_TROPHIES = {
    'Basketball Africa League (BAL)',
    'FIBA Africa Zone',
    'FIBA AfroBasket Championships'
}

    if sport != 'Basketball':
        invalid_basketball_trophies = [
            trophy for trophy in trophies
            if trophy in BASKETBALL_TROPHIES
        ]

        if invalid_basketball_trophies:
            flash(
                'BAL, FIBA Africa Zone, and FIBA AfroBasket Championships '
                'are only available for Basketball records.',
                'danger'
            )
            return redirect(url_for('student_dashboard'))

    if competition_category not in allowed_trophies:
        flash(
            f'Invalid competition category: {competition_category}.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    if not trophies:
        flash(
            f'Please select the trophy won for the {competition_category} competition.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    invalid_trophies = [
        trophy for trophy in trophies
        if trophy not in allowed_trophies[competition_category]
    ]

    if invalid_trophies:
        expected_trophy = ', '.join(
            sorted(allowed_trophies[competition_category])
        )

        flash(
            f'Invalid trophy for {competition_category}. '
            f'The trophy must be: {expected_trophy}.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    trophy_value = ", ".join(trophies) if trophies else None
    team = request.form.get('team', '').strip()
    team_played_against = request.form.get('team_played_against', '').strip()
    match_minutes_played = request.form.get('match_minutes_played') or 0
    clean_sheets = request.form.get('clean_sheets') or 0
    saves = request.form.get('saves') or 0 
    rebound_type = request.form.get('rebound_type','').strip()
    preferred_foot = request.form.get(
            'preferred_foot',
            ''
        ).strip()

    if preferred_foot not in ['', 'Right', 'Left', 'Both']:
        flash('Invalid preferred foot selection.', 'danger')
        return redirect(url_for('student_dashboard'))

    current_user.preferred_foot = preferred_foot or None

    if not competition_category:
        flash(
            'Please select a competition.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    if competition_category not in VALID_COMPETITIONS:
        flash(
            'Invalid competition category selected.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    # ===== POSITION VALIDATION =====
    if sport not in VALID_POSITIONS:
        flash('Please select a valid sport.', 'danger')
        return redirect(url_for('student_dashboard'))

    if not position:
        flash('Please select a position.', 'danger')
        return redirect(url_for('student_dashboard'))

    if position not in VALID_POSITIONS[sport]:
        flash(
            f'Invalid position selected for {sport}.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))
    
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
        flash(
            'You are not allowed to submit a record for this competition category.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    # ===== PREVENT DUPLICATE: Same Sport + Same Year =====
    # Only block if there is already an approved or pending record
    # =====================================================
    # GAME DATE
    # =====================================================

    game_date_raw = request.form.get('game_date', '').strip()

    if not game_date_raw:
        flash(
            'Please enter the date the game was played.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    try:
        game_date = datetime.strptime(
            game_date_raw,
            '%Y-%m-%d'
        ).date()

    except ValueError:
        flash(
            'Invalid game date. Please select a valid date.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))


    # =====================================================
    # RECORD YEAR MUST MATCH GAME DATE YEAR
    # =====================================================

    try:
        year = int(year)
    except (TypeError, ValueError):
        flash(
            'Invalid record year.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    if game_date.year != year:
        flash(
            f'The game date belongs to {game_date.year}, '
            f'but the selected record year is {year}. '
            f'Please make them match.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))


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
        flash(
            f'You already submitted a {existing.sport} record '
            f'for {existing.competition_category} against '
            f'{existing.team or "this team"} on '
            f'{existing.game_date.strftime("%B %d, %Y")}.',
            'danger'
        )
        return redirect(url_for('student_dashboard'))

    # Safe conversion helper
    def safe_int(value):
        try:
            return int(value) if value not in [None, ''] else 0
        except:
            return 0

    record = SportRecord(
    user_id=current_user.id,
    sport=sport,
    year=year,
    game_date=game_date,
    position=position,
    games_played=1,
    match_minutes_played=safe_int(match_minutes_played),
    man_of_the_match=safe_int(request.form.get('man_of_the_match')),
    trophy=trophy_value,
    team=team,
    team_played_against=team_played_against,
    competition_category=competition_category,
    club_division=club_division,
    mvp=safe_int(request.form.get('mvp')),
    status='pending'
)

    if sport == 'Football':

        record.goals = safe_int(
            request.form.get('goals')
        )

        record.assists = safe_int(
            request.form.get('assists')
        )

        record.yellow_cards = safe_int(
            request.form.get('yellow_cards')
        )

        record.red_cards = safe_int(
            request.form.get('red_cards')
        )

        # ======================================================
        # GOALKEEPER-ONLY STATISTICS
        # ======================================================

        if position == 'GK':

            record.clean_sheets = safe_int(
                request.form.get('clean_sheets')
            )

            record.saves = safe_int(
                request.form.get('saves')
            )

        else:

            record.clean_sheets = 0
            record.saves = 0

        record.rebound_type = None

    elif sport == 'Basketball':
        record.points = safe_int(request.form.get('points'))
        record.assists = safe_int(request.form.get('assists'))
        record.blocks = safe_int(request.form.get('blocks'))
        record.sent_off = safe_int(request.form.get('sent_off'))
        record.rebound_type = (rebound_type
        if rebound_type in [
            'Offensive rebound',
            'Defensive rebound'
        ]
        else None
    )

        record.clean_sheets = 0

    elif sport == 'Kickball':
        record.home_runs = safe_int(request.form.get('home_runs'))
        record.kickball_red_cards = safe_int(request.form.get('kickball_red_cards'))
        record.kickball_yellow_cards = safe_int(request.form.get('kickball_yellow_cards'))
        record.cut_base = safe_int(request.form.get('cut_base'))
        record.foul_played = safe_int(request.form.get('foul_played'))
        record.clean_sheets = 0
        record.rebound_type = None

    db.session.add(record)
    db.session.commit()

    flash('Record submitted successfully and is pending approval.', 'success')
    return redirect(url_for('student_dashboard'))

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
        # MOTM / QOTM MUST BE EXACTLY 1
        # =====================================================
        if man_of_the_match != 1:
            flash(
                'MOTM/QOTM must be exactly 1 per game.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        # Always save exactly 1.
        record.games_played = 1
        record.man_of_the_match = 1

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

        allowed_competitions = {
            'High School',
            'County Meet',
            'Club League',
            'University League',
            'Community/Area League',
            'AFCON',
            'WAFU',
            'World Cup'
        }

        if competition_category not in allowed_competitions:
            flash(
                'Invalid competition category selected.',
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
        if not athlete_can_submit_competition(
            current_user.athlete_category,
            competition_category
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
        # TROPHIES
        # =====================================================

        trophies = [
            trophy.strip()
            for trophy in request.form.getlist('trophy')
            if trophy.strip()
        ]

        # Competition -> required trophy
        allowed_trophies = {
            'High School': {'Classes League'},
            'County Meet': {'County Meet'},
            'Club League': {'Club Trophy'},
            'University League': {'University Championship'},
            'Community/Area League': {'Community Trophy'},
            'AFCON': {'AFCON'},
            'WAFU': {'WAFU'},
            'World Cup': {'World Cup'},
        }

        # Basketball-only trophies
        basketball_trophies = {
            'Basketball Africa League (BAL)',
            'FIBA Africa Zone',
            'FIBA AfroBasket Championships'
        }

        required_trophy = next(
            iter(
                allowed_trophies[competition_category]
            )
        )

        # =====================================================
        # TROPHY MUST MATCH COMPETITION
        # =====================================================

        if required_trophy not in trophies:
            flash(
                f'The trophy must match the selected competition. '
                f'{competition_category} requires: {required_trophy}.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        # =====================================================
        # BASKETBALL TROPHY VALIDATION
        # =====================================================

        if record.sport != 'Basketball':

            invalid_basketball_trophies = [
                trophy
                for trophy in trophies
                if trophy in basketball_trophies
            ]

            if invalid_basketball_trophies:
                flash(
                    'BAL, FIBA Africa Zone, and FIBA AfroBasket '
                    'Championships are only available for Basketball records.',
                    'danger'
                )
                return redirect(
                    url_for(
                        'edit_record',
                        record_id=record.id
                    )
                )

        # =====================================================
        # INVALID TROPHY CHECK
        # =====================================================

        valid_trophies_for_record = (
            allowed_trophies[competition_category].union(
                basketball_trophies
                if record.sport == 'Basketball'
                else set()
            )
        )

        invalid_trophies = [
            trophy
            for trophy in trophies
            if trophy not in valid_trophies_for_record
        ]

        if invalid_trophies:
            flash(
                'One or more selected trophies are not valid '
                'for this competition.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_record',
                    record_id=record.id
                )
            )

        # =====================================================
        # SAVE TROPHIES
        # =====================================================

        record.trophy = (
            ", ".join(trophies)
            if trophies
            else None
        )

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

            rebound_type = request.form.get(
                'rebound_type',
                ''
            ).strip()

            if rebound_type in {
                'Offensive rebound',
                'Defensive rebound'
            }:
                record.rebound_type = rebound_type
            else:
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
        registered_categories=registered_categories
    )

@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role not in ('Coach', 'System'):
        return redirect(url_for('student_dashboard'))

    # Students pending verification
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

    # Records pending approval
    all_pending_records = SportRecord.query.join(User).filter(
        SportRecord.status.in_(['pending', 'rejected'])
    ).order_by(SportRecord.status.desc()).all()

    # System sees every pending record. Coaches are restricted by exact team/school
    # matching and their coach competition category.
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

    # TEMPORARY DEBUG
    print("========== COACH DASHBOARD DEBUG ==========")
    print("Logged-in coach:", current_user.full_name)
    print("Coach school/team:", repr(current_user.school))
    print("Pending records found:", len(pending_records))

    for record in pending_records:
        print(
            "Record:",
            record.id,
            "| Team:", repr(record.team),
            "| Status:", record.status,
            "| Student:", record.user.full_name
        )

    print("==========================================")

    # Pending Coaches (only Super Admin)
    pending_coaches = []
    if current_user.role == 'System':
        pending_coaches = User.query.filter_by(
            role='Coach',
            is_verified=False
        ).all()

    return render_template(
        'admin_dashboard.html',
        pending_users=pending_users,
        pending_records=pending_records,
        pending_coaches=pending_coaches
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

@app.route('/profile')
@login_required
def profile():
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

    return render_template('profile.html')

#========Read-only user profile route=========
@app.route('/user/<int:user_id>')
def user_profile(user_id):
    user = User.query.get_or_404(user_id)

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
            user_id=user.id, registration_type=registration_type,
            registration_year=current_year, status='active'
        ).order_by(Registration.id.asc()).first()
        if current_registration:
            current_category = current_registration.category
    elif user.role == 'System':
        current_category = 'System'

    return render_template('user_profile.html', user=user, current_category=current_category)


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

    # Remove associated Storage objects before deleting database data.
    # If Storage reports an error, keep the account intact.
    try:
        if profile_picture_path:
            supabase.storage.from_('profile-pictures').remove([profile_picture_path])

        if id_document_path:
            supabase.storage.from_('id-documents').remove([id_document_path])

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

        user = User.query.get(user_id)
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
        db.session.add(coach)
        db.session.flush()

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

# Two-Factor Authentication (2FA) Recovery Codes Route
@app.route('/admin/2fa/recovery-codes', methods=['GET', 'POST'])
@login_required
def recovery_codes():
    # Only admins/coaches can access recovery codes
    if current_user.role not in ('Coach', 'System'):
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

    user = User.query.get(user_id)

    if not user or user.role not in ('Coach', 'System') or not user.two_factor_enabled:
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

                return redirect(url_for('admin_dashboard'))

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

                return redirect(url_for('admin_dashboard'))

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

    if current_user.is_authenticated and current_user.role in ('Coach', 'System'):
        user = current_user

    elif setup_user_id:
        user = User.query.get(setup_user_id)

        if not user or user.role not in ('Coach', 'System'):
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

            return redirect(url_for('admin_dashboard'))

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
