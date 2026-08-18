import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from models import db, User, SportRecord
from config import Config
from dotenv import load_dotenv
load_dotenv()  # This forces Python to read your local .env file

import os
from flask import Flask
from pymongo import MongoClient
from dotenv import load_dotenv

# Load variables from your hidden .env file
load_dotenv()

app = Flask(__name__)

# Fetch the clean string securely
mongo_uri = os.environ.get("MONGO_URI")
client = MongoClient(mongo_uri)
db = client['@cluster1.6row61v.mongodb.net'] # Replace with your actual database name

app = Flask(__name__)
app.config.from_object(Config)

app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

with app.app_context():
    db.create_all()
    # Create default admin if none exists
    if not User.query.filter_by(role='admin').first():
        admin = User(full_name='Admin Coach', school='System', role='admin', is_verified=True)
        admin.set_password('admin123')  # Change this later!
        db.session.add(admin)
        db.session.commit()

@app.route('/')
def index():
    return render_template('index.html')

# ========== PUBLIC ROUTES ==========
@app.route('/search')
def search():
    name = request.args.get('name', '').strip()
    school = request.args.get('school', '').strip()

    results = []

    # Only search if the user typed something
    if name or school:
        query = User.query.filter_by(role='student', is_verified=True)

        if name:
            query = query.filter(User.full_name.ilike(f'%{name}%'))
        if school:
            query = query.filter(User.school.ilike(f'%{school}%'))

        students = query.all()

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

        filename = secure_filename(f"{full_name}_{school}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        user = User(
            full_name=full_name,
            school=school,
            id_document=filename,
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


# ========== @app.route('/uploads/<filename>')def uploaded_file(filename):return send_from_directory(app.config['UPLOAD_FOLDER'], filename)======


# ========== STUDENT DASHBOARD ==========
@app.route('/student')
@login_required
def student_dashboard():
    if current_user.role != 'student':
        return redirect(url_for('admin_dashboard'))
    records = SportRecord.query.filter_by(user_id=current_user.id).all()
    return render_template('student_dashboard.html', records=records)

@app.route('/submit_record', methods=['POST'])
@login_required
def submit_record():
    if current_user.role != 'student':
        return redirect(url_for('index'))

    def safe_int(value, default=0):
        try:
            return int(value) if value and str(value).strip() else default
        except (ValueError, TypeError):
            return default

    sport = request.form.get('sport')

    record = SportRecord(
        user_id=current_user.id,
        sport=sport,
        year=safe_int(request.form.get('year')),
        position=request.form.get('position', '').strip(),
        games_played=safe_int(request.form.get('games_played')),
        status='pending'
    )

    # Add sport-specific fields
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
    flash('Record submitted! Waiting for coach approval.', 'success')
    return redirect(url_for('student_dashboard'))

# ========== ADMIN / COACH DASHBOARD ==========
@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        return redirect(url_for('student_dashboard'))

    pending_users = User.query.filter_by(role='student', is_verified=False).all()
    pending_records = SportRecord.query.filter_by(status='pending').all()
    return render_template('admin_dashboard.html',
                           pending_users=pending_users,
                           pending_records=pending_records)
@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')

@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    file = request.files.get('profile_picture')
    if file and file.filename != '':
        if allowed_file(file.filename):
            filename = secure_filename(f"profile_{current_user.id}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            current_user.profile_picture = filename
        else:
            flash('Only PNG, JPG or JPEG allowed.', 'danger')
            return redirect(url_for('profile'))

    new_school = request.form.get('school', '').strip()
    if new_school:
        current_user.school = new_school

    db.session.commit()
    flash('Profile updated successfully!', 'success')
    return redirect(url_for('profile'))

@app.route('/verify_user/<int:user_id>')
@login_required
def verify_user(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    user = User.query.get_or_404(user_id)
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
    record.status = 'rejected'
    db.session.commit()
    flash('Record rejected.', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True)