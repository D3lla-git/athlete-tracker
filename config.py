import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Secure session cookies
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Session lifetime
    PERMANENT_SESSION_LIFETIME = 30 * 60  # 30 minutes
    SESSION_REFRESH_EACH_REQUEST = True

    UPLOAD_FOLDER = 'uploads'
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB max

    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

    # Coach registration secret must come from environment variables
    COACH_SECRET_CODE = os.environ.get("COACH_SECRET_CODE")

        # Gmail SMTP settings
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER")