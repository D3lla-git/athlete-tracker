import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, send_from_directory, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from models import db, User, SportRecord, LoginAttempt, Registration
from dotenv import load_dotenv
load_dotenv(override=True)  # This forces Python to read your local .env file
from config import Config
from datetime import datetime
from flask_migrate import Migrate
from supabase import create_client
import uuid
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

app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

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
        email = request.form['email'].strip().lower()
        password = request.form['password']

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

    sport = request.form.get('sport')
    year = request.form.get('year')
    position = request.form.get('position', '').strip()
    games_played = request.form.get('games_played') or 0
    trophies = request.form.getlist('trophy')
    trophy_value = ", ".join(trophies) if trophies else None
    team = request.form.get('team', '').strip()
    team_played_against = request.form.get('team_played_against', '').strip()
    competition_category = request.form.get('competition_category', '').strip()
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
    existing = SportRecord.query.filter(
        SportRecord.user_id == current_user.id,
        SportRecord.sport == sport,
        SportRecord.year == year,
        SportRecord.competition_category == competition_category,
        SportRecord.status.in_(['approved', 'pending'])
    ).first()

    if existing:
        flash(
    f'You already have a {existing.status} {sport} record '
    f'for {existing.competition_category} in the year {year}.',
    'danger'
)
    # ===================================================== #
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
    position=position,
    games_played=safe_int(games_played),
    trophy=trophy_value,
    team=team,
    team_played_against=team_played_against,
    competition_category=competition_category,
    man_of_the_match=safe_int(request.form.get('man_of_the_match')),
    mvp=safe_int(request.form.get('mvp')),
    status='pending'
)

    if sport == 'Football':
        record.goals = safe_int(request.form.get('goals'))
        record.assists = safe_int(request.form.get('assists'))
        record.yellow_cards = safe_int(request.form.get('yellow_cards'))
        record.red_cards = safe_int(request.form.get('red_cards'))

    elif sport == 'Basketball':
        record.points = safe_int(request.form.get('points'))
        record.assists = safe_int(request.form.get('assists'))
        record.blocks = safe_int(request.form.get('blocks'))
        record.sent_off = safe_int(request.form.get('sent_off'))

    elif sport == 'Kickball':
        record.home_runs = safe_int(request.form.get('home_runs'))
        record.kickball_red_cards = safe_int(request.form.get('kickball_red_cards'))
        record.kickball_yellow_cards = safe_int(request.form.get('kickball_yellow_cards'))
        record.cut_base = safe_int(request.form.get('cut_base'))
        record.foul_played = safe_int(request.form.get('foul_played'))

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

    # Security: only owner can edit
    if record.user_id != current_user.id:
        flash('You can only edit your own records.', 'danger')
        return redirect(url_for('student_dashboard'))

    current_year = str(datetime.now().year)

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

    # Only allow editing for the current year
    if str(record.year) != current_year:
        flash(f'You can only edit records for the {current_year} season.', 'danger')
        return redirect(url_for('student_dashboard'))

    if request.method == 'POST':
        position = request.form.get('position', '').strip()

        # ===== POSITION VALIDATION =====
        if not position:
            flash('Please select a position.', 'danger')
            return redirect(url_for(
                'edit_record',
                record_id=record.id
            ))

        if record.sport not in VALID_POSITIONS:
            flash('Invalid sport for this record.', 'danger')
            return redirect(url_for(
                'edit_record',
                record_id=record.id
            ))

        if position not in VALID_POSITIONS[record.sport]:
            flash(
                f'Invalid position selected for {record.sport}.',
                'danger'
            )
            return redirect(url_for(
                'edit_record',
                record_id=record.id
            ))

        record.position = position
        record.games_played = int(
            request.form.get('games_played') or 0
        )
        record.team = request.form.get('team', '').strip()
        record.team_played_against = request.form.get('team_played_against', '').strip()
        competition_category = request.form.get(
            'competition_category',
            ''
        ).strip()
        record.competition_category = competition_category
        # ===== CATEGORY PERMISSION CHECK =====
        if not athlete_can_submit_competition(
            current_user.athlete_category,
            competition_category
        ):
            flash(
                'You are not allowed to use this competition category.',
                'danger'
            )
            return redirect(url_for('student_dashboard'))

        record.competition_category = competition_category
    
        record.man_of_the_match = int(request.form.get('man_of_the_match') or 0)
        record.mvp = int(request.form.get('mvp') or 0)

        trophies = request.form.getlist('trophy')
        record.trophy = ", ".join(trophies) if trophies else None

        if record.sport == 'Football':
            record.goals = int(request.form.get('goals') or 0)
            record.assists = int(request.form.get('assists') or 0)
            record.yellow_cards = int(request.form.get('yellow_cards') or 0)
            record.red_cards = int(request.form.get('red_cards') or 0)

        elif record.sport == 'Kickball':
            record.home_runs = int(request.form.get('home_runs') or 0)
            record.kickball_red_cards = int(request.form.get('kickball_red_cards') or 0)
            record.kickball_yellow_cards = int(request.form.get('kickball_yellow_cards') or 0)
            record.cut_base = int(request.form.get('cut_base') or 0)
            record.foul_played = int(request.form.get('foul_played') or 0)

        else:
            record.points = int(request.form.get('points') or 0)
            record.assists = int(request.form.get('assists') or 0)
            record.blocks = int(request.form.get('blocks') or 0)
            record.sent_off = int(request.form.get('sent_off') or 0)

        # Send back to pending after edit
        record.status = 'pending'

        db.session.commit()
        flash('Record updated successfully and sent for coach approval.', 'success')
        return redirect(url_for('student_dashboard'))

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
