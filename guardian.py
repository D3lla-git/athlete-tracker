"""
Safeguarding for young athletes (under 17).

Every athlete under GUARDIAN_AGE must give a parent / guardian contact (in
addition to their coach's contact, which comes from the coach's account) and
confirm that a parent, guardian or coach helps manage their D.A.R.T. page.
Until both are done, the app asks for them (like the username step).

Who sees the guardian contact: the athlete, coaches of the athlete's team and
the D.A.R.T Admin. Never the public, scouts or the shareable card.
"""
import re

from names import validate_full_name
import integrity

GUARDIAN_AGE = 17
RELATIONSHIPS = ('Mother', 'Father', 'Legal guardian', 'Grandparent', 'Aunt / Uncle', 'Older sibling', 'Other relative')

_INTL_RE = re.compile(r'^\+?(\d{8,15})$')


def is_minor(age):
    return age is not None and age < GUARDIAN_AGE


def clean_phone(raw):
    """Liberian numbers -> '+231…' (as for coaches); parents abroad: any international number '+<8-15 digits>'."""
    liberian = integrity.clean_phone(raw)
    if liberian:
        return liberian
    text = re.sub(r'[\s\-().]', '', raw or '')
    if text.startswith('00'):
        text = '+' + text[2:]
    m = _INTL_RE.match(text)
    if not m or not text.startswith('+') or text.startswith('+231'):
        return None             # a +231 number that isn't Orange / Lonestar is refused above
    return '+' + m.group(1)


def validate(form, athlete=None):
    """(clean data dict, error message or None) from a form with guardian_name / guardian_relationship / guardian_phone."""
    name, error = validate_full_name(form.get('guardian_name', ''))
    if error:
        return None, "Parent / guardian's name: " + error[0].lower() + error[1:]
    if athlete is not None and ' '.join((athlete.full_name or '').split()).casefold() == name.casefold():
        return None, "Enter your parent's or guardian's name, not your own."
    relationship = form.get('guardian_relationship', '')
    if relationship not in RELATIONSHIPS:
        return None, 'Choose how this person is related to you.'
    phone = clean_phone(form.get('guardian_phone'))
    if not phone:
        return None, ("Enter your parent's or guardian's phone number: a Liberian Orange / Lonestar number "
                      '(e.g. 0770 123 456) or an international number starting with + (e.g. +1 202 555 0100).')
    return {'guardian_name': name, 'guardian_relationship': relationship, 'guardian_phone': phone}, None


def needs_setup(user, age):
    """An athlete under 17 without a guardian contact, or who hasn't confirmed the safeguarding notice."""
    return (getattr(user, 'role', None) == 'Athlete' and is_minor(age)
            and not (user.guardian_phone and user.minor_notice_ack_at))
