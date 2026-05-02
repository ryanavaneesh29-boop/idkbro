from flask import Flask, abort, g, render_template, request, redirect, url_for, session, send_from_directory
import json
import hashlib
import mimetypes
import sqlite3
import uuid
import re
import os
import secrets
import smtplib
import time
import threading
from urllib.parse import urlparse
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from functools import wraps
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
try:
    from PIL import Image, UnidentifiedImageError
except ImportError:
    Image = None
    UnidentifiedImageError = OSError

app = Flask(__name__)
IS_PRODUCTION = os.getenv('FLASK_ENV', '').lower() == 'production' or os.getenv('APP_ENV', '').lower() == 'production'
if IS_PRODUCTION and not os.getenv('FLASK_SECRET_KEY'):
    raise RuntimeError('FLASK_SECRET_KEY must be set in production.')
app.secret_key = os.getenv('FLASK_SECRET_KEY') or secrets.token_hex(54)
if os.getenv('TRUST_PROXY', '').lower() in {'1', 'true', 'yes'}:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=int(os.getenv('PROXY_FIX_X_FOR', '1')),
        x_proto=int(os.getenv('PROXY_FIX_X_PROTO', '1')),
        x_host=int(os.getenv('PROXY_FIX_X_HOST', '1')),
        x_port=int(os.getenv('PROXY_FIX_X_PORT', '1')),
        x_prefix=int(os.getenv('PROXY_FIX_X_PREFIX', '0'))
    )
app.config.update(
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'true').lower() in {'1', 'true', 'yes'},
    PERMANENT_SESSION_LIFETIME=timedelta(days=14)
)

# Data storage
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / 'users.json'
TWEETS_FILE = DATA_DIR / 'tweets.json'
NOTIFICATIONS_FILE = DATA_DIR / 'notifications.json'
MESSAGES_FILE = DATA_DIR / 'messages.json'
RATE_LIMITS_DB = DATA_DIR / 'rate_limits.sqlite3'
APP_DB = DATA_DIR / 'app.sqlite3'
DATA_LOCK_FILE = DATA_DIR / '.data.lock'
UPLOAD_DIR = DATA_DIR / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(DATA_DIR, 0o700)
    os.chmod(UPLOAD_DIR, 0o700)
except OSError:
    pass
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'webm', 'm4v'}
ALLOWED_MEDIA_MIME_TYPES = {
    'image/png', 'image/jpeg', 'image/gif', 'image/webp',
    'video/mp4', 'video/quicktime', 'video/webm', 'video/x-m4v'
}
DATA_LOCK = threading.Lock()
RESET_REQUEST_MESSAGE = 'If an account exists for that email, we emailed a password reset link.'
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
TRUSTED_HOSTS = {
    host.strip().lower()
    for host in os.getenv('TRUSTED_HOSTS', 'localhost,127.0.0.1').split(',')
    if host.strip()
}
MEDIA_BASE_URL = os.getenv('MEDIA_BASE_URL', '').rstrip('/')
if IS_PRODUCTION and not PUBLIC_BASE_URL:
    raise RuntimeError('PUBLIC_BASE_URL must be set in production.')
if PUBLIC_BASE_URL:
    parsed_public_base_url = urlparse(PUBLIC_BASE_URL)
    if not parsed_public_base_url.scheme or not parsed_public_base_url.netloc:
        raise RuntimeError('PUBLIC_BASE_URL must be an absolute URL.')
    if IS_PRODUCTION and parsed_public_base_url.scheme != 'https':
        raise RuntimeError('PUBLIC_BASE_URL must use https in production.')

def csp_source_from_url(url):
    if not url:
        return "'self'"
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "'self'"

class DataFileLock:
    def __enter__(self):
        while True:
            try:
                self.fd = os.open(DATA_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - DATA_LOCK_FILE.stat().st_mtime > 30:
                        DATA_LOCK_FILE.unlink()
                        continue
                except FileNotFoundError:
                    continue
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        os.close(self.fd)
        try:
            DATA_LOCK_FILE.unlink()
        except FileNotFoundError:
            pass

def init_rate_limit_db():
    with sqlite3.connect(RATE_LIMITS_DB) as conn:
        conn.execute(
            'CREATE TABLE IF NOT EXISTS attempts (key TEXT NOT NULL, timestamp REAL NOT NULL)'
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_attempts_key_time ON attempts (key, timestamp)')

init_rate_limit_db()

def load_data():
    users = {}
    tweets = {}
    notifications = {}
    messages = {}
    with DATA_LOCK:
        with DataFileLock():
            if USERS_FILE.exists():
                with open(USERS_FILE, 'r') as f:
                    users = json.load(f)
            if TWEETS_FILE.exists():
                with open(TWEETS_FILE, 'r') as f:
                    tweets = json.load(f)
            if NOTIFICATIONS_FILE.exists():
                with open(NOTIFICATIONS_FILE, 'r') as f:
                    notifications = json.load(f)
            if MESSAGES_FILE.exists():
                with open(MESSAGES_FILE, 'r') as f:
                    messages = json.load(f)
    return users, tweets, notifications, messages

def atomic_write_json(path, payload):
    temp_path = path.with_suffix(f'{path.suffix}.tmp')
    with open(temp_path, 'w') as f:
        json.dump(payload, f, indent=2)
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass
    os.replace(temp_path, path)

def save_data(users, tweets, notifications=None, messages=None):
    with DATA_LOCK:
        with DataFileLock():
            atomic_write_json(USERS_FILE, users)
            atomic_write_json(TWEETS_FILE, tweets)
            if notifications is not None:
                atomic_write_json(NOTIFICATIONS_FILE, notifications)
            if messages is not None:
                atomic_write_json(MESSAGES_FILE, messages)

def hash_password(password):
    return generate_password_hash(password)

def legacy_sha256_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def is_legacy_password_hash(stored_hash):
    return bool(stored_hash and re.fullmatch(r'[0-9a-f]{64}', stored_hash))

def hash_reset_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def verify_password(stored_hash, password):
    if not stored_hash:
        return False
    if is_legacy_password_hash(stored_hash):
        return False
    try:
        return check_password_hash(stored_hash, password)
    except ValueError:
        return False

def rate_limit_key(action, identifier):
    return f"{action}:{identifier}"

def is_rate_limited(action, identifier, limit, window_seconds):
    key = rate_limit_key(action, identifier)
    now = time.time()
    cutoff = now - window_seconds
    with sqlite3.connect(RATE_LIMITS_DB, timeout=5) as conn:
        conn.execute('DELETE FROM attempts WHERE timestamp < ?', (cutoff,))
        current_count = conn.execute(
            'SELECT COUNT(*) FROM attempts WHERE key = ? AND timestamp >= ?',
            (key, cutoff)
        ).fetchone()[0]
        if current_count >= limit:
            return True
        conn.execute('INSERT INTO attempts (key, timestamp) VALUES (?, ?)', (key, now))
    return False

def csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token

@app.context_processor
def inject_csrf_token():
    return {'csrf_token': csrf_token}

def request_host():
    return request.host.split(':', 1)[0].lower()

def is_trusted_host(host):
    return not TRUSTED_HOSTS or host in TRUSTED_HOSTS

def client_ip():
    return request.remote_addr or 'unknown'

def safe_redirect_url(default_endpoint='index'):
    fallback = url_for(default_endpoint)
    referrer = request.referrer
    if not referrer:
        return fallback
    parsed_referrer = urlparse(referrer)
    if parsed_referrer.scheme or parsed_referrer.netloc:
        if parsed_referrer.netloc.lower() != request.host.lower():
            return fallback
    path = parsed_referrer.path or '/'
    if not path.startswith('/'):
        return fallback
    if parsed_referrer.query:
        path = f"{path}?{parsed_referrer.query}"
    return path

@app.before_request
def validate_request_security():
    if request.path.startswith('/static/uploads/'):
        abort(404)
    if not is_trusted_host(request_host()):
        abort(400)
    if request.method != 'POST':
        return
    sent_token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
    if not sent_token or not secrets.compare_digest(sent_token, session.get('_csrf_token', '')):
        abort(400)

@app.after_request
def add_security_headers(response):
    media_csp_source = csp_source_from_url(MEDIA_BASE_URL)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        f"img-src 'self' data: {media_csp_source}; "
        f"media-src 'self' {media_csp_source}; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    if request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response

def generate_id():
    return uuid.uuid4().hex

def allowed_media_extension(filename):
    if '.' not in filename:
        return None
    extension = filename.rsplit('.', 1)[1].lower()
    if extension in ALLOWED_IMAGE_EXTENSIONS:
        return 'image'
    if extension in ALLOWED_VIDEO_EXTENSIONS:
        return 'video'
    return None

def is_valid_email(email):
    return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email))

def is_valid_username(username):
    return bool(re.fullmatch(r'[a-z0-9_]{3,30}', username))

def normalize_user_record(uid, user):
    user.setdefault('id', uid)
    user.setdefault('email', '')
    user.setdefault('following', [])
    user.setdefault('followers', [])
    user.setdefault('drafts', [])
    user.setdefault('pending_requests', [])
    user.setdefault('blocked', [])
    user.setdefault('is_private', False)
    user.setdefault('pinned_tweet', None)
    user.setdefault('reset_token', None)
    user.setdefault('reset_token_expires_at', None)
    user.setdefault('is_admin', False)
    return user

def normalize_tweet_record(tid, tweet):
    tweet.setdefault('id', tid)
    tweet.setdefault('likes', [])
    tweet.setdefault('retweets', [])
    tweet.setdefault('replies', [])
    tweet['parsed_content'] = parse_tweet_content(tweet.get('content', ''))
    tweet['media'] = normalize_media(tweet.get('media'))
    return tweet

def ensure_admin_account():
    admin_username = os.getenv('ADMIN_USERNAME')
    admin_email = os.getenv('ADMIN_EMAIL')
    admin_password = os.getenv('ADMIN_PASSWORD')
    admin_user_id = os.getenv('ADMIN_USER_ID')

    if not admin_username:
        return False

    for uid, user in users.items():
        if user.get('username') == admin_username:
            if admin_user_id != uid:
                return False
            user['is_admin'] = True
            if admin_email and not user.get('email'):
                user['email'] = admin_email
            if admin_password:
                user['password_hash'] = hash_password(admin_password)
                return True
            return False

    if not admin_email or not admin_password:
        return False

    uid = generate_id()
    users[uid] = normalize_user_record(uid, {
        'id': uid,
        'username': admin_username,
        'email': admin_email,
        'display_name': admin_username,
        'bio': 'Admin account',
        'password_hash': hash_password(admin_password),
        'created_at': datetime.now().isoformat(),
        'is_admin': True
    })
    return True

def neutralize_unsafe_default_admin():
    changed = False
    for user in users.values():
        if user.get('username') == 'rytoobov' and user.get('password_hash') == legacy_sha256_password('change-me-admin'):
            user['password_hash'] = hash_password(secrets.token_urlsafe(32))
            user['is_admin'] = False
            changed = True
    return changed

def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

def find_user_by_reset_token(token):
    token_hash = hash_reset_token(token)
    for uid, user in users.items():
        stored_token = user.get('reset_token')
        if stored_token not in {token, token_hash}:
            continue
        expires_at = parse_iso_datetime(user.get('reset_token_expires_at'))
        if not expires_at or expires_at < datetime.utcnow():
            user['reset_token'] = None
            user['reset_token_expires_at'] = None
            save_data(users, tweets, notifications, messages)
            return None, None
        return uid, user
    return None, None

def send_password_reset_email(recipient_email, reset_link):
    smtp_host = os.getenv('SMTP_HOST')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_username = os.getenv('SMTP_USERNAME')
    smtp_password = os.getenv('SMTP_PASSWORD')
    mail_from = os.getenv('MAIL_FROM') or smtp_username

    if not all([smtp_host, smtp_username, smtp_password, mail_from]):
        raise RuntimeError('Email is not configured yet. Set SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, and MAIL_FROM.')

    message = EmailMessage()
    message['Subject'] = 'Reset your idkbro password'
    message['From'] = mail_from
    message['To'] = recipient_email
    message.set_content(
        'We received a request to reset your idkbro password.\n\n'
        f'Open this link to choose a new password:\n{reset_link}\n\n'
        'This link expires in 30 minutes. If you did not request this, you can ignore this email.'
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(message)

def build_password_reset_link(token):
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{url_for('reset_password', token=token)}"
    return url_for('reset_password', token=token, _external=True)

def get_user_by_username(username):
    for uid, user in users.items():
        if user['username'] == username:
            return uid, user
    return None, None

def get_user_by_email(email):
    email = email.lower().strip()
    for uid, user in users.items():
        if user.get('email', '').lower() == email:
            return uid, user
    return None, None

def parse_tweet_content(content):
    """Parse hashtags and mentions in tweet content"""
    content = str(escape(content or ''))

    # Parse hashtags
    hashtag_pattern = r'#(\w+)'
    content = re.sub(hashtag_pattern, r'<a href="/hashtag/\1" class="hashtag">#\1</a>', content)
    
    # Parse mentions
    mention_pattern = r'@(\w+)'
    content = re.sub(mention_pattern, r'<a href="/user/\1" class="mention">@\1</a>', content)
    
    return Markup(content)

def media_url(filename):
    return f"/media/{filename}"

def normalize_media(media):
    if not media:
        return None
    filename = secure_filename(media.get('filename', ''))
    if not filename:
        return None
    return {
        **media,
        'filename': filename,
        'url': media_url(filename)
    }

def media_signature_matches(media_type, extension, header):
    if media_type == 'image':
        image_signatures = {
            'png': header.startswith(b'\x89PNG\r\n\x1a\n'),
            'jpg': header.startswith(b'\xff\xd8\xff'),
            'jpeg': header.startswith(b'\xff\xd8\xff'),
            'gif': header.startswith(b'GIF87a') or header.startswith(b'GIF89a'),
            'webp': header.startswith(b'RIFF') and header[8:12] == b'WEBP'
        }
        return image_signatures.get(extension, False)
    video_signatures = {
        'mp4': b'ftyp' in header[:16],
        'm4v': b'ftyp' in header[:16],
        'mov': b'ftyp' in header[:16],
        'webm': header.startswith(b'\x1aE\xdf\xa3')
    }
    return video_signatures.get(extension, False)

def image_upload_is_parseable(file_storage):
    if Image is None:
        return True
    try:
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as image:
            image.verify()
        file_storage.stream.seek(0)
        return True
    except (OSError, UnidentifiedImageError):
        return False

def save_image_without_metadata(file_storage, destination, extension):
    if Image is None:
        file_storage.save(destination)
        return
    file_storage.stream.seek(0)
    with Image.open(file_storage.stream) as image:
        image_format = {
            'jpg': 'JPEG',
            'jpeg': 'JPEG',
            'png': 'PNG',
            'gif': 'GIF',
            'webp': 'WEBP'
        }[extension]
        save_kwargs = {}
        if image_format == 'JPEG' and image.mode not in {'RGB', 'L'}:
            image = image.convert('RGB')
        if image_format == 'GIF':
            save_kwargs['save_all'] = getattr(image, 'is_animated', False)
        image.save(destination, format=image_format, **save_kwargs)
    file_storage.stream.seek(0)

def save_uploaded_media(file_storage):
    if not file_storage or not file_storage.filename:
        return None, None

    media_type = allowed_media_extension(file_storage.filename)
    if not media_type:
        return None, 'Only PNG, JPG, JPEG, GIF, WEBP, MP4, MOV, WEBM, and M4V files are supported'
    extension = file_storage.filename.rsplit('.', 1)[1].lower()

    if file_storage.mimetype not in ALLOWED_MEDIA_MIME_TYPES:
        return None, 'That file type is not supported'

    header = file_storage.stream.read(32)
    file_storage.stream.seek(0)
    if not media_signature_matches(media_type, extension, header):
        return None, 'The uploaded file does not look like a valid image or video'
    if media_type == 'image' and not image_upload_is_parseable(file_storage):
        return None, 'The uploaded image could not be processed safely'

    filename = secure_filename(file_storage.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    destination = UPLOAD_DIR / unique_name
    if media_type == 'image':
        save_image_without_metadata(file_storage, destination, extension)
    else:
        file_storage.save(destination)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return {
        'type': media_type,
        'filename': unique_name,
        'url': media_url(unique_name)
    }, None

def build_tweet_view(tweet, viewer_id):
    tweet_user = users.get(tweet['user_id'], {})
    return {
        **tweet,
        'username': tweet_user.get('username', 'unknown'),
        'display_name': tweet_user.get('display_name', 'Unknown'),
        'is_liked': viewer_id in tweet.get('likes', []),
        'like_count': len(tweet.get('likes', [])),
        'retweet_count': len(tweet.get('retweets', [])),
        'parsed_content': parse_tweet_content(tweet.get('content', '')),
        'media': normalize_media(tweet.get('media'))
    }

def can_render_embedded_tweet(tweet_id, viewer_id):
    tweet = tweets.get(tweet_id)
    return bool(tweet and can_view_tweet(tweet, viewer_id))

def tweet_for_media(filename):
    safe_name = secure_filename(filename)
    for tweet in tweets.values():
        media = normalize_media(tweet.get('media'))
        if media and media.get('filename') == safe_name:
            return tweet
    return None

def build_message_view(message, current_user_id):
    return {
        **message,
        'is_from_current_user': message['sender_id'] == current_user_id,
        'parsed_content': parse_tweet_content(message.get('content', ''))
    }

def can_view_user_content(target_user, viewer_id):
    if not target_user:
        return False
    target_id = target_user.get('id')
    if target_id == viewer_id:
        return True
    viewer = users.get(viewer_id, {}) if viewer_id else {}
    if target_id in viewer.get('blocked', []):
        return False
    if viewer_id in target_user.get('blocked', []):
        return False
    return not target_user.get('is_private', False) or viewer_id in target_user.get('followers', [])

def can_view_tweet(tweet, viewer_id):
    tweet_user = users.get(tweet.get('user_id'), {})
    return can_view_user_content(tweet_user, viewer_id)

def is_visible_timeline_tweet(tweet):
    return not tweet.get('reply_to')

def delete_tweet_tree(tweet_id):
    tweet = tweets.get(tweet_id)
    if not tweet:
        return

    for reply_id in list(tweet.get('replies', [])):
        delete_tweet_tree(reply_id)

    media = tweet.get('media') or {}
    filename = media.get('filename')
    if filename:
        media_path = UPLOAD_DIR / secure_filename(filename)
        if media_path.exists():
            media_path.unlink()

    parent_id = tweet.get('reply_to')
    if parent_id and parent_id in tweets:
        parent_replies = tweets[parent_id].get('replies', [])
        if tweet_id in parent_replies:
            parent_replies.remove(tweet_id)

    del tweets[tweet_id]

def build_user_list_entry(user_id, viewer_id):
    user = users.get(user_id)
    if not user:
        return None
    return {
        'id': user_id,
        'username': user.get('username', 'unknown'),
        'display_name': user.get('display_name', 'Unknown'),
        'bio': user.get('bio', ''),
        'is_following': viewer_id in user.get('followers', [])
    }

def get_conversation_messages(current_user_id, other_user_id):
    convo = []
    for message in messages.values():
        participants = {message['sender_id'], message['receiver_id']}
        if participants == {current_user_id, other_user_id}:
            convo.append(build_message_view(message, current_user_id))
    convo.sort(key=lambda item: item['created_at'])
    return convo

def build_conversation_summaries(current_user_id):
    conversations = {}
    for message in messages.values():
        if current_user_id not in (message['sender_id'], message['receiver_id']):
            continue

        other_user_id = message['receiver_id'] if message['sender_id'] == current_user_id else message['sender_id']
        other_user = users.get(other_user_id)
        if not other_user:
            continue

        summary = conversations.setdefault(other_user_id, {
            'user_id': other_user_id,
            'username': other_user.get('username', 'unknown'),
            'display_name': other_user.get('display_name', 'Unknown'),
            'bio': other_user.get('bio', ''),
            'last_message': '',
            'last_message_at': '',
            'unread_count': 0
        })

        if not summary['last_message_at'] or message['created_at'] > summary['last_message_at']:
            summary['last_message'] = message.get('content', '')
            summary['last_message_at'] = message['created_at']

        if message['receiver_id'] == current_user_id and not message.get('read', False):
            summary['unread_count'] += 1

    return sorted(conversations.values(), key=lambda item: item['last_message_at'], reverse=True)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def create_notification(user_id, notification_type, from_user_id, tweet_id=None):
    """Create a notification for a user"""
    nid = generate_id()
    notifications[nid] = {
        'id': nid,
        'user_id': user_id,
        'type': notification_type,  # 'like', 'retweet', 'reply', 'follow'
        'from_user_id': from_user_id,
        'tweet_id': tweet_id,
        'created_at': datetime.now().isoformat(),
        'read': False
    }
    save_data(users, tweets, notifications, messages)

# Load data at startup
users, tweets, notifications, messages = load_data()
users = {uid: normalize_user_record(uid, user) for uid, user in users.items()}
tweets = {tid: normalize_tweet_record(tid, tweet) for tid, tweet in tweets.items()}
unsafe_admin_changed = neutralize_unsafe_default_admin()
admin_changed = ensure_admin_account()
if unsafe_admin_changed or admin_changed:
    save_data(users, tweets, notifications, messages)

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    current_user = users.get(session['user_id'])
    if not current_user:
        session.clear()
        return redirect(url_for('login'))
    
    # Get all tweets for the timeline
    timeline = []
    current_user_obj = users.get(session['user_id'])
    blocked = set(current_user_obj.get('blocked', []))
    for tid, tweet in sorted(tweets.items(), key=lambda x: x[1]['created_at'], reverse=True):
        if not is_visible_timeline_tweet(tweet):
            continue
        if not can_view_tweet(tweet, session['user_id']):
            continue
        if tweet['user_id'] in blocked:
            continue
        tweet_user = users.get(tweet['user_id'], {})
        if session['user_id'] in tweet_user.get('blocked', []):
            continue
        timeline.append(build_tweet_view(tweet, session['user_id']))
    
    return render_template('index.html', 
                         tweets=timeline[:20], 
                         current_user=current_user,
                         users=users,
                         all_tweets=tweets,
                         can_render_embedded_tweet=can_render_embedded_tweet,
                         viewer_id=session['user_id'])

@app.route('/media/<path:filename>')
def media_file(filename):
    safe_name = secure_filename(filename)
    if safe_name != filename:
        abort(404)
    media_path = UPLOAD_DIR / safe_name
    if not media_path.exists():
        abort(404)
    owning_tweet = tweet_for_media(safe_name)
    if not owning_tweet or not can_view_tweet(owning_tweet, session.get('user_id')):
        abort(404)
    response = send_from_directory(UPLOAD_DIR, safe_name, conditional=True)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    guessed_type, _ = mimetypes.guess_type(safe_name)
    if guessed_type:
        response.headers['Content-Type'] = guessed_type
    return response

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form['email'].lower().strip()
        password = request.form['password']
        client_id = client_ip()
        if is_rate_limited('login', client_id, 10, 300) or is_rate_limited('login', identifier, 8, 300):
            return render_template('login.html', error='Too many login attempts. Please wait a few minutes and try again.'), 429

        for uid, user in users.items():
            email_match = user.get('email', '').lower() == identifier
            username_match = user.get('username', '').lower() == identifier
            if not (email_match or username_match):
                continue
            if is_legacy_password_hash(user.get('password_hash')) and user.get('password_hash') == legacy_sha256_password(password):
                return render_template('login.html', error='This account needs a password reset before signing in.')
            if verify_password(user.get('password_hash'), password):
                session.clear()
                session['user_id'] = uid
                return redirect(url_for('index'))

        return render_template('login.html', error='Invalid email or password')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        client_id = client_ip()
        if is_rate_limited('register', client_id, 5, 900):
            return render_template('register.html', error='Too many signup attempts. Please wait and try again.'), 429
        username = request.form['username'].lower().strip()
        email = request.form['email'].lower().strip()
        display_name = request.form['display_name']
        password = request.form['password']
        bio = request.form.get('bio', '')

        if not is_valid_username(username):
            return render_template('register.html', error='Username must be 3-30 characters and use only letters, numbers, and underscores')
        if len(display_name) > 60 or len(bio) > 160:
            return render_template('register.html', error='Display name or bio is too long')

        # Check if username exists
        for user in users.values():
            if user['username'] == username:
                return render_template('register.html', error='Unable to create an account with those details')

        if not is_valid_email(email):
            return render_template('register.html', error='Please enter a valid email address')

        existing_email_id, _ = get_user_by_email(email)
        if existing_email_id:
            return render_template('register.html', error='Unable to create an account with those details')

        if len(password) < 8:
            return render_template('register.html', error='Password must be at least 8 characters')

        uid = generate_id()
        users[uid] = {
            'id': uid,
            'username': username,
            'email': email,
            'display_name': display_name,
            'bio': bio,
            'password_hash': hash_password(password),
            'following': [],
            'followers': [],
            'drafts': [],
            'pending_requests': [],
            'blocked': [],
            'is_private': False,
            'pinned_tweet': None,
            'created_at': datetime.now().isoformat()
        }
        save_data(users, tweets, notifications, messages)
        session['user_id'] = uid
        return redirect(url_for('index'))

    return render_template('register.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        client_id = client_ip()
        if is_rate_limited('forgot-password', client_id, 5, 900) or is_rate_limited('forgot-password', email, 3, 900):
            return render_template('forgot_password.html', error='Too many reset requests. Please wait and try again.'), 429

        if not is_valid_email(email):
            return render_template('forgot_password.html', error='Please enter a valid email address')

        uid, user = get_user_by_email(email)
        if not user:
            return render_template('forgot_password.html', success=RESET_REQUEST_MESSAGE)

        token = secrets.token_urlsafe(32)
        users[uid]['reset_token'] = hash_reset_token(token)
        users[uid]['reset_token_expires_at'] = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
        save_data(users, tweets, notifications, messages)

        reset_link = build_password_reset_link(token)
        try:
            send_password_reset_email(email, reset_link)
        except Exception:
            app.logger.exception('Password reset email failed')
            users[uid]['reset_token'] = None
            users[uid]['reset_token_expires_at'] = None
            save_data(users, tweets, notifications, messages)
            return render_template('forgot_password.html', success=RESET_REQUEST_MESSAGE)

        return render_template('forgot_password.html', success=RESET_REQUEST_MESSAGE)

    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    uid, user = find_user_by_reset_token(token)
    if not user:
        return render_template('reset_password.html', error='This reset link is invalid or has expired.')

    if request.method == 'POST':
        client_id = client_ip()
        if is_rate_limited('reset-password', client_id, 8, 900):
            return render_template('reset_password.html', token=token, error='Too many reset attempts. Please wait and try again.'), 429
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if len(new_password) < 8:
            return render_template('reset_password.html', token=token, error='Password must be at least 8 characters')

        if new_password != confirm_password:
            return render_template('reset_password.html', token=token, error='Passwords do not match')

        users[uid]['password_hash'] = hash_password(new_password)
        users[uid]['reset_token'] = None
        users[uid]['reset_token_expires_at'] = None
        save_data(users, tweets, notifications, messages)
        return render_template('reset_password.html', success='Password updated. You can log in now.')

    return render_template('reset_password.html', token=token)

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/drafts', methods=['GET','POST'])
@login_required
def drafts():
    current_user = users.get(session['user_id'])
    if request.method == 'POST':
        draft_content = request.form['content'].strip()
        if draft_content and len(draft_content) <= 280:
            current_user.setdefault('drafts', []).append(draft_content)
            save_data(users, tweets, notifications, messages)
        return redirect(url_for('drafts'))

    return render_template('drafts.html', current_user=current_user)

@app.route('/post_draft', methods=['POST'])
@login_required
def post_draft():
    content = request.form['content'].strip()
    if content and len(content) <= 280:
        current_user = users.get(session['user_id'])
        # Remove from drafts
        if content in current_user.get('drafts', []):
            current_user['drafts'].remove(content)
        
        # Post as tweet
        tid = generate_id()
        tweets[tid] = {
            'id': tid,
            'user_id': session['user_id'],
            'content': content,
            'parsed_content': parse_tweet_content(content),
            'created_at': datetime.now().isoformat(),
            'likes': [],
            'retweets': [],
            'replies': [],
            'is_retweet': False,
            'is_quote': False,
            'quote_to': None,
            'thread_to': None,
            'media': None
        }
        save_data(users, tweets, notifications, messages)
    return redirect(url_for('index'))

@app.route('/delete_draft', methods=['POST'])
@login_required
def delete_draft():
    content = request.form['content'].strip()
    current_user = users.get(session['user_id'])
    if content in current_user.get('drafts', []):
        current_user['drafts'].remove(content)
        save_data(users, tweets, notifications, messages)
    return redirect(url_for('drafts'))

@app.route('/save_draft', methods=['POST'])
@login_required
def save_draft():
    content = request.form['content'].strip()
    if content and len(content) <= 280:
        current_user = users.get(session['user_id'])
        current_user.setdefault('drafts', []).append(content)
        save_data(users, tweets, notifications, messages)
    return redirect(url_for('index'))

@app.route('/tweet', methods=['POST'])
@login_required
def post_tweet():
    content = request.form['content'].strip()
    media, media_error = save_uploaded_media(request.files.get('media'))
    if media_error:
        return render_template('index.html',
                             tweets=[build_tweet_view(tweet, session['user_id']) for _, tweet in sorted(tweets.items(), key=lambda x: x[1]['created_at'], reverse=True) if is_visible_timeline_tweet(tweet)][:20],
                             current_user=users.get(session['user_id']),
                             users=users,
                             all_tweets=tweets,
                             can_render_embedded_tweet=can_render_embedded_tweet,
                             viewer_id=session['user_id'],
                             error=media_error)

    if len(content) > 280 or (not content and not media):
        return redirect(url_for('index'))

    quote_id = request.form.get('quote_id')
    tid = generate_id()
    tweets[tid] = {
        'id': tid,
        'user_id': session['user_id'],
        'content': content,
        'parsed_content': parse_tweet_content(content),
        'created_at': datetime.now().isoformat(),
        'likes': [],
        'retweets': [],
        'replies': [],
        'is_retweet': False,
        'is_quote': bool(quote_id),
        'quote_to': quote_id or None,
        'thread_to': None,
        'media': media
    }

    # if quote, we can create reference link in template
    save_data(users, tweets, notifications, messages)
    return redirect(url_for('index'))

@app.route('/retweet/<tweet_id>', methods=['POST'])
@login_required
def retweet_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
    if tweet and not can_view_tweet(tweet, session['user_id']):
        return redirect(safe_redirect_url())
    if tweet and tweet['user_id'] != session['user_id']:
        user_id = session['user_id']
        retweets = set(tweet.get('retweets', []))
        if user_id in retweets:
            retweets.remove(user_id)
        else:
            retweets.add(user_id)
            create_notification(tweet['user_id'], 'retweet', user_id, tweet_id)
        tweet['retweets'] = list(retweets)
        save_data(users, tweets, notifications, messages)
    return redirect(safe_redirect_url())

@app.route('/quote/<tweet_id>', methods=['GET', 'POST'])
@login_required
def quote_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
    if not tweet or not can_view_tweet(tweet, session['user_id']):
        return redirect(url_for('index'))

    if request.method == 'POST':
        content = request.form['content'].strip()
        media, media_error = save_uploaded_media(request.files.get('media'))
        if media_error:
            return render_template('quote.html', tweet=tweet, current_user=users.get(session['user_id']), users=users, error=media_error)
        if len(content) > 280 or (not content and not media):
            return redirect(url_for('index'))
        qid = generate_id()
        tweets[qid] = {
            'id': qid,
            'user_id': session['user_id'],
            'content': content,
            'parsed_content': parse_tweet_content(content),
            'created_at': datetime.now().isoformat(),
            'likes': [],
            'retweets': [],
            'replies': [],
            'is_retweet': False,
            'is_quote': True,
            'quote_to': tweet_id,
            'thread_to': None,
            'media': media
        }
        create_notification(tweet['user_id'], 'quote', session['user_id'], tweet_id)
        save_data(users, tweets, notifications, messages)
        return redirect(url_for('index'))

    return render_template('quote.html', tweet=tweet, current_user=users.get(session['user_id']), users=users)

@app.route('/reply/<tweet_id>', methods=['POST'])
@login_required
def reply_tweet(tweet_id):

    content = request.form['content'].strip()
    media, media_error = save_uploaded_media(request.files.get('media'))
    if media_error:
        return redirect(safe_redirect_url())

    if len(content) > 280 or (not content and not media):
        return redirect(safe_redirect_url())
    
    original_tweet = tweets.get(tweet_id)
    if not original_tweet or not can_view_tweet(original_tweet, session['user_id']):
        return redirect(safe_redirect_url())
    
    tid = generate_id()
    tweets[tid] = {
        'id': tid,
        'user_id': session['user_id'],
        'content': content,
        'parsed_content': parse_tweet_content(content),
        'created_at': datetime.now().isoformat(),
        'likes': [],
        'retweets': [],
        'replies': [],
        'is_retweet': False,
        'reply_to': tweet_id,
        'media': media
    }
    
    # Add reply to original tweet's replies list
    if 'replies' not in original_tweet:
        original_tweet['replies'] = []
    original_tweet['replies'].append(tid)
        # Create notification for original tweet author
    if original_tweet['user_id'] != session['user_id']:
        create_notification(original_tweet['user_id'], 'reply', session['user_id'], tweet_id)
        save_data(users, tweets, notifications, messages)
    return redirect(safe_redirect_url())

@app.route('/like/<tweet_id>', methods=['POST'])
@login_required
def like_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
    if tweet and not can_view_tweet(tweet, session['user_id']):
        return redirect(safe_redirect_url())
    if tweet:
        user_id = session['user_id']
        likes = set(tweet.get('likes', []))
        if user_id in likes:
            likes.remove(user_id)
        else:
            likes.add(user_id)
            # Create notification for tweet author (if not liking own tweet)
            if tweet['user_id'] != user_id:
                create_notification(tweet['user_id'], 'like', user_id, tweet_id)
        tweet['likes'] = list(likes)
        save_data(users, tweets, notifications, messages)
    return redirect(safe_redirect_url())

@app.route('/follow/<username>', methods=['POST'])
@login_required
def follow_user(username):
    target = None
    for uid, user in users.items():
        if user['username'] == username:
            target = uid
            break
    
    if target and target != session['user_id']:
        current = users[session['user_id']]
        following = set(current.get('following', []))
        
        if target in following:
            following.remove(target)
            users[target]['followers'].remove(session['user_id'])
        else:
            # handle protected accounts
            if users[target].get('is_private'):
                users[target].setdefault('pending_requests', []).append(session['user_id'])
            else:
                following.add(target)
                users[target]['followers'].append(session['user_id'])
                create_notification(target, 'follow', session['user_id'])
        current['following'] = list(following)
        save_data(users, tweets, notifications, messages)
    
    return redirect(url_for('profile', username=username))

@app.route('/approve_follow/<username>/<follower_id>', methods=['POST'])
@login_required
def approve_follow(username, follower_id):
    uid, target_user = get_user_by_username(username)
    if not target_user or uid != session['user_id']:
        return redirect(url_for('index'))
    if follower_id in target_user.get('pending_requests', []):
        target_user['pending_requests'].remove(follower_id)
        target_user.setdefault('followers', []).append(follower_id)
        users[follower_id].setdefault('following', []).append(uid)
        create_notification(follower_id, 'follow', uid)
        save_data(users, tweets, notifications, messages)
    return redirect(url_for('profile', username=username))

@app.route('/block/<username>', methods=['POST'])
@login_required
def block_user(username):
    uid, target_user = get_user_by_username(username)
    if not target_user:
        return redirect(url_for('index'))
    current = users[session['user_id']]
    if uid == session['user_id']:
        return redirect(url_for('index'))
    current.setdefault('blocked', []).append(uid)
    if uid in current.get('following', []):
        current['following'].remove(uid)
        target_user.get('followers', []).remove(session['user_id'])
    save_data(users, tweets, notifications, messages)
    return redirect(url_for('profile', username=username))

@app.route('/user/<username>')
def profile(username):
    target_user = None
    for uid, user in users.items():
        if user['username'] == username:
            target_user = user
            target_user['id'] = uid
            break
    
    if not target_user:
        return 'User not found', 404
    
    # Get user's tweets (respect privacy and block list)
    user_tweets = []
    current_id = session.get('user_id')
    if not can_view_user_content(target_user, current_id):
        abort(404)
    is_follower = current_id in target_user.get('followers', [])
    allow_view = (not target_user.get('is_private', False)) or (target_user['id'] == current_id) or is_follower

    if allow_view:
        for tid, tweet in sorted(tweets.items(), key=lambda x: x[1]['created_at'], reverse=True):
            if not is_visible_timeline_tweet(tweet):
                continue
            if tweet['user_id'] == target_user['id']:
                if not can_view_tweet(tweet, current_id):
                    continue
                user_tweets.append(build_tweet_view(tweet, session.get('user_id')))
    
    is_following = False
    if 'user_id' in session:
        current = users.get(session['user_id'], {})
        is_following = target_user['id'] in current.get('following', [])
    
    return render_template('profile.html', 
                         user=target_user, 
                         tweets=user_tweets,
                         is_following=is_following,
                         current_user=users.get(session.get('user_id')),
                         all_tweets=tweets,
                         can_render_embedded_tweet=can_render_embedded_tweet)

@app.route('/who_to_follow')
@login_required
def who_to_follow():
    current_user = users.get(session['user_id'])
    following = set(current_user.get('following', []))
    blocked = set(current_user.get('blocked', []))
    candidates = []
    for uid, user in users.items():
        if uid == current_user['id'] or uid in following or uid in blocked:
            continue
        candidates.append({'id': uid, **user, 'follower_count': len(user.get('followers', []))})
    candidates.sort(key=lambda u: u['follower_count'], reverse=True)
    return render_template('who_to_follow.html', current_user=current_user, suggestions=candidates[:8])

@app.route('/user/<username>/following')
def following_list(username):
    uid, target_user = get_user_by_username(username)
    if not target_user:
        return 'User not found', 404

    viewer_id = session.get('user_id')
    if not can_view_user_content(target_user, viewer_id):
        abort(404)
    following_users = []
    for followed_id in target_user.get('following', []):
        entry = build_user_list_entry(followed_id, viewer_id)
        if entry:
            following_users.append(entry)

    return render_template(
        'user_connections.html',
        page_title=f"{target_user['display_name']} is following",
        page_heading='Following',
        page_user=target_user,
        connections=following_users,
        current_user=users.get(viewer_id)
    )

@app.route('/user/<username>/followers')
def followers_list(username):
    uid, target_user = get_user_by_username(username)
    if not target_user:
        return 'User not found', 404

    viewer_id = session.get('user_id')
    if not can_view_user_content(target_user, viewer_id):
        abort(404)
    follower_users = []
    for follower_id in target_user.get('followers', []):
        entry = build_user_list_entry(follower_id, viewer_id)
        if entry:
            follower_users.append(entry)

    return render_template(
        'user_connections.html',
        page_title=f"People following {target_user['display_name']}",
        page_heading='Followers',
        page_user=target_user,
        connections=follower_users,
        current_user=users.get(viewer_id)
    )

@app.route('/hashtag/<hashtag>')
def hashtag(hashtag):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    current_user = users.get(session['user_id'])
    if not current_user:
        session.clear()
        return redirect(url_for('login'))
    
    # Find tweets containing this hashtag
    hashtag_tweets = []
    for tid, tweet in tweets.items():
        if not is_visible_timeline_tweet(tweet):
            continue
        if not can_view_tweet(tweet, session['user_id']):
            continue
        if f'#{hashtag}' in tweet.get('content', '').lower():
            hashtag_tweets.append(build_tweet_view(tweet, session['user_id']))
    
    # Sort by creation date
    hashtag_tweets.sort(key=lambda x: x['created_at'], reverse=True)
    
    return render_template('hashtag.html', 
                         hashtag=hashtag,
                         tweets=hashtag_tweets[:50],  # Limit to 50 tweets
                         current_user=current_user,
                         users=users,
                         all_tweets=tweets,
                         can_render_embedded_tweet=can_render_embedded_tweet,
                         viewer_id=session['user_id'])

@app.route('/search', methods=['GET', 'POST'])
def search():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    query = request.args.get('q', '').strip()
    search_results = {'tweets': [], 'users': []}
    
    if query:
        # Search tweets
        for tid, tweet in tweets.items():
            if not is_visible_timeline_tweet(tweet):
                continue
            if not can_view_tweet(tweet, session.get('user_id')):
                continue
            if query.lower() in tweet.get('content', '').lower():
                search_results['tweets'].append(build_tweet_view(tweet, session.get('user_id')))
        
        # Search users
        for uid, user in users.items():
            if not can_view_user_content(user, session.get('user_id')):
                continue
            if (query.lower() in user['username'].lower() or 
                query.lower() in user['display_name'].lower()):
                search_results['users'].append({
                    **user,
                    'is_following': session.get('user_id') in user.get('followers', [])
                })
    
    current_user = users.get(session['user_id'])
    return render_template('search.html', 
                         query=query,
                         results=search_results,
                         current_user=current_user,
                         users=users,
                         all_tweets=tweets)

@app.route('/notifications')
@login_required
def notifications_view():
    current_user = users.get(session['user_id'])
    
    # Get user's notifications
    user_notifications = []
    for nid, notification in sorted(notifications.items(), key=lambda x: x[1]['created_at'], reverse=True):
        if notification['user_id'] == session['user_id']:
            from_user = users.get(notification['from_user_id'], {})
            tweet = tweets.get(notification.get('tweet_id', ''), {}) if notification.get('tweet_id') else None
            
            user_notifications.append({
                **notification,
                'from_username': from_user.get('username', 'unknown'),
                'from_display_name': from_user.get('display_name', 'Unknown'),
                'tweet_content': tweet.get('content', '')[:50] + '...' if tweet and len(tweet.get('content', '')) > 50 else tweet.get('content', '') if tweet else ''
            })
    
    # Mark notifications as read
    for nid in notifications:
        if notifications[nid]['user_id'] == session['user_id']:
            notifications[nid]['read'] = True
    save_data(users, tweets, notifications, messages)
    
    return render_template('notifications.html', 
                         notifications=user_notifications[:50],  # Limit to 50
                         current_user=current_user,
                         users=users)

@app.route('/toggle_theme', methods=['POST'])
@login_required
def toggle_theme():
    current_theme = session.get('theme', 'dark')
    session['theme'] = 'light' if current_theme == 'dark' else 'dark'
    return redirect(safe_redirect_url())

@app.route('/pin/<tweet_id>', methods=['POST'])
@login_required
def pin_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
    if tweet and tweet['user_id'] == session['user_id']:
        users[session['user_id']]['pinned_tweet'] = tweet_id
        save_data(users, tweets, notifications, messages)
    return redirect(safe_redirect_url())

@app.route('/delete/<tweet_id>', methods=['POST'])
@login_required
def delete_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
    current_user = users.get(session['user_id'], {})
    parent_tweet = tweets.get(tweet.get('reply_to')) if tweet else None
    can_delete = tweet and (
        tweet['user_id'] == session['user_id'] or
        current_user.get('is_admin', False) or
        (parent_tweet and parent_tweet.get('user_id') == session['user_id'])
    )
    if can_delete:
        delete_tweet_tree(tweet_id)
        save_data(users, tweets, notifications, messages)
    return redirect(safe_redirect_url())

@app.route('/messages')
@login_required
def messages_page():
    current_user = users.get(session['user_id'])
    conversations = build_conversation_summaries(session['user_id'])
    return render_template(
        'messages.html',
        conversations=conversations,
        current_user=current_user
    )

@app.route('/messages/<username>', methods=['GET', 'POST'])
@login_required
def message_thread(username):
    uid, target_user = get_user_by_username(username)
    if not target_user or uid == session['user_id']:
        return redirect(url_for('messages_page'))

    current_user = users.get(session['user_id'])

    if session['user_id'] in target_user.get('blocked', []) or uid in current_user.get('blocked', []):
        return redirect(url_for('messages_page'))

    if request.method == 'POST':
        content = request.form['content'].strip()
        if content and len(content) <= 280:
            mid = generate_id()
            messages[mid] = {
                'id': mid,
                'sender_id': session['user_id'],
                'receiver_id': uid,
                'content': content,
                'parsed_content': parse_tweet_content(content),
                'created_at': datetime.now().isoformat(),
                'read': False
            }
            save_data(users, tweets, notifications, messages)
            return redirect(url_for('message_thread', username=username))

    conversation_messages = get_conversation_messages(session['user_id'], uid)
    changed = False
    for message in conversation_messages:
        source = messages.get(message['id'])
        if source and source['receiver_id'] == session['user_id'] and not source.get('read', False):
            source['read'] = True
            changed = True
    if changed:
        save_data(users, tweets, notifications, messages)
        conversation_messages = get_conversation_messages(session['user_id'], uid)

    return render_template(
        'message_thread.html',
        target_user=target_user,
        messages=conversation_messages,
        current_user=current_user,
        conversations=build_conversation_summaries(session['user_id'])
    )

@app.route('/send_message/<username>', methods=['GET', 'POST'])
@login_required
def send_message(username):
    return redirect(url_for('message_thread', username=username))

@app.route('/mark_message_read/<message_id>', methods=['POST'])
@login_required
def mark_message_read(message_id):
    message = messages.get(message_id)
    if message and message['receiver_id'] == session['user_id']:
        message['read'] = True
        save_data(users, tweets, notifications, messages)
    other_user_id = message['sender_id'] if message else None
    other_user = users.get(other_user_id, {}) if other_user_id else {}
    if other_user.get('username'):
        return redirect(url_for('message_thread', username=other_user['username']))
    return redirect(url_for('messages_page'))

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
