import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, send_from_directory, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from models import db, User, SportRecord, LoginAttempt
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
        User.role == 'student',
        User.is_verified == True,
        User.full_name.ilike(f'{q}%')          # ← starts with
    ).order_by(User.full_name).limit(8).all()

    return jsonify([{'full_name': s.full_name} for s in students])

@app.route('/search')
def search():
    name = request.args.get('name', '').strip()
    school = request.args.get('school', '').strip()

    results = []

    if name or school:
        query = User.query.filter_by(role='student', is_verified=True)

        if name:
            # This allows searching by first, middle, or last name
            query = query.filter(User.full_name.ilike(f'%{name}%'))
        if school:
            query = query.filter(User.school.ilike(f'%{school}%'))

        students = query.order_by(User.full_name).all()

        for student in students:
            approved_records = SportRecord.query.filter_by(
                user_id=student.id,
                status='approved'
            ).all()

            results.append({
                'student': student,
                'records': approved_records
            })

    return render_template('search.html', results=results, name=name, school=school)
# ========== AUTH ROUTES ==========
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        school = request.form['school'].strip()
        gender = request.form.get('gender', '').strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
# ========= PASSWORD STRENGTH CHECK ========== #
        if not is_strong_password(password):
            flash(
                'Password must be at least 8 characters and include '
                'an Uppercase Letter, lowercase letter, number, '
                'and special character.',
                'danger'
            )
            return redirect(url_for('register'))

        if gender not in ['Male', 'Female']:
            flash('Please select a valid gender.', 'danger')
            return redirect(url_for('register'))

        # Check duplicate
        existing = User.query.filter_by(full_name=full_name, school=school).first()
        if existing:
            flash('A student with this name and school already exists.', 'danger')
            return redirect(url_for('register'))

        # Handle file upload
        file = request.files.get('id_document')
        if not file or file.filename == '':
            flash('Student ID or Passport is required.', 'danger')
            return redirect(url_for('register'))

        if not allowed_file(file.filename):
            flash('Only PNG, JPG, JPEG or PDF files are allowed.', 'danger')
            return redirect(url_for('register'))

        actual_file_type = valid_file_content(file)

        if not actual_file_type:
            flash(
                'The uploaded file is invalid or does not match its file type.',
                'danger'
            )
            return redirect(url_for('register'))

        extension = file.filename.rsplit('.', 1)[1].lower()

        if extension in ['jpg', 'jpeg'] and actual_file_type != 'jpg':
            flash('The uploaded image is not a valid JPEG file.', 'danger')
            return redirect(url_for('register'))

        if extension == 'png' and actual_file_type != 'png':
            flash('The uploaded image is not a valid PNG file.', 'danger')
            return redirect(url_for('register'))

        if extension == 'pdf' and actual_file_type != 'pdf':
            flash('The uploaded document is not a valid PDF file.', 'danger')
            return redirect(url_for('register'))

#========== SUPABASE UPLOAD ==========
        filename = secure_filename(file.filename)
        storage_path = f"students/{uuid.uuid4().hex}_{filename}"

        try:
            upload_to_supabase(
                file,
                "id-documents",
                storage_path
            )
        except Exception as e:
            print("Supabase ID document upload error:", e)
            flash('There was a problem uploading your ID document. Please try again.', 'danger')
            return redirect(url_for('register'))

        #filename = secure_filename(f"{full_name}_{school}_{file.filename}")
        #filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        #file.save(filepath)

        existing_email = User.query.filter_by(email=email).first()

        if existing_email:
            flash('An account with this email address already exists.', 'danger')
            return redirect(url_for('register'))

        user = User(
            full_name=full_name,
            school=school,
            gender=gender,
            email=email,
            id_document=filename,
            role='student'
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('Registration successful! Wait for admin verification of your ID.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

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
        if user.role == 'admin' and not user.is_verified:
            flash(
                'Your coach account is waiting for Super Admin approval.',
                'warning'
            )
            return redirect(url_for('login'))

        # Coaches/admins must complete 2FA before being logged in
        if user.role == 'admin':

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
    if current_user.role != 'admin':
        flash('You are not authorized to view ID documents.', 'danger')
        return redirect(url_for('student_dashboard'))

    # Find the student who owns this ID document
    owner = User.query.filter_by(id_document=filename).first()

    if not owner:
        flash('ID document not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Super Admin can view documents from all schools
    is_super_admin = (
        current_user.school.strip().casefold() == 'system'
        or current_user.full_name.strip().casefold() == 'admin coach'
    )

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
    if current_user.role != 'student':
        return redirect(url_for('admin_dashboard'))

    records = SportRecord.query.filter_by(user_id=current_user.id).all()
    current_year = datetime.now().year

    return render_template('student_dashboard.html',
                           records=records,
                           current_year=current_year)

@app.route('/submit_record', methods=['POST'])
@login_required
def submit_record():
    if current_user.role != 'student' or not current_user.is_verified:
        flash('You are not allowed to submit records.', 'danger')
        return redirect(url_for('student_dashboard'))

    sport = request.form.get('sport')
    year = request.form.get('year')
    position = request.form.get('position')
    games_played = request.form.get('games_played') or 0
    trophies = request.form.getlist('trophy')
    trophy_value = ", ".join(trophies) if trophies else None
    team = request.form.get('team')

    # ===== PREVENT DUPLICATE: Same Sport + Same Year =====
    # Only block if there is already an approved or pending record
    existing = SportRecord.query.filter(
        SportRecord.user_id == current_user.id,
        SportRecord.sport == sport,
        SportRecord.year == year,
        SportRecord.status.in_(['approved', 'pending'])
    ).first()

    if existing:
        flash(f'You already have a {existing.status} {sport} record for the year {year}.', 'danger')
        return redirect(url_for('student_dashboard'))
    # ===================================================== #
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

    # Only allow editing for the current year
    if str(record.year) != current_year:
        flash(f'You can only edit records for the {current_year} season.', 'danger')
        return redirect(url_for('student_dashboard'))

    if request.method == 'POST':
        record.position = request.form.get('position')
        record.games_played = int(request.form.get('games_played') or 0)
        record.team = request.form.get('team')
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

    return render_template('edit_record.html', record=record)

@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        return redirect(url_for('student_dashboard'))

    # Students pending verification
    pending_users = User.query.filter_by(
        role='student',
        is_verified=False,
        school=current_user.school
    ).all()

    # Records pending approval — based on the Team field
    pending_records = SportRecord.query.join(User).filter(
        SportRecord.status.in_(['pending', 'rejected']),
        SportRecord.team.ilike(f'%{current_user.school}%')
    ).order_by(SportRecord.status.desc()).all()

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
    if current_user.school == 'System' or current_user.full_name == 'Admin Coach':
        pending_coaches = User.query.filter_by(
            role='admin',
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
    if current_user.role != 'admin' or current_user.school != 'System':
        # Fallback: also allow by name if needed
        if current_user.full_name != 'Admin Coach':
            flash('Only Super Admin can approve coaches.', 'danger')
            return redirect(url_for('admin_dashboard'))

    coach = User.query.get_or_404(user_id)

    if coach.role != 'admin':
        flash('Invalid request.', 'danger')
        return redirect(url_for('admin_dashboard'))

    coach.is_verified = True
    db.session.commit()
    flash(f'Coach {coach.full_name} ({coach.school}) has been approved.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')

@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    # Update school
    new_school = request.form.get('school', '').strip()
    if new_school:
        current_user.school = new_school

    # Update gender
    new_gender = request.form.get('gender', '').strip()

    if new_gender in ['Male', 'Female']:
        current_user.gender = new_gender

    # Handle profile picture upload
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
            storage_path = f"profiles/{current_user.id}_{uuid.uuid4().hex}_{filename}"

            try:
                upload_to_supabase(
                    file,
                    "profile-pictures",
                    storage_path
                )
                current_user.profile_picture = storage_path
            except Exception as e:
                print("Supabase profile picture upload error:", e)
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

@app.route('/verify_user/<int:user_id>', methods=['POST'])
@login_required
def verify_user(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))

    user = User.query.get_or_404(user_id)

    # Security check
    if user.school != current_user.school:
        flash('You can only verify students from your own school.', 'danger')
        return redirect(url_for('admin_dashboard'))

    user.is_verified = True
    db.session.commit()
    flash(f'{user.full_name} has been verified.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/approve_record/<int:record_id>', methods=['POST'])
@login_required
def approve_record(record_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))

    record = SportRecord.query.get_or_404(record_id)

    # Check if this record belongs to the coach's school/team
    if current_user.school.strip().casefold() != (record.team or '').strip().casefold():
        flash('You can only approve records from your own school/team.', 'danger')
        return redirect(url_for('admin_dashboard'))

    record.status = 'approved'
    db.session.commit()
    flash('Record approved.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/reject_record/<int:record_id>', methods=['POST'])
@login_required
def reject_record(record_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))

    record = SportRecord.query.get_or_404(record_id)

    # Check if this record belongs to the coach's school/team
    if current_user.school.strip().casefold() != (record.team or '').strip().casefold():
        flash('You can only reject records from your own school/team.', 'danger')
        return redirect(url_for('admin_dashboard'))

    record.status = 'rejected'
    db.session.commit()
    flash('Record rejected.', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/register-coach', methods=['GET', 'POST'])
def register_coach():
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        school = request.form['school'].strip()
        gender = request.form.get('gender', '').strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        # ======== PASSWORD STRENGTH CHECK ========== #
        if not is_strong_password(password):
            flash(
                'Password must be at least 10 characters and include '
                'an uppercase letter, lowercase letter, number, '
                'and special character.',
                'danger'
            )
            return redirect(url_for('register_coach'))

        secret_code = request.form['secret_code'].strip()

        existing_email = User.query.filter_by(email=email).first()

        if existing_email:
            flash('An account with this email address already exists.', 'danger')
            return redirect(url_for('register_coach'))

        if gender not in ['Male', 'Female']:
            flash('Please select a valid gender.', 'danger')
            return redirect(url_for('register_coach'))

        # Check secret code
        expected_code = app.config.get('COACH_SECRET_CODE')
        if not expected_code:
            flash('Coach registration is temporarily unavailable.', 'danger')
            return redirect(url_for('register_coach'))
        if secret_code != expected_code:
            flash('Invalid Coach Secret Code.', 'danger')
            return redirect(url_for('register_coach'))

        # Check duplicate
        existing = User.query.filter_by(full_name=full_name, school=school, role='admin').first()
        if existing:
            flash('A coach with this name and school already exists.', 'danger')
            return redirect(url_for('register_coach'))

        coach = User(
            full_name=full_name,
            school=school,
            gender=gender,
            email=email,
            role='admin',
            is_verified=False          # Pending Super Admin approval
        )
        coach.set_password(password)
        db.session.add(coach)
        db.session.commit()

        flash('Coach account created! Waiting for Super Admin approval.', 'success')
        return redirect(url_for('login'))

    return render_template('register_coach.html')

# Two-Factor Authentication (2FA) Recovery Codes Route
@app.route('/admin/2fa/recovery-codes', methods=['GET', 'POST'])
@login_required
def recovery_codes():
    # Only admins/coaches can access recovery codes
    if current_user.role != 'admin':
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

    if not user or user.role != 'admin' or not user.two_factor_enabled:
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

    if current_user.is_authenticated and current_user.role == 'admin':
        user = current_user

    elif setup_user_id:
        user = User.query.get(setup_user_id)

        if not user or user.role != 'admin':
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
