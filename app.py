from flask import Flask, render_template, request, redirect, url_for, session
import json
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'

# Data storage
DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / 'users.json'
TWEETS_FILE = DATA_DIR / 'tweets.json'
NOTIFICATIONS_FILE = DATA_DIR / 'notifications.json'
MESSAGES_FILE = DATA_DIR / 'messages.json'

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


def get_user_by_username(username):
    for uid, user in users.items():
        if user['username'] == username:
            return uid, user
    return None, None

def parse_tweet_content(content):
    """Parse hashtags and mentions in tweet content"""
    import re
    
    # Parse hashtags
    hashtag_pattern = r'#(\w+)'
    content = re.sub(hashtag_pattern, r'<a href="/hashtag/\1" class="hashtag">#\1</a>', content)
    
    # Parse mentions
    mention_pattern = r'@(\w+)'
    content = re.sub(mention_pattern, r'<a href="/user/\1" class="mention">@\1</a>', content)
    
    return content

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
        if tweet['user_id'] in blocked:
            continue
        tweet_user = users.get(tweet['user_id'], {})
        if session['user_id'] in tweet_user.get('blocked', []):
            continue
        timeline.append({
            **tweet,
            'username': tweet_user.get('username', 'unknown'),
            'display_name': tweet_user.get('display_name', 'Unknown'),
            'is_liked': session['user_id'] in tweet.get('likes', []),
            'like_count': len(tweet.get('likes', [])),
            'retweet_count': len(tweet.get('retweets', [])),
            'parsed_content': tweet.get('parsed_content', parse_tweet_content(tweet.get('content', '')))
        })
    
    return render_template('index.html', 
                         tweets=timeline[:20], 
                         current_user=current_user,
                         users=users,
                         all_tweets=tweets)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].lower().strip()
        password = hash_password(request.form['password'])
        
        for uid, user in users.items():
            if user['username'] == username and user['password_hash'] == password:
                session['user_id'] = uid
                return redirect(url_for('index'))
        
        return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].lower().strip()
        display_name = request.form['display_name']
        password = request.form['password']
        bio = request.form.get('bio', '')
        
        # Check if username exists
        for user in users.values():
            if user['username'] == username:
                return render_template('register.html', error='Username already taken')
        
        if len(password) < 4:
            return render_template('register.html', error='Password must be at least 4 characters')
        
        uid = generate_id()
        users[uid] = {
            'id': uid,
            'username': username,
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
            'thread_to': None
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
    if not content or len(content) > 280:
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
        'thread_to': None
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
        if not content or len(content) > 280:
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
            'thread_to': None
        }
        create_notification(tweet['user_id'], 'quote', session['user_id'], tweet_id)
        save_data(users, tweets, notifications, messages)
        return redirect(url_for('index'))

    return render_template('quote.html', tweet=tweet, current_user=users.get(session['user_id']))

@app.route('/reply/<tweet_id>', methods=['POST'])
@login_required
def reply_tweet(tweet_id):



    content = request.form['content'].strip()
    if not content or len(content) > 280:
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
        'reply_to': tweet_id
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
            if tweet['user_id'] == target_user['id']:
                user_tweets.append({
                **tweet,
                'username': target_user['username'],
                'display_name': target_user['display_name'],
                'is_liked': session.get('user_id') in tweet.get('likes', []),
                'like_count': len(tweet.get('likes', [])),
                'retweet_count': len(tweet.get('retweets', []))
            })
    
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

@app.route('/search', methods=['GET', 'POST'])
def search():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    query = request.args.get('q', '').strip()
    search_results = {'tweets': [], 'users': []}
    
    if query:
        # Search tweets
        for tid, tweet in tweets.items():
            if query.lower() in tweet.get('content', '').lower():
                tweet_user = users.get(tweet['user_id'], {})
                search_results['tweets'].append({
                    **tweet,
                    'username': tweet_user.get('username', 'unknown'),
                    'display_name': tweet_user.get('display_name', 'Unknown'),
                    'is_liked': session.get('user_id') in tweet.get('likes', []),
                    'like_count': len(tweet.get('likes', [])),
                    'retweet_count': len(tweet.get('retweets', [])),
                    'parsed_content': tweet.get('parsed_content', parse_tweet_content(tweet.get('content', '')))
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
    if tweet and tweet['user_id'] == session['user_id']:
        del tweets[tweet_id]
        save_data(users, tweets, notifications, messages)
    return redirect(request.referrer or url_for('index'))

@app.route('/messages')
@login_required
def messages_page():
    current_user = users.get(session['user_id'])
    user_messages = []
    
    # Get all messages where current user is sender or receiver
    for mid, message in messages.items():
        if message['sender_id'] == session['user_id'] or message['receiver_id'] == session['user_id']:
            user_messages.append(message)
    
    # Sort by created_at descending
    user_messages.sort(key=lambda x: x['created_at'], reverse=True)
    
    return render_template('messages.html', 
                         messages=user_messages[:50],  # Limit to 50
                         current_user=current_user,
                         users=users)

@app.route('/send_message/<username>', methods=['GET', 'POST'])
@login_required
def send_message(username):
    uid, target_user = get_user_by_username(username)
    if not target_user or uid == session['user_id']:
        return redirect(url_for('index'))
    
    current_user = users.get(session['user_id'])
    
    # Check if blocked
    if session['user_id'] in target_user.get('blocked', []) or uid in current_user.get('blocked', []):
        return redirect(url_for('index'))
    
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
            return redirect(url_for('messages_page'))
    
    return render_template('send_message.html', 
                         target_user=target_user,
                         current_user=current_user)

@app.route('/mark_message_read/<message_id>', methods=['POST'])
@login_required
def mark_message_read(message_id):
    message = messages.get(message_id)
    if message and message['receiver_id'] == session['user_id']:
        message['read'] = True
        save_data(users, tweets, notifications, messages)
    return redirect(url_for('messages_page'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)