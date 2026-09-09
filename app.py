import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from models import db, User, SportRecord
from dotenv import load_dotenv
load_dotenv(override=True)  # This forces Python to read your local .env file
from config import Config
from datetime import datetime
from flask_migrate import Migrate 
from supabase import create_client
import uuid


app = Flask(__name__)
app.config.from_object(Config)
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

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']
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

with app.app_context():
    # db.create_all()
    if not User.query.filter_by(role='admin').first():
        admin = User(
            full_name='Admin Coach',
            school='System',
            role='admin',
            is_verified=True
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()

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
        password = request.form['password']

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

        user = User(
            full_name=full_name,
            school=school,
            id_document=storage_path,
            role='student'
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('Registration successful! Wait for admin verification of your ID.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        password = request.form['password']

        user = User.query.filter_by(full_name=full_name).first()
        if user and user.check_password(password):
            if user.role == 'admin' and not user.is_verified:
                flash('Your coach account is waiting for Super Admin approval.', 'warning')
                return redirect(url_for('login'))
            
            login_user(user)
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('student_dashboard'))
        flash('Invalid full name or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))
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
        
    db.session.add(record)
    db.session.commit()

    flash('Record submitted successfully and is pending approval.', 'success')
    return redirect(url_for('student_dashboard'))

# ========== Delete route ==========#
@app.route('/delete_record/<int:record_id>')
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
@app.route('/resubmit_record/<int:record_id>')
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
        record.team = request.form.get('team')          # ← Add this line

        trophies = request.form.getlist('trophy')
        record.trophy = ", ".join(trophies) if trophies else None

        if record.sport == 'Football':
            record.goals = int(request.form.get('goals') or 0)
            record.assists = int(request.form.get('assists') or 0)
            record.yellow_cards = int(request.form.get('yellow_cards') or 0)
            record.red_cards = int(request.form.get('red_cards') or 0)
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

@app.route('/approve_coach/<int:user_id>')
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

    # Handle profile picture upload
    file = request.files.get('profile_picture')
    if file and file.filename != '':
        if allowed_file(file.filename):
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

@app.route('/verify_user/<int:user_id>')
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


@app.route('/approve_record/<int:record_id>')
@login_required
def approve_record(record_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))

    record = SportRecord.query.get_or_404(record_id)

    # Check if this record belongs to the coach's school/team
    if current_user.school not in (record.team or ''):
        flash('You can only approve records from your own school/team.', 'danger')
        return redirect(url_for('admin_dashboard'))

    record.status = 'approved'
    db.session.commit()
    flash('Record approved.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/reject_record/<int:record_id>')
@login_required
def reject_record(record_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))

    record = SportRecord.query.get_or_404(record_id)

    # Check if this record belongs to the coach's school/team
    if current_user.school not in (record.team or ''):
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
        password = request.form['password']
        secret_code = request.form['secret_code'].strip()

        # Check secret code
        if secret_code != app.config.get('COACH_SECRET_CODE', 'ATHLETE-COACH-2025'):
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
            role='admin',
            is_verified=False          # Pending Super Admin approval
        )
        coach.set_password(password)
        db.session.add(coach)
        db.session.commit()

        flash('Coach account created! Waiting for Super Admin approval.', 'success')
        return redirect(url_for('login'))

    return render_template('register_coach.html')


if __name__ == '__main__':
    app.run(debug=True)
