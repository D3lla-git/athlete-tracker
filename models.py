from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    school = db.Column(db.String(150), nullable=False)
    gender = db.Column(db.String(10), nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='student')  # student or admin
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
    position = db.Column(db.String(50))
    games_played = db.Column(db.Integer, default=0)
    trophy = db.Column(db.String(100), nullable=True)
    team = db.Column(db.String(100), nullable=True)
    school = db.Column(db.String(150), nullable=True)

    # Football fields
    goals = db.Column(db.Integer, default=0)
    assists = db.Column(db.Integer, default=0)
    yellow_cards = db.Column(db.Integer, default=0)
    red_cards = db.Column(db.Integer, default=0)

    # Basketball fields
    points = db.Column(db.Integer, default=0)
    blocks = db.Column(db.Integer, default=0)
    sent_off = db.Column(db.Integer, default=0)

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