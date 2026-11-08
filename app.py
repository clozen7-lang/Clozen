import os
import re
import sqlite3
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from flask_socketio import SocketIO, join_room, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "clozen.db"
UPLOAD_DIR = BASE / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("CLOZEN_SECRET", "dev-only-change-this-before-deploying")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
socketio = SocketIO(app, cors_allowed_origins=[])

ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            bio TEXT NOT NULL DEFAULT '',
            profile_image TEXT,
            cover_image TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            image_name TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            followed_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (follower_id, followed_id),
            CHECK (follower_id != followed_id)
        );
        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, post_id)
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            actor_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
            message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, post_id)
        );
        """)
        # Safe migration for databases created by the original CLOZEN starter.
        cols = {row["name"] for row in con.execute("PRAGMA table_info(users)")}
        if "profile_image" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN profile_image TEXT")
        if "cover_image" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN cover_image TEXT")
        message_cols = {row["name"] for row in con.execute("PRAGMA table_info(messages)")}
        if "is_read" not in message_cols:
            con.execute("ALTER TABLE messages ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0")

init_db()

def add_notification(recipient_id, actor_id, kind, message, post_id=None):
    if recipient_id and actor_id and recipient_id != actor_id:
        with db() as con:
            con.execute(
                "INSERT INTO notifications(recipient_id,actor_id,kind,post_id,message) VALUES(?,?,?,?,?)",
                (recipient_id, actor_id, kind, post_id, message)
            )

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def current_user():
    if "user_id" not in session:
        return None
    with db() as con:
        return con.execute("SELECT id, username, email, bio, profile_image FROM users WHERE id=?", (session["user_id"],)).fetchone()

@app.context_processor
def inject_user():
    user = current_user()
    if not user:
        return {"me": None, "notification_count": 0}
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM notifications WHERE recipient_id=? AND is_read=0", (user["id"],)).fetchone()[0]
        unread_messages = con.execute("SELECT COUNT(*) FROM messages WHERE receiver_id=? AND is_read=0", (user["id"],)).fetchone()[0]
    return {"me": user, "notification_count": count, "unread_messages": unread_messages}

def save_image(file):
    if not file or not file.filename:
        return None
    safe = secure_filename(file.filename)
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    if ext not in ALLOWED:
        raise ValueError("Only PNG, JPG, WEBP and GIF images are allowed.")
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(UPLOAD_DIR / name)
    return name

@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login"))
    with db() as con:
        posts = con.execute("""
            SELECT p.*, u.username, u.profile_image,
              (SELECT COUNT(*) FROM likes l WHERE l.post_id=p.id) AS like_count,
              EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=?) AS liked
            FROM posts p JOIN users u ON u.id=p.user_id
            ORDER BY p.created_at DESC, p.id DESC LIMIT 100
        """, (session["user_id"],)).fetchall()
        comments = {}
        for p in posts:
            comments[p["id"]] = con.execute("""
                SELECT c.body, u.username FROM comments c
                JOIN users u ON u.id=c.user_id
                WHERE c.post_id=? ORDER BY c.id ASC LIMIT 4
            """, (p["id"],)).fetchall()
    return render_template("feed.html", posts=posts, comments=comments)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", username):
            flash("Username: use 3–24 letters, numbers or underscores.")
        elif "@" not in email or len(email) > 120:
            flash("Enter a valid email address.")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.")
        else:
            try:
                with db() as con:
                    cur = con.execute(
                        "INSERT INTO users(username,email,password_hash) VALUES(?,?,?)",
                        (username, email, generate_password_hash(password))
                    )
                    session.clear()
                    session["user_id"] = cur.lastrowid
                return redirect(url_for("home"))
            except sqlite3.IntegrityError:
                flash("That username or email is already registered.")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE username=? OR email=?",
                               (identity, identity.lower())).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
        flash("Username/email or password is incorrect.")
    return render_template("auth.html", mode="login")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.post("/post")
@login_required
def create_post():
    body = request.form.get("body", "").strip()
    image = request.files.get("image")
    if not body and (not image or not image.filename):
        flash("Write something or choose an image.")
        return redirect(url_for("home"))
    if len(body) > 2000:
        flash("Post is too long (maximum 2,000 characters).")
        return redirect(url_for("home"))
    try:
        image_name = save_image(image)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("home"))
    with db() as con:
        con.execute("INSERT INTO posts(user_id,body,image_name) VALUES(?,?,?)",
                    (session["user_id"], body, image_name))
    return redirect(url_for("home"))

@app.post("/post/<int:post_id>/like")
@login_required
def toggle_like(post_id):
    with db() as con:
        if not con.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            abort(404)
        liked = con.execute("SELECT 1 FROM likes WHERE user_id=? AND post_id=?",
                            (session["user_id"], post_id)).fetchone()
        if liked:
            con.execute("DELETE FROM likes WHERE user_id=? AND post_id=?",
                        (session["user_id"], post_id))
        else:
            con.execute("INSERT INTO likes(user_id,post_id) VALUES(?,?)",
                        (session["user_id"], post_id))
            owner = con.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()[0]
            actor = con.execute("SELECT username FROM users WHERE id=?", (session["user_id"],)).fetchone()[0]
            add_notification(owner, session["user_id"], "like", f"{actor} liked your post", post_id)
    return redirect(url_for("home"))

@app.post("/post/<int:post_id>/comment")
@login_required
def add_comment(post_id):
    body = request.form.get("body", "").strip()
    if not body or len(body) > 500:
        flash("Comment must be 1–500 characters.")
        return redirect(url_for("home"))
    with db() as con:
        if not con.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            abort(404)
        con.execute("INSERT INTO comments(user_id,post_id,body) VALUES(?,?,?)",
                    (session["user_id"], post_id, body))
        owner = con.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()[0]
        actor = con.execute("SELECT username FROM users WHERE id=?", (session["user_id"],)).fetchone()[0]
        add_notification(owner, session["user_id"], "comment", f"{actor} commented on your post", post_id)
    return redirect(url_for("home"))

@app.route("/people")
@login_required
def people():
    q = request.args.get("q", "").strip()
    with db() as con:
        users = con.execute("""
            SELECT u.id,u.username,u.bio,u.profile_image,
            EXISTS(SELECT 1 FROM follows f WHERE f.follower_id=? AND f.followed_id=u.id) AS following
            FROM users u WHERE u.id != ? AND u.username LIKE ?
            ORDER BY u.username LIMIT 50
        """, (session["user_id"], session["user_id"], f"%{q}%")).fetchall()
    return render_template("people.html", users=users, q=q)

@app.post("/follow/<int:user_id>")
@login_required
def follow(user_id):
    if user_id == session["user_id"]:
        abort(400)
    with db() as con:
        if not con.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            abort(404)
        con.execute("INSERT OR IGNORE INTO follows(follower_id,followed_id) VALUES(?,?)",
                    (session["user_id"], user_id))
        actor = con.execute("SELECT username FROM users WHERE id=?", (session["user_id"],)).fetchone()[0]
        add_notification(user_id, session["user_id"], "follow", f"{actor} started following you")
    return redirect(request.referrer or url_for("people"))

@app.post("/unfollow/<int:user_id>")
@login_required
def unfollow(user_id):
    with db() as con:
        con.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?",
                    (session["user_id"], user_id))
    return redirect(request.referrer or url_for("people"))

@app.route("/notifications")
@login_required
def notifications():
    with db() as con:
        items = con.execute("""
            SELECT n.*, u.username AS actor_name, u.profile_image
            FROM notifications n LEFT JOIN users u ON u.id=n.actor_id
            WHERE n.recipient_id=? ORDER BY n.id DESC LIMIT 100
        """, (session["user_id"],)).fetchall()
        con.execute("UPDATE notifications SET is_read=1 WHERE recipient_id=?", (session["user_id"],))
        suggestions = con.execute("""
            SELECT u.id,u.username,u.bio,u.profile_image FROM users u
            WHERE u.id != ? AND u.id NOT IN
              (SELECT followed_id FROM follows WHERE follower_id=?)
            ORDER BY u.id DESC LIMIT 8
        """, (session["user_id"], session["user_id"])).fetchall()
    return render_template("notifications.html", notifications=items, suggestions=suggestions)

@app.post("/post/<int:post_id>/share")
@login_required
def share_post(post_id):
    with db() as con:
        post = con.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            abort(404)
        con.execute("INSERT OR IGNORE INTO shares(user_id,post_id) VALUES(?,?)", (session["user_id"], post_id))
        actor = con.execute("SELECT username FROM users WHERE id=?", (session["user_id"],)).fetchone()[0]
        add_notification(post["user_id"], session["user_id"], "share", f"{actor} shared your post", post_id)
    flash("Post shared to your CLOZEN activity.")
    return redirect(url_for("home"))

@app.route("/chat")
@login_required
def chat_inbox():
    with db() as con:
        conversations = con.execute("""
            SELECT u.id, u.username, u.profile_image, m.body AS last_message, m.created_at
            FROM users u
            JOIN messages m ON m.id = (
                SELECT m2.id FROM messages m2
                WHERE (m2.sender_id=? AND m2.receiver_id=u.id)
                   OR (m2.sender_id=u.id AND m2.receiver_id=?)
                ORDER BY m2.id DESC LIMIT 1
            )
            WHERE u.id != ?
            ORDER BY m.id DESC
        """, (session["user_id"], session["user_id"], session["user_id"])).fetchall()
    return render_template("chat.html", conversations=conversations)

@app.route("/messages/<int:user_id>", methods=["GET", "POST"])
@login_required
def messages(user_id):
    if user_id == session["user_id"]:
        abort(400)
    with db() as con:
        other = con.execute("SELECT id,username,profile_image FROM users WHERE id=?", (user_id,)).fetchone()
        if not other:
            abort(404)
        if request.method == "POST":
            body = request.form.get("body", "").strip()
            if body and len(body) <= 2000:
                con.execute("INSERT INTO messages(sender_id,receiver_id,body) VALUES(?,?,?)",
                            (session["user_id"], user_id, body))
                return redirect(url_for("messages", user_id=user_id))
            flash("Message must be 1–2,000 characters.")
        con.execute("UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=?", (user_id, session["user_id"]))
        history = con.execute("""
            SELECT m.*, u.username FROM messages m JOIN users u ON u.id=m.sender_id
            WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
            ORDER BY m.id ASC LIMIT 200
        """, (session["user_id"], user_id, user_id, session["user_id"])).fetchall()
    return render_template("messages.html", other=other, history=history)

@app.route("/profile/<username>")
@login_required
def profile(username):
    with db() as con:
        person = con.execute("SELECT id,username,bio,profile_image,cover_image FROM users WHERE username=?", (username,)).fetchone()
        if not person:
            abort(404)
        posts = con.execute("""
            SELECT p.*, (SELECT COUNT(*) FROM likes l WHERE l.post_id=p.id) AS like_count
            FROM posts p WHERE p.user_id=? ORDER BY p.id DESC
        """, (person["id"],)).fetchall()
        follower_count = con.execute("SELECT COUNT(*) FROM follows WHERE followed_id=?", (person["id"],)).fetchone()[0]
        following_count = con.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (person["id"],)).fetchone()[0]
        is_following = con.execute("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?",
                                   (session["user_id"], person["id"])).fetchone() is not None
    return render_template("profile.html", person=person, posts=posts,
                           follower_count=follower_count, following_count=following_count,
                           is_following=is_following, is_owner=person["id"] == session["user_id"])

@app.route("/settings/profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if request.method == "POST":
            bio = request.form.get("bio", "").strip()[:300]
            try:
                profile_image = save_image(request.files.get("profile_image")) or user["profile_image"]
                cover_image = save_image(request.files.get("cover_image")) or user["cover_image"]
            except ValueError as exc:
                flash(str(exc))
                return render_template("edit_profile.html", user=user)
            con.execute("UPDATE users SET bio=?,profile_image=?,cover_image=? WHERE id=?",
                        (bio, profile_image, cover_image, session["user_id"]))
            flash("Profile updated successfully.")
            return redirect(url_for("profile", username=user["username"]))
    return render_template("edit_profile.html", user=user)

# Socket.IO: authenticated user rooms are used for live chat and WebRTC call signalling.
@socketio.on("connect")
def socket_connect(auth=None):
    if "user_id" not in session:
        return False
    join_room(f"user_{session['user_id']}")

@socketio.on("chat_send")
def chat_send(data):
    if "user_id" not in session:
        return
    try:
        receiver_id = int(data.get("receiver_id"))
    except (TypeError, ValueError, AttributeError):
        return
    body = str(data.get("body", "")).strip() if isinstance(data, dict) else ""
    if not body or len(body) > 2000:
        return
    sender_id = session["user_id"]
    with db() as con:
        receiver = con.execute("SELECT username FROM users WHERE id=?", (receiver_id,)).fetchone()
        if not receiver or receiver_id == sender_id:
            return
        cur = con.execute("INSERT INTO messages(sender_id,receiver_id,body) VALUES(?,?,?)",
                          (sender_id, receiver_id, body))
        msg = con.execute("SELECT created_at FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
        sender = con.execute("SELECT username FROM users WHERE id=?", (sender_id,)).fetchone()
    add_notification(receiver_id, sender_id, "message", f"{sender['username']} sent you a message")
    payload = {"sender_id": sender_id, "sender": sender["username"], "body": body,
               "created_at": msg["created_at"]}
    emit("chat_new", payload, room=f"user_{receiver_id}")
    emit("chat_new", payload, room=f"user_{sender_id}")

@socketio.on("call_user")
def call_user(data):
    if "user_id" not in session or not isinstance(data, dict):
        return
    try:
        target = int(data.get("to"))
    except (TypeError, ValueError):
        return
    with db() as con:
        user = con.execute("SELECT username FROM users WHERE id=?", (session["user_id"],)).fetchone()
        exists = con.execute("SELECT 1 FROM users WHERE id=?", (target,)).fetchone()
    if exists and target != session["user_id"]:
        add_notification(target, session["user_id"], "call", f"{user['username']} is calling you")
        emit("incoming_call", {"from": session["user_id"], "name": user["username"],
             "call_type": data.get("call_type", "audio")}, room=f"user_{target}")

@socketio.on("call_response")
def call_response(data):
    if "user_id" not in session or not isinstance(data, dict):
        return
    try:
        target = int(data.get("to"))
    except (TypeError, ValueError):
        return
    emit("call_answer", {"from": session["user_id"], "accepted": bool(data.get("accepted")),
         "call_type": data.get("call_type", "audio")}, room=f"user_{target}")

@socketio.on("webrtc_signal")
def webrtc_signal(data):
    if "user_id" not in session or not isinstance(data, dict):
        return
    try:
        target = int(data.get("to"))
    except (TypeError, ValueError):
        return
    emit("webrtc_signal", {"from": session["user_id"], "signal": data.get("signal")},
         room=f"user_{target}")

@socketio.on("end_call")
def end_call(data):
    if "user_id" not in session or not isinstance(data, dict):
        return
    try:
        target = int(data.get("to"))
    except (TypeError, ValueError):
        return
    emit("call_ended", {"from": session["user_id"]}, room=f"user_{target}")

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False
    )
    