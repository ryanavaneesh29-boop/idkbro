from flask import Flask, app, render_template, request, redirect, url_for, session
import json
import hashlib
import uuid
import re
import os
import secrets
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from functools import wraps
from werkzeug.utils import secure_filename

app.secret_key = os.getenv('FLASK_SECRET_KEY')
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(54))

# Data storage
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / 'users.json'
TWEETS_FILE = DATA_DIR / 'tweets.json'
NOTIFICATIONS_FILE = DATA_DIR / 'notifications.json'
MESSAGES_FILE = DATA_DIR / 'messages.json'
UPLOAD_DIR = BASE_DIR / 'static' / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'webm', 'm4v'}

def load_data():
    users = {}
    tweets = {}
    notifications = {}
    messages = {}
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

def save_data(users, tweets, notifications=None, messages=None):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)
    with open(TWEETS_FILE, 'w') as f:
        json.dump(tweets, f, indent=2)
    if notifications is not None:
        with open(NOTIFICATIONS_FILE, 'w') as f:
            json.dump(notifications, f, indent=2)
    if messages is not None:
        with open(MESSAGES_FILE, 'w') as f:
            json.dump(messages, f, indent=2)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_id():
    return str(uuid.uuid4())[:8]

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
    tweet.setdefault('parsed_content', parse_tweet_content(tweet.get('content', '')))
    tweet.setdefault('media', None)
    return tweet

def ensure_admin_account():
    admin_username = 'rytoobov'
    admin_email = 'rytoobov@admin.local'

    for uid, user in users.items():
        if user.get('username') == admin_username:
            user['is_admin'] = True
            if not user.get('email'):
                user['email'] = admin_email
            return False

    uid = generate_id()
    users[uid] = normalize_user_record(uid, {
        'id': uid,
        'username': admin_username,
        'email': admin_email,
        'display_name': 'Rytoobov',
        'bio': 'Admin account',
        'password_hash': hash_password('change-me-admin'),
        'created_at': datetime.now().isoformat(),
        'is_admin': True
    })
    return True

def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

def find_user_by_reset_token(token):
    for uid, user in users.items():
        if user.get('reset_token') != token:
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

    # Parse hashtags
    hashtag_pattern = r'#(\w+)'
    content = re.sub(hashtag_pattern, r'<a href="/hashtag/\1" class="hashtag">#\1</a>', content)
    
    # Parse mentions
    mention_pattern = r'@(\w+)'
    content = re.sub(mention_pattern, r'<a href="/user/\1" class="mention">@\1</a>', content)
    
    return content

def save_uploaded_media(file_storage):
    if not file_storage or not file_storage.filename:
        return None, None

    media_type = allowed_media_extension(file_storage.filename)
    if not media_type:
        return None, 'Only PNG, JPG, JPEG, GIF, WEBP, MP4, MOV, WEBM, and M4V files are supported'

    filename = secure_filename(file_storage.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    destination = UPLOAD_DIR / unique_name
    file_storage.save(destination)
    return {
        'type': media_type,
        'filename': unique_name,
        'url': url_for('static', filename=f'uploads/{unique_name}')
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
        'parsed_content': tweet.get('parsed_content', parse_tweet_content(tweet.get('content', '')))
    }

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
        media_path = UPLOAD_DIR / filename
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
            convo.append({
                **message,
                'is_from_current_user': message['sender_id'] == current_user_id
            })
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
if ensure_admin_account():
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
                         all_tweets=tweets)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form['email'].lower().strip()
        password = hash_password(request.form['password'])

        for uid, user in users.items():
            email_match = user.get('email', '').lower() == identifier
            username_match = user.get('username', '').lower() == identifier
            if (email_match or username_match) and user['password_hash'] == password:
                session['user_id'] = uid
                return redirect(url_for('index'))

        return render_template('login.html', error='Invalid email or password')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].lower().strip()
        email = request.form['email'].lower().strip()
        display_name = request.form['display_name']
        password = request.form['password']
        bio = request.form.get('bio', '')

        # Check if username exists
        for user in users.values():
            if user['username'] == username:
                return render_template('register.html', error='Username already taken')

        if not is_valid_email(email):
            return render_template('register.html', error='Please enter a valid email address')

        existing_email_id, _ = get_user_by_email(email)
        if existing_email_id:
            return render_template('register.html', error='Email already registered')

        if len(password) < 4:
            return render_template('register.html', error='Password must be at least 4 characters')

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

        if not is_valid_email(email):
            return render_template('forgot_password.html', error='Please enter a valid email address')

        uid, user = get_user_by_email(email)
        if not user:
            return render_template('forgot_password.html', error='No account was found with that email')

        token = secrets.token_urlsafe(32)
        users[uid]['reset_token'] = token
        users[uid]['reset_token_expires_at'] = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
        save_data(users, tweets, notifications, messages)

        reset_link = url_for('reset_password', token=token, _external=True)
        try:
            send_password_reset_email(email, reset_link)
        except Exception as exc:
            users[uid]['reset_token'] = None
            users[uid]['reset_token_expires_at'] = None
            save_data(users, tweets, notifications, messages)
            return render_template('forgot_password.html', error=str(exc))

        return render_template('forgot_password.html', success='We emailed you a password reset link.')

    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    uid, user = find_user_by_reset_token(token)
    if not user:
        return render_template('reset_password.html', error='This reset link is invalid or has expired.')

    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if len(new_password) < 4:
            return render_template('reset_password.html', token=token, error='Password must be at least 4 characters')

        if new_password != confirm_password:
            return render_template('reset_password.html', token=token, error='Passwords do not match')

        users[uid]['password_hash'] = hash_password(new_password)
        users[uid]['reset_token'] = None
        users[uid]['reset_token_expires_at'] = None
        save_data(users, tweets, notifications, messages)
        return render_template('reset_password.html', success='Password updated. You can log in now.')

    return render_template('reset_password.html', token=token)

@app.route('/logout')
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
    return redirect(request.referrer or url_for('index'))

@app.route('/quote/<tweet_id>', methods=['GET', 'POST'])
@login_required
def quote_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
    if not tweet:
        return redirect(url_for('index'))

    if request.method == 'POST':
        content = request.form['content'].strip()
        media, media_error = save_uploaded_media(request.files.get('media'))
        if media_error:
            return render_template('quote.html', tweet=tweet, current_user=users.get(session['user_id']), error=media_error)
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

    return render_template('quote.html', tweet=tweet, current_user=users.get(session['user_id']))

@app.route('/reply/<tweet_id>', methods=['POST'])
@login_required
def reply_tweet(tweet_id):

    content = request.form['content'].strip()
    media, media_error = save_uploaded_media(request.files.get('media'))
    if media_error:
        return redirect(request.referrer or url_for('index'))

    if len(content) > 280 or (not content and not media):
        return redirect(request.referrer or url_for('index'))
    
    original_tweet = tweets.get(tweet_id)
    if not original_tweet:
        return redirect(request.referrer or url_for('index'))
    
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
    return redirect(request.referrer or url_for('index'))

@app.route('/like/<tweet_id>', methods=['POST'])
@login_required
def like_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
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
    return redirect(request.referrer or url_for('index'))

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
    is_follower = current_id in target_user.get('followers', [])
    allow_view = (not target_user.get('is_private', False)) or (target_user['id'] == current_id) or is_follower

    if allow_view:
        for tid, tweet in sorted(tweets.items(), key=lambda x: x[1]['created_at'], reverse=True):
            if not is_visible_timeline_tweet(tweet):
                continue
            if tweet['user_id'] == target_user['id']:
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
                         all_tweets=tweets)

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
        if f'#{hashtag}' in tweet.get('content', '').lower():
            hashtag_tweets.append(build_tweet_view(tweet, session['user_id']))
    
    # Sort by creation date
    hashtag_tweets.sort(key=lambda x: x['created_at'], reverse=True)
    
    return render_template('hashtag.html', 
                         hashtag=hashtag,
                         tweets=hashtag_tweets[:50],  # Limit to 50 tweets
                         current_user=current_user,
                         users=users,
                         all_tweets=tweets)

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
            if query.lower() in tweet.get('content', '').lower():
                search_results['tweets'].append(build_tweet_view(tweet, session.get('user_id')))
        
        # Search users
        for uid, user in users.items():
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
        if f'#{hashtag}' in tweet.get('content', '').lower():
            tweet_user = users.get(tweet['user_id'], {})
            hashtag_tweets.append({
                **tweet,
                'username': tweet_user.get('username', 'unknown'),
                'display_name': tweet_user.get('display_name', 'Unknown'),
                'is_liked': session['user_id'] in tweet.get('likes', []),
                'like_count': len(tweet.get('likes', [])),
                'retweet_count': len(tweet.get('retweets', [])),
                'parsed_content': tweet.get('parsed_content', parse_tweet_content(tweet.get('content', '')))
            })
    
    # Sort by creation date
    hashtag_tweets.sort(key=lambda x: x['created_at'], reverse=True)
    
    return render_template('hashtag.html', 
                         hashtag=hashtag,
                         tweets=hashtag_tweets[:50],  # Limit to 50 tweets
                         current_user=current_user,
                         users=users,
                         all_tweets=tweets)
def search():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    query = request.args.get('q', '').strip()
    search_results = {'tweets': [], 'users': []}
    
    if query:
        # Search tweets
        for tid, tweet in tweets.items():
            if query.lower() in tweet['content'].lower():
                tweet_user = users.get(tweet['user_id'], {})
                search_results['tweets'].append({
                    **tweet,
                    'username': tweet_user.get('username', 'unknown'),
                    'display_name': tweet_user.get('display_name', 'Unknown'),
                    'is_liked': session.get('user_id') in tweet.get('likes', []),
                    'like_count': len(tweet.get('likes', [])),
                    'retweet_count': len(tweet.get('retweets', []))
                })
        
        # Search users
        for uid, user in users.items():
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

@app.route('/toggle_theme')
@login_required
def toggle_theme():
    current_theme = session.get('theme', 'dark')
    session['theme'] = 'light' if current_theme == 'dark' else 'dark'
    return redirect(request.referrer or url_for('index'))

@app.route('/pin/<tweet_id>', methods=['POST'])
@login_required
def pin_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
    if tweet and tweet['user_id'] == session['user_id']:
        users[session['user_id']]['pinned_tweet'] = tweet_id
        save_data(users, tweets, notifications, messages)
    return redirect(request.referrer or url_for('index'))

@app.route('/delete/<tweet_id>', methods=['POST'])
@login_required
def delete_tweet(tweet_id):
    tweet = tweets.get(tweet_id)
    current_user = users.get(session['user_id'], {})
    can_delete = tweet and (
        tweet['user_id'] == session['user_id'] or current_user.get('is_admin', False)
    )
    if can_delete:
        delete_tweet_tree(tweet_id)
        save_data(users, tweets, notifications, messages)
    return redirect(request.referrer or url_for('index'))

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
