"""
Usernames (@handles): every athlete, coach and scout has one. Two people can
share a full name ("John Brown"), but never a username, so it tells them
apart everywhere (search, profiles, feed, comments, messages, login).

Rules (like X / Instagram):
- 3 to 20 characters: letters a-z, numbers 0-9 and _ (underscore).
- Starts with a letter; no "__"; does not end with "_".
- Not case sensitive: "MusuK" and "musuk" are the same username
  (stored in lowercase).
- Not a reserved word (admin, dart, support, coach...) and nothing rude.
"""
import re
import unicodedata

USERNAME_MIN = 3
USERNAME_MAX = 20
USERNAME_ROLES = ('Athlete', 'Coach', 'Scout')
USERNAME_CHANGE_DAYS = 30

_FORMAT_RE = re.compile(r'^[a-z][a-z0-9_]*$')

# Words that could pose as D.A.R.T. staff or clash with app pages.
_RESERVED = {
    'admin', 'administrator', 'superadmin', 'root', 'system', 'sysadmin', 'moderator', 'mod', 'staff', 'support',
    'help', 'helpdesk', 'security', 'official', 'verified', 'dart', 'dartlr', 'dart_lr', 'dartapp', 'dart_official',
    'liberia', 'lfa', 'fifa', 'caf', 'api', 'static', 'login', 'logout', 'register', 'signup', 'signin', 'settings',
    'profile', 'feed', 'search', 'inbox', 'messages', 'pricing', 'terms', 'privacy', 'legal', 'about', 'contact',
    'coach', 'coaches', 'scout', 'scouts', 'athlete', 'athletes', 'organization', 'organizations', 'team', 'teams',
    'null', 'none', 'undefined', 'unknown', 'anonymous', 'user', 'username', 'test', 'tester', 'everyone', 'here',
}
# Rude / unsafe words: refused anywhere inside the username.
# (Only fragments that never appear in real names: "dickson", "skill" and
# "grape" stay allowed.)
_BANNED_PARTS = (
    'fuck', 'shit', 'bitch', 'pussy', 'cunt', 'nigg', 'whore', 'slut', 'porn', 'nude', 'naked',
    'nazi', 'hitler', 'faggot', 'retard', 'asshole',
)

USERNAME_HELP = ('3-20 characters: letters, numbers and _ (starts with a letter). '
                 'This is how people tell you apart from others with the same name.')


def normalize_username(raw):
    """'  @MusuK ' -> 'musuk' (no validation)."""
    text = unicodedata.normalize('NFKC', raw or '').strip()
    if text.startswith('@'):
        text = text[1:]
    return text.strip().lower()


def validate_username(raw):
    """(clean lowercase username, error message or None). Uniqueness is checked by the caller."""
    name = normalize_username(raw)
    if not name:
        return '', 'Choose a username.'
    if len(name) < USERNAME_MIN or len(name) > USERNAME_MAX:
        return '', f'Usernames are {USERNAME_MIN} to {USERNAME_MAX} characters long.'
    if not name[0].isascii() or not name[0].isalpha():
        return '', 'Usernames start with a letter.'
    if not _FORMAT_RE.match(name):
        return '', 'Use only letters, numbers and _ (no spaces, dots or symbols).'
    if '__' in name or name.endswith('_'):
        return '', 'Usernames cannot have "__" or end with "_".'
    if name in _RESERVED or name.replace('_', '') in _RESERVED:
        return '', 'That username is reserved. Please choose another.'
    if any(part in name for part in _BANNED_PARTS):
        return '', 'That username is not allowed. Please choose another.'
    return name, None


def suggestions(full_name, is_taken, limit=3):
    """A few free usernames built from the person's name: musu_kollie, mkollie, musukollie7..."""
    words = [re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', w).encode('ascii', 'ignore').decode().lower())
             for w in (full_name or '').split()]
    words = [w for w in words if w]
    if not words:
        return []
    first, last = words[0], words[-1]
    bases = [f'{first}_{last}', f'{first}{last}', f'{first[0]}{last}', f'{first}_{last[0]}', f'{last}_{first}']
    out, seen = [], set()

    def offer(candidate):
        candidate = candidate[:USERNAME_MAX].rstrip('_')
        if candidate in seen:
            return False
        seen.add(candidate)
        clean, error = validate_username(candidate)
        if not error and not is_taken(clean):
            out.append(clean)
        return len(out) >= limit

    for base in bases:
        if offer(base):
            return out
    for base in bases[:2]:
        for n in (7, 10, 11, 23, 99):
            if offer(f'{base[:USERNAME_MAX - len(str(n))]}{n}'):
                return out
    return out
