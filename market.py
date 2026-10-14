"""
D.A.R.T Market$: a marketplace for SPORTS gear only.

- Anyone with a D.A.R.T. account (athletes 18+, individual athletes 18+,
  coaches, scouts, organizations, officials, vendors) can sell; every
  listing is checked by the D.A.R.T Admin before it goes live.
- D.A.R.T never handles the money: buyer and seller chat in the app, see
  each other's phone number and pay each other directly, off D.A.R.T.
- Young athletes (under 18) can browse, but not sell or message strangers.

Security: product photos are re-encoded (EXIF / GPS removed, size capped)
and kept private until approval; strict field validation; per-user limits
on listings, new conversations and messages; reports + seller bans.
"""
import io
import re
from urllib.parse import quote
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from PIL import Image, ImageOps
from sqlalchemy import or_, func

from models import db, User, MarketListing, MarketThread, MarketMessage, MarketReport, MarketCartItem

ADULT_AGE = 18
SPORTS = ('Football', 'Basketball', 'Kickball', 'Multi-sport')
# The ONLY things D.A.R.T Market$ sells: sports gear for D.A.R.T.'s three sports.
CATEGORIES = {
    'football_boots': ('Football boots', 'Football', 'fa-shoe-prints'),
    'basketball_sneakers': ('Basketball sneakers', 'Basketball', 'fa-shoe-prints'),
    'kickball_skips': ('Kickball low-top skips', 'Kickball', 'fa-shoe-prints'),
    'sports_socks': ('Sports socks', None, 'fa-socks'),
    'jerseys': ('Jerseys & kits', None, 'fa-shirt'),
    'balls': ('Balls', None, 'fa-futbol'),
    'protection': ('Shin guards & protection', None, 'fa-shield-halved'),
    'goalkeeper': ('Goalkeeper gloves', 'Football', 'fa-hand'),
    'training': ('Training equipment', None, 'fa-dumbbell'),
    'bags': ('Sports bags', None, 'fa-bag-shopping'),
    'other_sports': ('Other sports gear', None, 'fa-medal'),
}
CONDITIONS = {'new': 'New', 'used': 'Used (good condition)'}
CURRENCIES = ('USD', 'LRD')
REPORT_REASONS = {
    'not_sports': 'Not sports gear',
    'scam': 'Looks like a scam',
    'fake': 'Fake / counterfeit product',
    'wrong_info': 'Wrong price or details',
    'offensive': 'Offensive content',
}
MAX_IMAGES = 5
IMAGE_MAX_BYTES = 6 * 1024 * 1024
IMAGE_MAX_SIDE = 1600
MAX_PENDING_PER_SELLER = 10
MAX_LIVE_FREE = 10                 # free sellers: live products at a time
MAX_LIVE_PREMIUM = 1000            # Vendor Premium: unlimited (sanity cap)
MAX_CART_ITEMS = 50
MAX_PRICE = {'USD': Decimal('20000'), 'LRD': Decimal('4000000')}
NEW_THREADS_PER_DAY = 20
MESSAGES_PER_10_MIN = 30
PAGE_SIZE = 24

Image.MAX_IMAGE_PIXELS = 40_000_000      # refuse decompression bombs

_TEXT_RE = re.compile(r'\s+')
_LIST_ITEM_RE = re.compile(r'^[\w .+/#-]{1,15}$', re.UNICODE)


def clean(text, limit):
    return _TEXT_RE.sub(' ', (text or '').replace('\x00', '')).strip()[:limit]


def clean_multiline(text, limit):
    lines = [_TEXT_RE.sub(' ', ln).strip() for ln in (text or '').replace('\x00', '').splitlines()]
    out = '\n'.join(ln for ln in lines if ln)
    return out[:limit]


def clean_list(text, limit=12):
    """'40, 41,42 ' -> '40, 41, 42' (short tokens only)."""
    items = []
    for raw in (text or '').split(','):
        item = clean(raw, 15)
        if item and _LIST_ITEM_RE.match(item) and item.lower() not in [i.lower() for i in items]:
            items.append(item)
    return ', '.join(items[:limit]) or None


# ---------- shop on the map (Google Maps directions) ----------
# Vendors pin their shop with "Use my current location" (standing in the shop)
# or by pasting a Google Maps link / coordinates. Only numbers are stored;
# links are never fetched by the server. Buyers get a Google Maps
# directions link (exact pin, else the written address).
_COORD_PATTERNS = (
    re.compile(r'!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)'),                                  # place pin: ...!3d6.30!4d-10.79
    re.compile(r'@(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)'),                                  # map centre: .../@6.30,-10.79,17z
    re.compile(r'[?&](?:q|query|ll|destination|daddr|center)=(-?\d{1,2}\.\d+)(?:,|%2C)\s*(-?\d{1,3}\.\d+)', re.I),
    re.compile(r'^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$'),               # "6.3005, -10.7969"
)
SHORT_MAP_LINK_RE = re.compile(r'(maps\.app\.goo\.gl|goo\.gl/maps)', re.I)


def parse_map_location(raw):
    """(lat, lng) from coordinates or a full Google Maps link, else None."""
    text = (raw or '').strip()[:600]
    if not text:
        return None
    for pattern in _COORD_PATTERNS:
        m = pattern.search(text)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0):
                return round(lat, 6), round(lng, 6)
    return None


def valid_coords(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0):
        return round(lat, 6), round(lng, 6)
    return None


def directions_url(user):
    """Google Maps directions to the shop: the pin, else the written address (Liberia)."""
    if not user or getattr(user, 'role', None) != 'Vendor':
        return ''
    if user.shop_lat is not None and user.shop_lng is not None:
        destination = f'{user.shop_lat:.6f},{user.shop_lng:.6f}'
    elif user.business_address:
        destination = ', '.join(x for x in (user.business_address, user.business_location, 'Liberia') if x)
    else:
        return ''
    return 'https://www.google.com/maps/dir/?api=1&destination=' + quote(destination, safe=',')


def map_embed_url(user):
    """A small embedded Google map of the pinned shop ('' without a pin)."""
    if not user or user.shop_lat is None or user.shop_lng is None:
        return ''
    return f'https://www.google.com/maps?q={user.shop_lat:.6f},{user.shop_lng:.6f}&z=17&output=embed'


# ---------- who can do what ----------
def age_of(user, age_fn):
    return age_fn(user) if user.role == 'Athlete' else None


def is_rejected(user):
    """A vendor / buyer the D.A.R.T Admin did not approve (until they fix it and resubmit)."""
    return bool(user and getattr(user, 'role', None) in ('Vendor', 'Buyer') and user.market_status == 'rejected')


def rejected_ids_query():
    """Sellers whose account is rejected: their products are hidden until they are approved."""
    return db.session.query(User.id).filter(User.market_status == 'rejected')


def market_block_reason(user, age_fn):
    """Why this signed-in user can't SELL or MESSAGE (None = allowed)."""
    if not getattr(user, 'is_authenticated', False):
        return 'Create a D.A.R.T. account (or log in) to use D.A.R.T Market$.'
    if user.is_suspended or user.market_banned:
        return 'Your account cannot sell or message on D.A.R.T Market$.'
    if is_rejected(user):
        return ('The D.A.R.T Admin did not approve this account: ' + (user.market_reject_reason or 'see your notifications') +
                '. Fix it and resubmit to use D.A.R.T Market$ again.')
    if user.role == 'System':
        return 'The D.A.R.T Admin reviews listings; it does not sell.'
    if user.role == 'Athlete':
        age = age_fn(user)
        if age is None or age < ADULT_AGE:
            return ('Athletes under 18 can browse D.A.R.T Market$, but buying and selling is done by a parent, '
                    'guardian or coach.')
    return None


# ---------- listing form ----------
def parse_price(raw, currency):
    try:
        price = Decimal(str(raw or '').replace(',', '').strip()).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None
    if price <= 0 or price > MAX_PRICE[currency]:
        return None
    return price


def validate_listing(form):
    """(values dict, error message or None)."""
    title = clean(form.get('title'), 80)
    if len(title) < 4:
        return None, 'Give the product a clear name (at least 4 characters).'
    category = form.get('category')
    if category not in CATEGORIES:
        return None, 'Choose a category. D.A.R.T Market$ only sells sports gear.'
    sport = form.get('sport')
    if sport not in SPORTS:
        return None, 'Choose the sport this product is for.'
    required_sport = CATEGORIES[category][1]
    if required_sport and sport != required_sport:
        return None, f'{CATEGORIES[category][0]} are {required_sport} gear: choose {required_sport} as the sport.'
    condition = form.get('condition')
    if condition not in CONDITIONS:
        return None, 'Is it new or used?'
    currency = form.get('currency') if form.get('currency') in CURRENCIES else None
    if not currency:
        return None, 'Choose the currency (USD or LRD).'
    price = parse_price(form.get('price'), currency)
    if price is None:
        return None, f'Enter a valid price (more than 0, at most {MAX_PRICE[currency]:,.0f} {currency}).'
    try:
        quantity = int(form.get('quantity') or 1)
    except ValueError:
        quantity = 0
    if not 1 <= quantity <= 500:
        return None, 'Quantity must be between 1 and 500.'
    description = clean_multiline(form.get('description'), 1500)
    if len(description) < 20:
        return None, 'Describe the product (at least 20 characters): size, condition, what is included.'
    return {
        'title': title, 'category': category, 'sport': sport, 'condition': condition, 'currency': currency, 'price': price,
        'quantity': quantity, 'description': description,
        'brand': clean(form.get('brand'), 40) or None,
        'sizes': clean_list(form.get('sizes')),
        'colors': clean_list(form.get('colors')),
        'specs': clean_multiline(form.get('specs'), 1000) or None,
        'location': clean(form.get('location'), 80) or None,
    }, None


def clean_image(file_storage):
    """
    Validate and re-encode an uploaded product photo. Returns JPEG bytes, or
    None if it isn't a real photo / too big. Re-encoding drops EXIF data
    (phone GPS location, camera serial...) and anything hidden in the file.
    """
    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if not size or size > IMAGE_MAX_BYTES:
        return None
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    if not (head.startswith(b'\xff\xd8\xff') or head.startswith(b'\x89PNG\r\n\x1a\n')
            or (head[:4] == b'RIFF' and head[8:12] == b'WEBP')):
        return None
    try:
        with Image.open(file_storage.stream) as probe:
            probe.verify()
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert('RGB')
            img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=85, optimize=True)
            return out.getvalue()
    except Exception:
        return None


def live_limit(premium_vendor):
    return MAX_LIVE_PREMIUM if premium_vendor else MAX_LIVE_FREE


def seller_limits_error(user, premium_vendor=False):
    pending = MarketListing.query.filter_by(seller_id=user.id, status='pending').count()
    if pending >= MAX_PENDING_PER_SELLER:
        return f'You have {pending} listings waiting for review. Please wait for the D.A.R.T Admin to check them.'
    live = MarketListing.query.filter_by(seller_id=user.id, status='approved').count()
    limit = live_limit(premium_vendor)
    if live + pending >= limit:
        extra = '' if premium_vendor else ' Vendor Premium gives unlimited products.' if user.role == 'Vendor' else ''
        return f'You can have {limit} products on D.A.R.T Market$ at a time. Mark sold items as sold first.{extra}'
    return None


# ---------- vendors ----------
_BUSINESS_RE = re.compile(r"^[\w][\w .,&'()/-]{1,99}$", re.UNICODE)


def validate_business_name(raw):
    """(name, error): a registered business / trading name, e.g. 'Sinkor Sports & Co.'."""
    name = clean(raw, 100)
    if len(name) < 2 or not _BUSINESS_RE.match(name) or not re.search(r'[^\W\d_]', name):
        return None, 'Enter your business name (letters, numbers, spaces and . , & \' ( ) / - only).'
    return name, None


# ---------- cart ----------
def cart_for(user):
    """[(seller, [items])] grouped by seller, newest first. Items whose product is no longer on sale stay, marked."""
    items = (MarketCartItem.query.filter_by(user_id=user.id).order_by(MarketCartItem.created_at.desc()).all())
    groups, order = {}, []
    for it in items:
        sid = it.listing.seller_id
        if sid not in groups:
            groups[sid] = []
            order.append(it.listing.seller)
        groups[sid].append(it)
    return [(seller, groups[seller.id]) for seller in order]


def cart_totals(items):
    """{'USD': Decimal, 'LRD': Decimal} for the items still in the cart and on sale."""
    totals = {}
    for it in items:
        if it.listing.status == 'approved' and (it.status or 'cart') == 'cart':
            totals[it.listing.currency] = totals.get(it.listing.currency, Decimal('0')) + it.listing.price * it.quantity
    return totals


def cart_count(user):
    if not getattr(user, 'is_authenticated', False):
        return 0
    return MarketCartItem.query.filter_by(user_id=user.id, status='cart').count()


# ---------- order tracker ----------
# In cart -> Sent to vendor -> Left vendor shop -> Buyer's possession.
ORDER_STEPS = (('cart', 'In cart', 'fa-cart-shopping'), ('requested', 'Sent to vendor', 'fa-paper-plane'),
               ('sold', 'Left vendor shop', 'fa-truck-fast'), ('received', "Buyer's possession", 'fa-house-circle-check'))
ORDER_STEP_INDEX = {key: i for i, (key, _, _) in enumerate(ORDER_STEPS)}
# A vendor must mark a sent order as sold (or not sold) within this time:
# overdue orders are shown to the D.A.R.T Admin (account suspension risk).
ORDER_OVERDUE_DAYS = 7


def order_step(item):
    return ORDER_STEP_INDEX.get(item.status or 'cart', 0)


def vendor_orders(user, limit=60):
    """Orders buyers sent to this seller (newest first)."""
    return (MarketCartItem.query.join(MarketListing, MarketCartItem.listing_id == MarketListing.id)
            .filter(MarketListing.seller_id == user.id, MarketCartItem.status.in_(('requested', 'sold', 'received', 'cancelled')))
            .order_by(MarketCartItem.requested_at.desc().nullslast(), MarketCartItem.id.desc()).limit(limit).all())


def overdue_orders(seller_id=None):
    """Sent orders a vendor has not marked sold / not sold in ORDER_OVERDUE_DAYS."""
    cutoff = datetime.utcnow() - timedelta(days=ORDER_OVERDUE_DAYS)
    q = (MarketCartItem.query.join(MarketListing, MarketCartItem.listing_id == MarketListing.id)
         .filter(MarketCartItem.status == 'requested', MarketCartItem.requested_at < cutoff))
    if seller_id is not None:
        q = q.filter(MarketListing.seller_id == seller_id)
    return q.order_by(MarketCartItem.requested_at.asc()).limit(200).all()


def is_overdue(item):
    return (item.status == 'requested' and item.requested_at is not None
            and item.requested_at < datetime.utcnow() - timedelta(days=ORDER_OVERDUE_DAYS))


def cart_message(items):
    lines = ['Hi! I would like to buy:']
    for it in items:
        if it.listing.status != 'approved':
            continue
        extra = ', '.join(x for x in (f'size {it.size}' if it.size else '', it.color or '') if x)
        lines.append(f'- {it.quantity} x {it.listing.title}' + (f' ({extra})' if extra else ''))
    lines.append('Are they available? Where can we meet?')
    return '\n'.join(lines)[:1000]


# ---------- browsing ----------
def search(args):
    """Live (approved) listings matching the filters, newest first by default."""
    q = MarketListing.query.filter(MarketListing.status == 'approved', ~MarketListing.seller_id.in_(rejected_ids_query()))
    text = clean(args.get('q'), 60)
    if text:
        like = f'%{text}%'
        q = q.filter(or_(MarketListing.title.ilike(like), MarketListing.brand.ilike(like)))
    if args.get('sport') in SPORTS:
        q = q.filter(MarketListing.sport == args.get('sport'))
    if args.get('category') in CATEGORIES:
        q = q.filter(MarketListing.category == args.get('category'))
    if args.get('condition') in CONDITIONS:
        q = q.filter(MarketListing.condition == args.get('condition'))
    if args.get('currency') in CURRENCIES:
        q = q.filter(MarketListing.currency == args.get('currency'))
    for key, op in (('min', '>='), ('max', '<=')):
        try:
            value = Decimal(str(args.get(key) or '').strip())
        except (InvalidOperation, ValueError):
            continue
        q = q.filter(MarketListing.price >= value if op == '>=' else MarketListing.price <= value)
    for key, col in (('size', MarketListing.sizes), ('color', MarketListing.colors), ('brand', MarketListing.brand)):
        val = clean(args.get(key), 20)
        if val:
            q = q.filter(col.ilike(f'%{val}%'))
    sort = args.get('sort')
    if sort == 'price_asc':
        q = q.order_by(MarketListing.price.asc(), MarketListing.id.desc())
    elif sort == 'price_desc':
        q = q.order_by(MarketListing.price.desc(), MarketListing.id.desc())
    elif sort == 'popular':
        q = q.order_by(MarketListing.views.desc(), MarketListing.id.desc())
    else:
        q = q.order_by(MarketListing.reviewed_at.desc().nullslast(), MarketListing.id.desc())
    return q


def brands():
    rows = (db.session.query(MarketListing.brand).filter(MarketListing.status == 'approved', MarketListing.brand.isnot(None))
            .distinct().order_by(MarketListing.brand).limit(40).all())
    return [r[0] for r in rows]


# ---------- chat ----------
def new_thread_error(user):
    since = datetime.utcnow() - timedelta(days=1)
    if MarketThread.query.filter(MarketThread.buyer_id == user.id, MarketThread.created_at >= since).count() >= NEW_THREADS_PER_DAY:
        return 'You have contacted many sellers today. Please try again tomorrow.'
    return None


def message_rate_error(user):
    since = datetime.utcnow() - timedelta(minutes=10)
    if MarketMessage.query.filter(MarketMessage.sender_id == user.id, MarketMessage.created_at >= since).count() >= MESSAGES_PER_10_MIN:
        return 'You are sending messages too fast. Please wait a few minutes.'
    return None


def threads_for(user):
    return (MarketThread.query.filter(or_(MarketThread.buyer_id == user.id, MarketThread.seller_id == user.id))
            .order_by(MarketThread.last_message_at.desc()).limit(100).all())


def unread_count(user):
    if not getattr(user, 'is_authenticated', False):
        return 0
    return (db.session.query(func.count(MarketMessage.id))
            .join(MarketThread, MarketThread.id == MarketMessage.thread_id)
            .filter(or_(MarketThread.buyer_id == user.id, MarketThread.seller_id == user.id),
                    MarketMessage.sender_id != user.id, MarketMessage.read_at.is_(None)).scalar() or 0)


def pending_counts():
    return {
        'listings': MarketListing.query.filter_by(status='pending').count(),
        'reports': MarketReport.query.filter_by(status='open').count(),
    }
