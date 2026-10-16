"""
Liberia National Identification Number (NIN), as printed on the National
Identification Registry (NIR) card: 10 digits, e.g. 2500750437.

The NIN is never shown to anyone but the D.A.R.T Admin:
  - it is stored encrypted (Fernet / AES) - nin_cipher;
  - a keyed fingerprint (HMAC-SHA256) - nin_hash - stops one NIN from
    opening two accounts without storing the number in clear;
  - logs, audit details, flashes and exports never contain it.

Key: NIN_ENCRYPTION_KEY (environment) if set, else derived from SECRET_KEY.
Set NIN_ENCRYPTION_KEY in production before the first NIN is saved, and
never change it afterwards (saved numbers could no longer be read).
"""
import base64
import hashlib
import hmac
import os
import re

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NIN_LENGTH = 10
NIN_HELP = 'Your 10-digit National Identification Number (NIN) from your NIR card, e.g. 2500750437.'
ADULT_AGE = 18
_SEPARATORS = re.compile(r'[\s\-./]')
_DIGITS = re.compile(r'^\d{10}$')
_SEQUENCES = ('0123456789', '1234567890', '9876543210', '0987654321')


def normalize(raw):
    """The NIN as 10 digits (spaces / dashes removed), or ''."""
    return _SEPARATORS.sub('', str(raw or ''))[:40]


def validate(raw):
    """(nin, error). nin is the 10-digit string when valid."""
    value = normalize(raw)
    if not value:
        return None, 'Enter your National Identification Number (NIN).'
    if not value.isdigit():
        return None, 'The NIN has numbers only (10 digits, as on your NIR card).'
    if len(value) != NIN_LENGTH:
        return None, f'The NIN must be exactly {NIN_LENGTH} digits (you entered {len(value)}).'
    if not _DIGITS.match(value) or len(set(value)) == 1 or value in _SEQUENCES:
        return None, 'That is not a valid NIN. Enter the number exactly as on your NIR card.'
    return value, None


def _secret():
    key = os.environ.get('NIN_ENCRYPTION_KEY') or os.environ.get('SECRET_KEY') or ''
    if not key:
        raise RuntimeError('NIN_ENCRYPTION_KEY (or SECRET_KEY) is not set')
    return key.encode()


def _derive(info):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b'dart-nin', info=info).derive(_secret())


def _fernet():
    return Fernet(base64.urlsafe_b64encode(_derive(b'dart-nin-encrypt-v1')))


def encrypt(nin):
    return _fernet().encrypt(nin.encode()).decode()


def decrypt(cipher):
    """The NIN, or '' if it can't be read (wrong key / damaged)."""
    if not cipher:
        return ''
    try:
        return _fernet().decrypt(cipher.encode()).decode()
    except (InvalidToken, ValueError):
        return ''


def fingerprint(nin):
    return hmac.new(_derive(b'dart-nin-fingerprint-v1'), nin.encode(), hashlib.sha256).hexdigest()


def masked(user):
    """What the account owner sees: 'On file' (never any digits), '' if none."""
    return 'On file' if getattr(user, 'nin_cipher', None) else ''


def required_for(role, age):
    """Coaches always; athletes from 18. Under-18 athletes may use a birth certificate instead."""
    if role == 'Coach':
        return True
    if role == 'Athlete':
        return age is not None and age >= ADULT_AGE
    return False


def applies_to(user):
    return getattr(user, 'role', None) in ('Athlete', 'Coach')
