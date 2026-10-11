"""
Full (government) names at registration: the name exactly as on the
person's ID or passport. No nicknames, titles, handles, numbers or emoji.

validate_full_name("  john  felix WLEH-SAM ") -> ("John Felix Wleh-Sam", None)
validate_full_name("Lil Baby")                -> ("", "...nickname...")

Rules
- First AND last name (2 to 6 names). A middle initial is fine ("Mandella
  L. Wleh-Sam"); the first and last names must be real names (2+ letters).
- Letters only, plus the hyphen and apostrophe used in real names
  (Wleh-Sam, O'Neil) and a dot after an initial. Accented letters are fine.
- Generational suffixes Jr / Sr / II / III / IV are allowed at the end.
- No titles (Mr, Dr, Rev, Coach...), nickname words (Lil, Baby, Boss...),
  placeholders (Test, Unknown...) or the same letter 3+ times ("Johnnn").
- Spaces are tidied and all-lowercase / ALL-CAPS names get capitals.
"""
import re
import unicodedata

NAME_MAX = 100
NAME_MIN_PARTS = 2
NAME_MAX_PARTS = 6

# One name: letters (any alphabet), joined by - or ' (Wleh-Sam, O'Neil).
_PART_RE = re.compile(r"^[^\W\d_]+(?:[-'][^\W\d_]+)*$")
# A middle initial: one letter, optional dot.
_INITIAL_RE = re.compile(r"^[^\W\d_]\.?$")
_SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv'}
_REPEATED_RE = re.compile(r'([^\W\d_])\1\1', re.IGNORECASE)

# Words that are never part of a legal name.
_TITLES = {
    'mr', 'mrs', 'ms', 'miss', 'mx', 'dr', 'prof', 'rev', 'hon', 'sir', 'madam', 'madame', 'dame', 'lord',
    'coach', 'captain', 'capt', 'pastor', 'bishop', 'apostle', 'prophet', 'evangelist', 'sheikh', 'imam',
    'officer', 'sgt', 'gen', 'col', 'lt', 'esq',
}
_NICKNAMES = {
    'lil', 'lill', 'lilbaby', 'baby', 'babe', 'boss', 'bossman', 'bigman', 'bigboy', 'smallie', 'bro', 'sis',
    'guy', 'dude', 'kid', 'boyz', 'gal', 'shorty', 'swag', 'swaggy', 'gangsta', 'thug', 'goat', 'legend',
    'baller', 'striker', 'goalie', 'goalkeeper', 'footballer', 'player', 'superstar', 'champ',
    'killer', 'beast', 'boy', 'girl', 'bae', 'daddy', 'mama', 'papa',
}
_PLACEHOLDERS = {
    'test', 'tester', 'user', 'username', 'admin', 'administrator', 'unknown', 'none', 'null', 'nil',
    'anonymous', 'anon', 'name', 'firstname', 'lastname', 'surname', 'fullname', 'asdf', 'qwerty', 'abc',
    'xyz', 'fake', 'nobody', 'official', 'dart', 'fc', 'team', 'sports', 'real',
}
_BLOCKED = _TITLES | _NICKNAMES | _PLACEHOLDERS

NAME_HELP = ('Use your full government name exactly as on your ID or passport: first and last name '
             '(and middle name if you have one). No nicknames, titles, numbers or emoji.')


def _capitalise(part):
    """'wleh-sam' -> 'Wleh-Sam', "o'neil" -> "O'Neil" (only for all-lower / ALL-CAPS input)."""
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), part)


def validate_full_name(raw):
    """(clean name, error message or None)."""
    text = unicodedata.normalize('NFC', raw or '')
    text = text.replace('’', "'").replace('‘', "'")       # curly apostrophes
    text = text.replace('‐', '-').replace('‑', '-').replace('–', '-')
    text = ' '.join(text.split())

    if not text:
        return '', 'Full name is required.'
    if len(text) > NAME_MAX:
        return '', f'Full name can be up to {NAME_MAX} characters.'

    parts = text.split(' ')
    # Generational suffix at the end (John Doe Jr.)
    core = parts[:-1] if len(parts) > 2 and parts[-1].lower() in _SUFFIXES else parts

    if len(core) < NAME_MIN_PARTS:
        return '', 'Enter your first AND last name, exactly as on your ID or passport.'
    if len(parts) > NAME_MAX_PARTS:
        return '', 'That is too many names. Use your name exactly as on your ID or passport.'

    for i, part in enumerate(core):
        first_or_last = i in (0, len(core) - 1)
        if part.rstrip('.').lower() in _BLOCKED:          # "Dr.", "Mr.", "Coach"
            return '', ('Nicknames, titles and made-up names are not allowed. '
                        'Use your full government name exactly as on your ID or passport.')
        if _INITIAL_RE.match(part):
            if first_or_last:
                return '', 'Write your first and last name in full (initials only for a middle name).'
            continue
        if not _PART_RE.match(part):
            return '', ('Names can only contain letters, a hyphen or an apostrophe '
                        '(no numbers, emoji, symbols or brackets).')
        letters = re.sub(r"[-']", '', part)
        if len(letters) < 2:
            return '', 'Each name must have at least 2 letters.'
        if part.lower().strip("'-") in _BLOCKED or any(w.lower() in _BLOCKED for w in re.split(r"[-']", part)):
            return '', ('Nicknames, titles and made-up names are not allowed. '
                        'Use your full government name exactly as on your ID or passport.')
        if _REPEATED_RE.search(part):
            return '', 'Please check the spelling of your name (no letter 3 times in a row).'

    if len({p.lower() for p in core}) == 1:
        return '', 'Enter your real first and last name.'

    # Tidy capitals only when typed all-lowercase or ALL-CAPS ("McDonald" stays as typed).
    if text == text.lower() or text == text.upper():
        parts = [p if p.lower() in _SUFFIXES and p.lower() not in ('jr', 'jr.', 'sr', 'sr.') else _capitalise(p) for p in parts]
        parts = [p.upper() if p.lower() in ('ii', 'iii', 'iv') else p for p in parts]
    return ' '.join(parts), None


def client_rules():
    """The same lists for the in-browser check on the registration forms."""
    return {'blocked': sorted(_BLOCKED), 'suffixes': sorted(_SUFFIXES), 'max': NAME_MAX, 'help': NAME_HELP}
