import os
import secrets
import sqlite3
import base64
import io
import csv
import random
import string
import ipaddress
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, flash, Response
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config as cfg

BASE_DIR = cfg.BASE_DIR
DATA_DIR = cfg.DATA_DIR
DATABASE_PATH = cfg.DATABASE_PATH
FACE_DATABASE_PATH = cfg.FACE_DATABASE_PATH
CASCADE_PATH = cfg.CASCADE_PATH

SECRET_KEY_ENV_VAR = cfg.SECRET_KEY_ENV_VAR
_secret_key = os.environ.get(SECRET_KEY_ENV_VAR)
if not _secret_key:
    # No key was provided via the environment: generate one for this run only.
    # This keeps the app usable for local/dev testing, but sessions will NOT
    # survive a restart, and this path must never be relied on in production —
    # always set FLASK_SECRET_KEY explicitly when deploying.
    _secret_key = secrets.token_hex(32)
    print(
        f'[warning] {SECRET_KEY_ENV_VAR} not set — using a temporary, random '
        'secret key for this process only. Set the environment variable for '
        'persistent sessions and before deploying.'
    )

app = Flask(__name__)
app.secret_key = _secret_key


def configure_proxy_fix(flask_app):
    """Wraps flask_app.wsgi_app in werkzeug's ProxyFix when the app is
    configured to run behind a reverse proxy (nginx, a cloud load balancer,
    etc.), so request.remote_addr reflects the real client IP instead of
    the proxy's own address.

    This matters beyond logging: per-IP rate limiting (Flask-Limiter),
    account-lockout-by-IP, the attendance IP allowlist
    (ATTENDANCE_ALLOWED_NETWORKS), and the audit log all read
    request.remote_addr directly. Left unconfigured behind a proxy, every
    request appears to come from the proxy, which silently defeats all
    four. Off by default so a direct (no-proxy) deployment isn't tricked
    into trusting a spoofable X-Forwarded-For header from an untrusted
    client. See README's "Deploying behind a reverse proxy" section.

    Returns flask_app for convenience/chaining; also easily unit-testable
    on its own Flask instance without needing to reimport this module.
    """
    if not cfg.BEHIND_REVERSE_PROXY:
        return flask_app
    flask_app.wsgi_app = ProxyFix(
        flask_app.wsgi_app,
        x_for=cfg.PROXY_FIX_NUM_PROXIES,
        x_proto=cfg.PROXY_FIX_NUM_PROXIES,
        x_host=cfg.PROXY_FIX_NUM_PROXIES,
        x_port=cfg.PROXY_FIX_NUM_PROXIES,
    )
    return flask_app


configure_proxy_fix(app)

# Login sessions expire after a period of inactivity instead of lasting
# indefinitely. session.permanent is set to True at each successful login
# (see login() and student_login()) so this actually applies.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=cfg.SESSION_LIFETIME_MINUTES)

# Reject any request body larger than this outright (protects against a
# basic oversized-payload resource-exhaustion attempt). Registration and
# attendance-marking add their own tighter, more specific checks on top.
app.config['MAX_CONTENT_LENGTH'] = cfg.MAX_CONTENT_LENGTH_MB * 1024 * 1024
# Exposed so templates can do {% if config.CAPTCHA_ENABLED %} without a
# separate context processor.
app.config['CAPTCHA_ENABLED'] = cfg.CAPTCHA_ENABLED

# CSRF protection for every state-changing (non-GET) request. Forms get a
# hidden csrf_token field; JS fetch() calls send it via the X-CSRFToken
# header (see the csrfHeaders() helper in static/app.js).
csrf = CSRFProtect(app)

# Per-IP rate limiting on top of (not instead of) the per-account lockout
# below: rate limiting throttles a single IP regardless of which account
# it's hitting; lockout protects one account even from a distributed
# attempt. Defaults to in-memory storage — fine for a single-process
# deployment; set RATE_LIMIT_STORAGE_URI for a shared backend (e.g. Redis)
# if running multiple processes.
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=cfg.RATE_LIMIT_STORAGE_URI,
    enabled=cfg.RATE_LIMIT_ENABLED,
    default_limits=[],
)


@app.errorhandler(413)
def handle_request_too_large(e):
    return jsonify({'status': 'error', 'message': 'Request too large.'}), 413


@app.errorhandler(429)
def handle_rate_limited(e):
    return jsonify({'status': 'error', 'message': 'Too many requests. Please wait and try again.'}), 429


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_databases():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(cfg.MODELS_DIR, exist_ok=True)
    os.makedirs(cfg.DATABASE_DIR, exist_ok=True)

    if not os.path.exists(CASCADE_PATH):
        if hasattr(cv2, 'data'):
            src = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
            if os.path.exists(src):
                with open(src, 'rb') as src_file:
                    with open(CASCADE_PATH, 'wb') as dst_file:
                        dst_file.write(src_file.read())

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY,
        name TEXT,
        roll_no TEXT,
        branch TEXT,
        semester TEXT,
        password TEXT,
        photo_count INTEGER DEFAULT 0
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS subjects(
        id INTEGER PRIMARY KEY,
        name TEXT,
        code TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY,
        subject_id INTEGER,
        title TEXT,
        date TEXT,
        time TEXT,
        end_date TEXT,
        end_time TEXT,
        is_recurring INTEGER DEFAULT 0,
        active INTEGER DEFAULT 0,
        FOREIGN KEY(subject_id) REFERENCES subjects(id)
    )''')
    
    # Migration for sessions table
    cursor.execute("PRAGMA table_info(sessions)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'end_date' not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN end_date TEXT")
    if 'end_time' not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN end_time TEXT")
    if 'is_recurring' not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN is_recurring INTEGER DEFAULT 0")

    # Migration: brute-force lockout tracking for admins and students.
    # failed_attempts resets to 0 on a successful login; locked_until (an
    # ISO timestamp) blocks further attempts until it's in the past.
    for table in ('admins', 'students'):
        cursor.execute(f"PRAGMA table_info({table})")
        table_columns = [column[1] for column in cursor.fetchall()]
        if 'failed_attempts' not in table_columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN failed_attempts INTEGER DEFAULT 0")
        if 'locked_until' not in table_columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN locked_until TEXT")

    # Audit log: who did what, when, from where. Covers admin-initiated
    # changes (student/session deletion, overrides, admin account
    # management) and login outcomes for both admins and students.
    cursor.execute('''CREATE TABLE IF NOT EXISTS audit_log(
        id INTEGER PRIMARY KEY,
        timestamp TEXT NOT NULL,
        actor_type TEXT NOT NULL,
        actor_name TEXT,
        action TEXT NOT NULL,
        target TEXT,
        details TEXT,
        ip_address TEXT
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp)')

    cursor.execute('''CREATE TABLE IF NOT EXISTS attendance(
        id INTEGER PRIMARY KEY,
        student_id INTEGER,
        session_id INTEGER,
        status TEXT,
        timestamp TEXT,
        note TEXT,
        FOREIGN KEY(student_id) REFERENCES students(id),
        FOREIGN KEY(session_id) REFERENCES sessions(id)
    )''')
    # One row per registered face image. Unlike the old LBPH approach
    # (which needed a full model retrain over the whole dataset on every
    # add/delete), this is just an incremental gallery: registering a
    # student inserts rows here, deleting one removes them — no retraining
    # step exists at all anymore.
    cursor.execute('''CREATE TABLE IF NOT EXISTS face_embeddings(
        id INTEGER PRIMARY KEY,
        student_id INTEGER NOT NULL,
        embedding BLOB NOT NULL,
        created_at TEXT,
        FOREIGN KEY(student_id) REFERENCES students(id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_face_embeddings_student_id ON face_embeddings(student_id)')
    cursor.execute('SELECT 1 FROM admins LIMIT 1')
    if cursor.fetchone() is None:
        default_admin_password = cfg.DEFAULT_ADMIN_PASSWORD
        cursor.execute('INSERT INTO admins(username, password) VALUES(?, ?)',
                        ('admin', generate_password_hash(default_admin_password)))
    conn.commit()
    conn.close()

    if not os.path.exists(FACE_DATABASE_PATH):
        face_conn = sqlite3.connect(FACE_DATABASE_PATH)
        face_cursor = face_conn.cursor()
        face_cursor.execute('''CREATE TABLE IF NOT EXISTS people(
            id INTEGER PRIMARY KEY,
            name TEXT,
            gender TEXT,
            section TEXT
        )''')
        face_conn.commit()
        face_conn.close()


def query_db(query, args=(), one=False):
    conn = get_db_connection()
    cursor = conn.execute(query, args)
    rows = cursor.fetchall()
    conn.close()
    return rows[0] if one and rows else rows


def execute_db(query, args=()):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, args)
    conn.commit()
    lastrowid = cursor.lastrowid
    conn.close()
    return lastrowid


def get_pagination(total_count, per_page, page_param='page'):
    """
    Reads the current page number from the query string, clamps it to a
    valid range given total_count/per_page, and returns a dict with
    everything a template needs to render page controls:
    {page, per_page, total_pages, total_count, offset, has_prev, has_next}.
    Page numbers are 1-indexed.
    """
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    try:
        page = int(request.args.get(page_param, 1))
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(page, total_pages))
    return {
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'total_count': total_count,
        'offset': (page - 1) * per_page,
        'has_prev': page > 1,
        'has_next': page < total_pages,
    }


def log_audit(actor_type, actor_name, action, target=None, details=None):
    """Records one audit-log entry. actor_type is 'admin', 'student', or
    'anonymous'; action is a short machine-readable label like
    'admin_login_failed' or 'student_deleted'; target and details are
    free-form human-readable strings."""
    try:
        ip_address = request.remote_addr
    except RuntimeError:
        ip_address = None  # called outside a request context (shouldn't happen, but don't crash)
    execute_db(
        'INSERT INTO audit_log(timestamp, actor_type, actor_name, action, target, details, ip_address) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (datetime.now().isoformat(), actor_type, actor_name, action, target, details, ip_address)
    )


def _is_locked_out(row):
    """row is an admins/students sqlite3.Row. Returns True if this account
    is currently within its lockout window."""
    locked_until = row['locked_until']
    if not locked_until:
        return False
    try:
        return datetime.fromisoformat(locked_until) > datetime.now()
    except ValueError:
        return False


def _register_failed_login(table, row_id):
    """Increments the failed-attempt counter for one account and sets
    locked_until once it crosses the configured threshold."""
    conn = get_db_connection()
    row = conn.execute(f'SELECT failed_attempts FROM {table} WHERE id=?', (row_id,)).fetchone()
    attempts = (row['failed_attempts'] or 0) + 1 if row else 1
    locked_until = None
    if attempts >= cfg.LOCKOUT_MAX_FAILED_ATTEMPTS:
        locked_until = (datetime.now() + timedelta(minutes=cfg.LOCKOUT_DURATION_MINUTES)).isoformat()
    conn.execute(f'UPDATE {table} SET failed_attempts=?, locked_until=? WHERE id=?', (attempts, locked_until, row_id))
    conn.commit()
    conn.close()
    return attempts, locked_until


def _clear_failed_logins(table, row_id):
    execute_db(f'UPDATE {table} SET failed_attempts=0, locked_until=NULL WHERE id=?', (row_id,))


def generate_captcha_text():
    alphabet = string.ascii_uppercase + string.digits
    # Excludes visually ambiguous characters (0/O, 1/I) to keep a
    # legitimate user's success rate reasonable.
    alphabet = ''.join(c for c in alphabet if c not in 'O0I1')
    return ''.join(random.choice(alphabet) for _ in range(cfg.CAPTCHA_LENGTH))


def render_captcha_image(text):
    """Renders `text` as a distorted-enough PNG to block naive scripted
    login attempts, without depending on a third-party CAPTCHA service."""
    width, height = 160, 60
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Background noise lines
    for _ in range(6):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=(random.randint(150, 200),) * 3, width=1)

    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 32)
    except OSError:
        font = ImageFont.load_default()

    for i, char in enumerate(text):
        x = 12 + i * (width - 24) // len(text) + random.randint(-3, 3)
        y = random.randint(8, 18)
        angle = random.randint(-25, 25)
        char_img = Image.new('RGBA', (40, 40), (255, 255, 255, 0))
        char_draw = ImageDraw.Draw(char_img)
        color = (random.randint(0, 90), random.randint(0, 90), random.randint(0, 90))
        char_draw.text((5, 0), char, font=font, fill=color)
        char_img = char_img.rotate(angle, expand=True)
        image.paste(char_img, (x, y), char_img)

    # Noise dots
    for _ in range(80):
        x, y = random.randint(0, width - 1), random.randint(0, height - 1)
        draw.point((x, y), fill=(random.randint(120, 180),) * 3)

    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return buf.getvalue()


def validate_password_strength(password):
    """Returns None if `password` meets the configured policy, or an
    error message string explaining what's missing."""
    if not password or len(password) < cfg.PASSWORD_MIN_LENGTH:
        return f'Password must be at least {cfg.PASSWORD_MIN_LENGTH} characters long.'
    if cfg.PASSWORD_REQUIRE_LETTER_AND_DIGIT:
        has_letter = any(c.isalpha() for c in password)
        has_digit = any(c.isdigit() for c in password)
        if not (has_letter and has_digit):
            return 'Password must contain at least one letter and one number.'
    return None


_embedder_net = None
_eye_cascade = None


def get_eye_cascade():
    """Loads (and caches) the bundled Haar eye cascade, used for face
    alignment and blink-based liveness."""
    global _eye_cascade
    if _eye_cascade is None:
        _eye_cascade = cv2.CascadeClassifier(cfg.EYE_CASCADE_PATH)
    return _eye_cascade


def get_embedder():
    """
    Loads (and caches) the pretrained OpenFace CNN used to turn a face crop
    into a 128-d embedding vector. See models/README.md for what this
    model is and where it came from.
    """
    global _embedder_net
    if _embedder_net is None:
        if not os.path.exists(cfg.EMBEDDER_MODEL_PATH):
            raise RuntimeError(
                f'Face embedding model not found at {cfg.EMBEDDER_MODEL_PATH}. '
                'See models/README.md.'
            )
        _embedder_net = cv2.dnn.readNetFromTorch(cfg.EMBEDDER_MODEL_PATH)
    return _embedder_net


def compute_embedding(face_rgb):
    """
    face_rgb: an RGB numpy array crop of a single detected face (any size —
    it's resized internally to what the model expects).
    Returns a 128-d, L2-normalized float32 embedding vector. Two
    embeddings from the same person land close together in this space
    (high cosine similarity); different people land far apart.
    """
    embedder = get_embedder()
    size = cfg.EMBEDDING_INPUT_SIZE
    # face_rgb is already RGB (this app converts to RGB early throughout),
    # so no channel swap needed here.
    blob = cv2.dnn.blobFromImage(face_rgb, 1.0 / 255, (size, size), (0, 0, 0), swapRB=False, crop=False)
    embedder.setInput(blob)
    vec = embedder.forward()
    return vec.flatten().astype(np.float32)


def cosine_similarity(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def store_embedding(student_id, embedding):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        'INSERT INTO face_embeddings(student_id, embedding, created_at) VALUES (?, ?, ?)',
        (student_id, embedding.astype(np.float32).tobytes(), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def delete_student_embeddings(student_id):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute('DELETE FROM face_embeddings WHERE student_id=?', (student_id,))
    conn.commit()
    conn.close()


def get_all_embeddings():
    """Every stored (student_id, embedding_ndarray) pair, across all students."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT student_id, embedding FROM face_embeddings').fetchall()
    conn.close()
    return [(row['student_id'], np.frombuffer(row['embedding'], dtype=np.float32)) for row in rows]


def get_student_embeddings(student_id):
    """All stored embeddings belonging to one specific student."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT embedding FROM face_embeddings WHERE student_id=?', (student_id,)).fetchall()
    conn.close()
    return [np.frombuffer(row['embedding'], dtype=np.float32) for row in rows]


def find_best_match(embedding, gallery=None):
    """
    1:N identification: compares `embedding` against every (student_id,
    embedding) pair in `gallery` (defaults to the full stored gallery) and
    returns (best_student_id, best_similarity) — the single closest match
    — or (None, 0.0) if the gallery is empty. This does NOT apply any
    threshold itself; callers decide what similarity counts as a match.
    """
    if gallery is None:
        gallery = get_all_embeddings()
    best_id, best_sim = None, -1.0
    for student_id, stored_embedding in gallery:
        sim = cosine_similarity(embedding, stored_embedding)
        if sim > best_sim:
            best_sim = sim
            best_id = student_id
    if best_id is None:
        return None, 0.0
    return int(best_id), best_sim


def detect_faces_multiscale(face_cascade, gray, detect_params):
    """Thin wrapper around detectMultiScale, returning a plain list (not
    whatever array-like type OpenCV returns) sorted largest-first."""
    faces = face_cascade.detectMultiScale(gray, **detect_params)
    return sorted(list(faces), key=lambda f: f[2] * f[3], reverse=True)


def detect_largest_face(face_cascade, gray, detect_params):
    """Runs Haar cascade detection and returns the (x, y, w, h) box of the
    largest detected face, or None if no face was found."""
    faces = detect_faces_multiscale(face_cascade, gray, detect_params)
    if len(faces) == 0:
        return None
    return faces[0]


def detect_single_face(face_cascade, gray, detect_params):
    """Like detect_largest_face, but returns a (box, error_message) pair.
    error_message is set (and box is None) if no face was found, or if
    more than one face was found and cfg.REJECT_MULTIPLE_FACES is on —
    rather than silently proceeding with whichever face happens to be
    largest."""
    faces = detect_faces_multiscale(face_cascade, gray, detect_params)
    if len(faces) == 0:
        return None, 'No clear face detected! Please look directly at the camera in a well-lit area.'
    if len(faces) > 1 and cfg.REJECT_MULTIPLE_FACES:
        return None, 'Multiple faces detected in frame. Please make sure only you are visible to the camera.'
    return faces[0], None


def align_face_crop(gray, rgb, face_box, eye_cascade):
    """
    Improves embedding quality by leveling the face crop before it's fed
    to the embedder: detects both eyes within the face box (using the
    bundled Haar eye cascade), rotates the source image so the eye line
    is horizontal, then re-crops the face region from the rotated image.

    Falls back to the plain (unrotated) crop if eyes can't be reliably
    found — this is a best-effort improvement, not a hard requirement.

    A modern learned detector (e.g. YuNet) would give more reliable
    5-point landmarks for this than a Haar eye cascade does — that model's
    weights weren't reachable from this project's build environment (see
    models/README.md) — so this is the practical alternative: still Haar
    cascade for detection, but now with an alignment pass before
    embedding, which is the main accuracy benefit alignment provides.
    """
    x, y, w, h = face_box
    plain_crop = rgb[y:y + h, x:x + w]

    face_gray = gray[y:y + h, x:x + w]
    eyes = eye_cascade.detectMultiScale(face_gray, scaleFactor=1.1, minNeighbors=5, minSize=(15, 15))
    if len(eyes) < 2:
        return plain_crop

    # Take the two largest eye detections, then order them left/right by
    # x position (within the face crop).
    eyes = sorted(eyes, key=lambda e: e[2] * e[3], reverse=True)[:2]
    eyes = sorted(eyes, key=lambda e: e[0])
    (ex1, ey1, ew1, eh1), (ex2, ey2, ew2, eh2) = eyes
    left_eye_center = (x + ex1 + ew1 / 2, y + ey1 + eh1 / 2)
    right_eye_center = (x + ex2 + ew2 / 2, y + ey2 + eh2 / 2)

    dy = right_eye_center[1] - left_eye_center[1]
    dx = right_eye_center[0] - left_eye_center[0]
    if dx == 0:
        return plain_crop
    angle = np.degrees(np.arctan2(dy, dx))

    # Sanity bound: a legitimate near-frontal face shouldn't need a huge
    # rotation to level its eyes. A large angle usually means the eye
    # cascade found something spurious, not real eyes — fall back rather
    # than apply a distorting rotation.
    if abs(angle) > 30:
        return plain_crop

    face_center = (x + w / 2, y + h / 2)
    rotation_matrix = cv2.getRotationMatrix2D(face_center, angle, 1.0)
    rotated = cv2.warpAffine(rgb, rotation_matrix, (rgb.shape[1], rgb.shape[0]))

    aligned_crop = rotated[y:y + h, x:x + w]
    if aligned_crop.size == 0:
        return plain_crop
    return aligned_crop


def count_visible_eyes(gray, face_box, eye_cascade):
    x, y, w, h = face_box
    face_gray = gray[y:y + h, x:x + w]
    eyes = eye_cascade.detectMultiScale(face_gray, scaleFactor=1.1, minNeighbors=5, minSize=(15, 15))
    return len(eyes)


def check_blink_transition(frames_gray, face_boxes, eye_cascade):
    """
    Basic blink-based liveness signal: looks across a burst of frames for
    an eyes-visible -> eyes-not-visible -> eyes-visible pattern (or at
    minimum a clear visible/not-visible transition), which a static photo
    or a screen displaying a single frozen frame can't produce.

    frames_gray and face_boxes are parallel lists — one grayscale frame
    and that frame's already-detected face box, in capture order.

    This is a heuristic, not exact blink detection: Haar eye-cascade
    detection is noisy (glasses, angle, and lighting all affect it), so
    this looks for *any* clear transition rather than a strict
    open-closed-open triple, to avoid over-rejecting genuine attempts.
    """
    eye_counts = [count_visible_eyes(g, box, eye_cascade) for g, box in zip(frames_gray, face_boxes)]
    saw_eyes_visible = any(c >= 2 for c in eye_counts)
    saw_eyes_hidden = any(c == 0 for c in eye_counts)
    return saw_eyes_visible and saw_eyes_hidden


def assess_image_quality(gray_crop):
    """Returns a list of quality issues (empty list = no issues) found in
    a face crop, used to reject blurry/too-dark/too-bright registration
    photos before they're turned into an embedding."""
    if not cfg.IMAGE_QUALITY_CHECK_ENABLED:
        return []
    issues = []
    laplacian_variance = cv2.Laplacian(gray_crop, cv2.CV_64F).var()
    if laplacian_variance < cfg.IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE:
        issues.append('blurry')
    brightness = float(np.mean(gray_crop))
    if brightness < cfg.IMAGE_QUALITY_MIN_BRIGHTNESS:
        issues.append('too dark')
    elif brightness > cfg.IMAGE_QUALITY_MAX_BRIGHTNESS:
        issues.append('too bright/overexposed')
    return issues


def is_ip_allowed_for_attendance(ip_address):
    """True if attendance-marking is permitted from this IP. No
    restriction configured (the default) always returns True."""
    if not cfg.ATTENDANCE_ALLOWED_NETWORKS:
        return True
    if not ip_address:
        return False
    try:
        addr = ipaddress.ip_address(ip_address)
    except ValueError:
        return False
    for network in cfg.ATTENDANCE_ALLOWED_NETWORKS:
        try:
            if '/' in network:
                if addr in ipaddress.ip_network(network, strict=False):
                    return True
            elif addr == ipaddress.ip_address(network):
                return True
        except ValueError:
            continue  # skip a malformed entry rather than failing every request
    return False


def _parse_session_date(date_str):
    """Sessions store dates as dd-mm-yyyy (see admin_sessions()). Returns
    a date object, or None if unparseable."""
    try:
        return datetime.strptime(date_str, '%d-%m-%Y').date()
    except (ValueError, TypeError):
        return None


def find_conflicting_session(date, end_date, time, end_time, exclude_session_id=None):
    """
    Returns the first existing session whose date+time window overlaps
    the given candidate window, or None if there's no conflict. A
    student can't physically attend two overlapping sessions, so this
    checks across ALL subjects, not just the same one.

    Dates are dd-mm-yyyy strings, times are HH:MM 24h strings (both as
    stored in the sessions table).
    """
    candidate_start_date = _parse_session_date(date)
    candidate_end_date = _parse_session_date(end_date) or candidate_start_date
    if candidate_start_date is None:
        return None  # can't evaluate, don't block on a data problem

    existing_sessions = query_db('SELECT * FROM sessions')
    for existing in existing_sessions:
        if exclude_session_id is not None and existing['id'] == exclude_session_id:
            continue
        existing_start = _parse_session_date(existing['date'])
        existing_end = _parse_session_date(existing['end_date']) or existing_start
        if existing_start is None:
            continue

        dates_overlap = candidate_start_date <= existing_end and existing_start <= candidate_end_date
        if not dates_overlap:
            continue

        # HH:MM strings compare correctly lexically.
        times_overlap = time < (existing['end_time'] or '23:59') and (existing['time'] or '00:00') < end_time
        if times_overlap:
            return existing
    return None


def get_face_profile(student_id):
    conn = sqlite3.connect(FACE_DATABASE_PATH)
    cursor = conn.execute('SELECT id, name, gender, section FROM people WHERE id=?', (student_id,))
    profile = cursor.fetchone()
    conn.close()
    return profile


def save_face_profile(student_id, name, gender, section):
    conn = sqlite3.connect(FACE_DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM people WHERE id=?', (student_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO people(id, name, gender, section) VALUES(?, ?, ?, ?)', (student_id, name, gender, section))
    else:
        cursor.execute('UPDATE people SET name=?, gender=?, section=? WHERE id=?', (name, gender, section, student_id))
    conn.commit()
    conn.close()


def parse_base64_image(data):
    if ',' in data:
        data = data.split(',', 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data)))


def save_face_images(student_id, images):
    """
    For each captured registration image: detect the face, check its
    quality, save a color crop to disk (for provenance / so embeddings
    can be recomputed later — see migrate_embeddings.py), align it using
    detected eye positions, and immediately compute + store its
    embedding. There is no separate "training" step: adding a student's
    face is just inserting embedding rows, so it's O(1) per image rather
    than reprocessing the whole dataset.

    Returns (saved_count, skip_reasons) — skip_reasons is a list of short
    strings, one per image that didn't get stored, explaining why (e.g.
    'blurry', 'multiple faces detected'), so the caller can give the
    person useful feedback instead of a silent partial failure.
    """
    existing_images = [name for name in os.listdir(DATA_DIR) if name.startswith(f'User.{student_id}.')]
    count = len(existing_images)
    saved = 0
    skip_reasons = []
    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
    eye_cascade = get_eye_cascade()
    for image_data in images:
        image = parse_base64_image(image_data)
        frame = np.array(image.convert('RGB'))
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

        box, error = detect_single_face(face_cascade, gray, cfg.REGISTRATION_DETECT_PARAMS)
        if box is None:
            skip_reasons.append('multiple faces detected' if error and 'Multiple' in error else 'no face detected')
            continue

        (x, y, w, h) = box
        face_crop_gray = gray[y:y+h, x:x+w]
        quality_issues = assess_image_quality(face_crop_gray)
        if quality_issues:
            skip_reasons.append(', '.join(quality_issues))
            continue

        face_crop_rgb = align_face_crop(gray, frame, box, eye_cascade)

        face_img = Image.fromarray(face_crop_rgb)
        count += 1
        filename = f'User.{student_id}.{count}.jpg'
        face_img.save(os.path.join(DATA_DIR, filename))

        embedding = compute_embedding(face_crop_rgb)
        store_embedding(student_id, embedding)
        saved += 1
    return saved, skip_reasons


def _face_crop_pair_has_motion(gray1, box1, gray2, box2):
    """Shared by check_liveness and check_liveness_sequence: True if two
    face crops (each from its own frame + detected box) differ enough to
    suggest genuine motion between frames, rather than a static image."""
    (x1, y1, w1, h1) = box1
    (x2, y2, w2, h2) = box2
    crop1 = gray1[y1:y1 + h1, x1:x1 + w1]
    crop2 = gray2[y2:y2 + h2, x2:x2 + w2]
    size = (100, 100)
    crop1 = cv2.resize(crop1, size)
    crop2 = cv2.resize(crop2, size)
    mean_diff = float(np.mean(cv2.absdiff(crop1, crop2)))
    return mean_diff >= cfg.LIVENESS_MIN_MEAN_PIXEL_DIFF


def check_liveness(face_cascade, gray1, gray2, detect_params):
    """
    Basic motion-based liveness check (see config.LIVENESS_CHECK_ENABLED for
    the full explanation). Detects a face in each of two frames captured a
    short moment apart and requires enough pixel-level difference between
    the two face crops to suggest a live, moving subject rather than a
    static photo or screen held in front of the camera.

    Returns True if the pair looks "live" (or if the check is disabled),
    False if it looks static. Also returns False if a face can't be found
    in both frames, since that's already handled as a detection failure by
    the caller.
    """
    if not cfg.LIVENESS_CHECK_ENABLED:
        return True

    faces1 = face_cascade.detectMultiScale(gray1, **detect_params)
    faces2 = face_cascade.detectMultiScale(gray2, **detect_params)
    if len(faces1) == 0 or len(faces2) == 0:
        return False

    faces1 = sorted(faces1, key=lambda f: f[2] * f[3], reverse=True)
    faces2 = sorted(faces2, key=lambda f: f[2] * f[3], reverse=True)
    return _face_crop_pair_has_motion(gray1, faces1[0], gray2, faces2[0])


def check_liveness_sequence(face_cascade, eye_cascade, frames_gray, detect_params):
    """
    Extended liveness check across a burst of frames (cfg.LIVENESS_FRAME_COUNT
    of them), combining two independent signals:

      1. Motion: at least half of consecutive frame pairs must show
         pixel-level change in the face region (same idea as
         check_liveness, just applied across more than one pair).
      2. Blink (if cfg.LIVENESS_REQUIRE_BLINK): the frame burst must show
         a detectable eyes-visible / eyes-not-visible transition.

    A video replay of the real person has genuine motion and would still
    likely pass signal 1 alone — that's the known limitation of motion-only
    liveness. Requiring a blink too raises the bar (a replay needs to
    contain a blink at the right moment), though it's still not
    production-grade anti-spoofing against a sufficiently prepared replay.

    Returns (is_live: bool, face_boxes: list) — face_boxes is the detected
    box per frame (None for frames with no detected face), so the caller
    can reuse them instead of re-running detection.
    """
    face_boxes = [detect_largest_face(face_cascade, g, detect_params) for g in frames_gray]
    if any(box is None for box in face_boxes):
        return False, face_boxes

    if cfg.LIVENESS_CHECK_ENABLED:
        motion_votes = sum(
            _face_crop_pair_has_motion(frames_gray[i], face_boxes[i], frames_gray[i + 1], face_boxes[i + 1])
            for i in range(len(frames_gray) - 1)
        )
        if motion_votes < (len(frames_gray) - 1) / 2:
            return False, face_boxes

    if cfg.LIVENESS_REQUIRE_BLINK:
        if not check_blink_transition(frames_gray, face_boxes, eye_cascade):
            return False, face_boxes

    return True, face_boxes


def recognize_face(image_data):
    """1:N identification: match the single face in `image_data` against
    the full embedding gallery. Returns (student_id, similarity, error) —
    student_id/similarity are None with error set if no face (or, when
    cfg.REJECT_MULTIPLE_FACES is on, more than one face) was found;
    otherwise error is None and student_id is set only if the best match
    clears cfg.RECOGNIZE_MATCH_THRESHOLD (student_id may still be None
    with no error, meaning a face was found but didn't match anyone)."""
    gallery = get_all_embeddings()
    image = parse_base64_image(image_data).convert('RGB')
    frame = np.array(image)
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
    box, error = detect_single_face(face_cascade, gray, cfg.RECOGNIZE_DETECT_PARAMS)
    if box is None:
        return None, None, error
    if not gallery:
        return None, None, None
    (x, y, w, h) = box
    face_crop_rgb = align_face_crop(gray, frame, box, get_eye_cascade())
    embedding = compute_embedding(face_crop_rgb)
    student_id, similarity = find_best_match(embedding, gallery)
    if student_id is not None and similarity >= cfg.RECOGNIZE_MATCH_THRESHOLD:
        return student_id, similarity, None
    return None, None, None


@app.route('/')
def home():
    if session.get('user_type') == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif session.get('user_type') == 'student':
        return redirect(url_for('student_login'))
    return render_template('index.html')


@app.before_request
def restrict_access():
    allowed_routes = ['home', 'login', 'student_login', 'student_register', 'static', 'test_image', 'get_active_sessions']
    if request.endpoint in allowed_routes or not request.endpoint:
        return
    
    if request.path.startswith('/admin/'):
        if session.get('user_type') != 'admin':
            flash('Admin access required', 'error')
            return redirect(url_for('login'))
    
    if request.path.startswith('/student/'):
        # Some student routes might be public (login, register), handled by allowed_routes
        # But history and attend should be protected
        protected_student_routes = ['student_history', 'student_attend', 'student_mark_attendance', 'student_profile', 'student_change_password', 'student_register_face']
        if request.endpoint in protected_student_routes and session.get('user_type') != 'student':
            flash('Student login required', 'error')
            return redirect(url_for('student_login'))


@app.route('/captcha-image')
def captcha_image():
    text = generate_captcha_text()
    session['captcha_answer'] = text
    session['captcha_issued_at'] = datetime.now().isoformat()
    png_bytes = render_captcha_image(text)
    return Response(png_bytes, mimetype='image/png', headers={'Cache-Control': 'no-store'})


def _check_captcha(submitted):
    """Validates and consumes the CAPTCHA answer stored in the session.
    Always clears it afterward (pass or fail) so it can't be reused."""
    if not cfg.CAPTCHA_ENABLED:
        return True
    expected = session.pop('captcha_answer', None)
    session.pop('captcha_issued_at', None)
    if not expected or not submitted:
        return False
    return submitted.strip().upper() == expected.upper()


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit(lambda: cfg.RATE_LIMIT_LOGIN, methods=['POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        captcha_response = request.form.get('captcha')

        if not _check_captcha(captcha_response):
            flash('Incorrect CAPTCHA. Please try again.', 'error')
            log_audit('anonymous', username, 'admin_login_failed', details='captcha failed')
            return render_template('login.html')

        admin = query_db('SELECT * FROM admins WHERE username=?', (username,), one=True)

        if admin and _is_locked_out(admin):
            flash('This account is temporarily locked due to repeated failed login attempts. Please try again later.', 'error')
            log_audit('anonymous', username, 'admin_login_blocked', details='account locked')
            return render_template('login.html')

        if admin and check_password_hash(admin['password'], password):
            _clear_failed_logins('admins', admin['id'])
            session.clear()
            session.permanent = True
            session['admin_user'] = username
            session['user_type'] = 'admin'
            log_audit('admin', username, 'admin_login_success')
            return redirect(url_for('admin_dashboard'))

        if admin:
            attempts, locked_until = _register_failed_login('admins', admin['id'])
            if locked_until:
                flash(f'Too many failed attempts. This account is locked for {cfg.LOCKOUT_DURATION_MINUTES} minutes.', 'error')
            else:
                flash('Invalid username or password', 'error')
        else:
            flash('Invalid username or password', 'error')
        log_audit('anonymous', username, 'admin_login_failed')
    return render_template('login.html')


@app.route('/logout')
def logout():
    actor_name = session.get('admin_user') or session.get('student_id')
    actor_type = 'admin' if session.get('user_type') == 'admin' else 'student'
    if session.get('user_type'):
        log_audit(actor_type, str(actor_name), f'{actor_type}_logout')
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('home'))


@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    student_count = query_db('SELECT COUNT(*) as count FROM students', one=True)['count']
    session_count = query_db('SELECT COUNT(*) as count FROM sessions', one=True)['count']
    attendance_count = query_db('SELECT COUNT(*) as count FROM attendance', one=True)['count']
    
    # Get detailed attendance records
    attendance_records = query_db('''
        SELECT a.id, a.status, a.timestamp, a.note,
               s.name as student_name, s.roll_no,
               ses.title as session_title, ses.date as session_date, ses.time as session_time,
               sub.name as subject_name, sub.code as subject_code
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.id
        LEFT JOIN sessions ses ON a.session_id = ses.id
        LEFT JOIN subjects sub ON ses.subject_id = sub.id
        ORDER BY a.timestamp DESC
        LIMIT 20
    ''')
    
    low_alerts = []
    threshold = cfg.LOW_ATTENDANCE_THRESHOLD_PERCENT
    students = query_db('SELECT * FROM students')
    for student in students:
        total = query_db('SELECT COUNT(*) as count FROM attendance WHERE student_id=?', (student['id'],), one=True)['count']
        present = query_db('SELECT COUNT(*) as count FROM attendance WHERE student_id=? AND status=?', (student['id'], 'Present'), one=True)['count']
        percentage = (present / total * 100) if total > 0 else 0
        if percentage < threshold:
            low_alerts.append({'name': student['name'], 'roll_no': student['roll_no'], 'percentage': round(percentage, 1)})
    
    active_session_count = query_db('SELECT COUNT(*) as count FROM sessions WHERE active=1', one=True)['count']

    return render_template('admin_dashboard.html', 
                         students=student_count, 
                         sessions=session_count, 
                         active_sessions=active_session_count,
                         attendance=attendance_count,
                         attendance_records=attendance_records,
                         low_alerts=low_alerts, 
                         threshold=threshold)


@app.route('/admin/students')
def admin_students():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    search = (request.args.get('q') or '').strip()
    if search:
        total_count = query_db(
            'SELECT COUNT(*) as count FROM students WHERE name LIKE ? OR roll_no LIKE ?',
            (f'%{search}%', f'%{search}%'), one=True
        )['count']
    else:
        total_count = query_db('SELECT COUNT(*) as count FROM students', one=True)['count']

    pagination = get_pagination(total_count, cfg.STUDENTS_PER_PAGE)

    if search:
        students = query_db(
            'SELECT * FROM students WHERE name LIKE ? OR roll_no LIKE ? ORDER BY name LIMIT ? OFFSET ?',
            (f'%{search}%', f'%{search}%', pagination['per_page'], pagination['offset'])
        )
    else:
        students = query_db(
            'SELECT * FROM students ORDER BY name LIMIT ? OFFSET ?',
            (pagination['per_page'], pagination['offset'])
        )
    return render_template('admin_students.html', students=students, pagination=pagination, search=search)


@app.route('/admin/students/bulk-import', methods=['GET', 'POST'])
def bulk_import_students():
    """CSV bulk-import for onboarding many students at once. Creates
    accounts (name, roll_no, branch, semester, password) with NO face
    data — bulk face capture isn't practical via CSV. Each imported
    student completes their own registration afterward by logging in and
    visiting /student/register-face (linked from their profile page).

    Expected columns: name, roll_no, branch, semester, and optionally
    password (auto-generated and shown in the results if omitted)."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    if request.method == 'GET':
        return render_template('admin_bulk_import.html', results=None)

    upload = request.files.get('csv_file')
    if not upload or not upload.filename:
        flash('Please choose a CSV file to upload.', 'error')
        return redirect(url_for('bulk_import_students'))

    try:
        raw = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        flash('Could not read the file — please upload a UTF-8 encoded CSV.', 'error')
        return redirect(url_for('bulk_import_students'))

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or 'roll_no' not in reader.fieldnames:
        flash('CSV must include at least "name", "roll_no", "branch", and "semester" columns.', 'error')
        return redirect(url_for('bulk_import_students'))

    rows = list(reader)
    if len(rows) > cfg.MAX_BULK_IMPORT_ROWS:
        flash(f'Too many rows ({len(rows)}). Maximum is {cfg.MAX_BULK_IMPORT_ROWS} per import.', 'error')
        return redirect(url_for('bulk_import_students'))

    existing_roll_numbers = {r['roll_no'] for r in query_db('SELECT roll_no FROM students')}
    seen_in_file = set()
    created = []
    skipped = []

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        name = (row.get('name') or '').strip()
        roll_no = (row.get('roll_no') or '').strip()
        branch = (row.get('branch') or '').strip()
        semester = (row.get('semester') or '').strip()
        password = (row.get('password') or '').strip()

        if not name or not roll_no or not branch or not semester:
            skipped.append({'row': i, 'roll_no': roll_no or '(blank)', 'reason': 'missing required field(s)'})
            continue
        if roll_no in existing_roll_numbers:
            skipped.append({'row': i, 'roll_no': roll_no, 'reason': 'roll number already registered'})
            continue
        if roll_no in seen_in_file:
            skipped.append({'row': i, 'roll_no': roll_no, 'reason': 'duplicate roll number within this file'})
            continue

        generated_password = False
        if not password:
            password = secrets.token_urlsafe(9)
            generated_password = True
        else:
            password_error = validate_password_strength(password)
            if password_error:
                skipped.append({'row': i, 'roll_no': roll_no, 'reason': f'password: {password_error}'})
                continue

        student_id = execute_db(
            'INSERT INTO students(name, roll_no, branch, semester, password) VALUES(?, ?, ?, ?, ?)',
            (name, roll_no, branch, semester, generate_password_hash(password))
        )
        seen_in_file.add(roll_no)
        created.append({
            'student_id': student_id, 'name': name, 'roll_no': roll_no,
            'password': password if generated_password else None,
        })

    if created:
        log_audit('admin', session.get('admin_user'), 'bulk_student_import',
                   details=f'{len(created)} created, {len(skipped)} skipped')

    return render_template('admin_bulk_import.html', results={'created': created, 'skipped': skipped})


@app.route('/admin/students/bulk-import/template.csv')
def bulk_import_template():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['name', 'roll_no', 'branch', 'semester', 'password'])
    writer.writerow(['Jane Doe', '21CS001', 'CSE', '5', ''])
    writer.writerow(['John Smith', '21CS002', 'CSE', '5', 'OptionalPass123'])
    response = Response(buf.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=student_import_template.csv'
    return response


@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    """Admin account management: list existing admins, add a new one, or
    delete one (never the last remaining account — that would lock
    everyone out with no self-service way back in, given there's no
    email-based recovery for admin accounts; see README for the CLI-based
    recovery path)."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        new_username = (request.form.get('new_username') or '').strip()
        new_password = request.form.get('new_password')

        if not new_username or not new_password:
            flash('Username and password are required.', 'error')
        else:
            password_error = validate_password_strength(new_password)
            if password_error:
                flash(password_error, 'error')
            elif query_db('SELECT 1 FROM admins WHERE username=?', (new_username,), one=True):
                flash('That username is already taken.', 'error')
            else:
                execute_db('INSERT INTO admins(username, password) VALUES (?, ?)',
                           (new_username, generate_password_hash(new_password)))
                log_audit('admin', session.get('admin_user'), 'admin_account_created', target=new_username)
                flash(f'Admin account "{new_username}" created.', 'success')
        return redirect(url_for('admin_settings'))

    admins = query_db('SELECT id, username FROM admins ORDER BY username')
    return render_template('admin_settings.html', admins=admins)


@app.route('/admin/settings/admins/<int:admin_id>/delete', methods=['POST'])
def delete_admin(admin_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    total_admins = query_db('SELECT COUNT(*) as c FROM admins', one=True)['c']
    if total_admins <= 1:
        flash('Cannot delete the last remaining admin account.', 'error')
        return redirect(url_for('admin_settings'))

    target_admin = query_db('SELECT username FROM admins WHERE id=?', (admin_id,), one=True)
    if not target_admin:
        flash('Admin account not found.', 'error')
        return redirect(url_for('admin_settings'))

    if target_admin['username'] == session.get('admin_user'):
        flash('You cannot delete the account you are currently logged in as.', 'error')
        return redirect(url_for('admin_settings'))

    execute_db('DELETE FROM admins WHERE id=?', (admin_id,))
    log_audit('admin', session.get('admin_user'), 'admin_account_deleted', target=target_admin['username'])
    flash(f'Admin account "{target_admin["username"]}" deleted.', 'success')
    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/change-password', methods=['POST'])
def admin_change_own_password():
    """Lets the logged-in admin change their own password, given their
    current one — the self-service counterpart to reset_admin_password.py
    (which exists for when an admin is locked out entirely)."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    admin = query_db('SELECT * FROM admins WHERE username=?', (session['admin_user'],), one=True)
    if not admin or not check_password_hash(admin['password'], current_password or ''):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('admin_settings'))

    if new_password != confirm_password:
        flash('New password and confirmation do not match.', 'error')
        return redirect(url_for('admin_settings'))

    password_error = validate_password_strength(new_password)
    if password_error:
        flash(password_error, 'error')
        return redirect(url_for('admin_settings'))

    execute_db('UPDATE admins SET password=? WHERE id=?', (generate_password_hash(new_password), admin['id']))
    log_audit('admin', session['admin_user'], 'admin_changed_own_password')
    flash('Password updated.', 'success')
    return redirect(url_for('admin_settings'))


@app.route('/admin/attendance')
def admin_attendance_records():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    total_count = query_db('SELECT COUNT(*) as count FROM attendance', one=True)['count']
    pagination = get_pagination(total_count, cfg.ATTENDANCE_RECORDS_PER_PAGE)
    records = query_db('''
        SELECT a.id, a.status, a.timestamp, a.note,
               s.name as student_name, s.roll_no,
               ses.title as session_title, ses.date as session_date, ses.time as session_time,
               sub.name as subject_name, sub.code as subject_code
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.id
        LEFT JOIN sessions ses ON a.session_id = ses.id
        LEFT JOIN subjects sub ON ses.subject_id = sub.id
        ORDER BY a.timestamp DESC
        LIMIT ? OFFSET ?
    ''', (pagination['per_page'], pagination['offset']))
    return render_template('admin_attendance.html', records=records, pagination=pagination)


@app.route('/admin/audit-log')
def audit_log_view():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    total_count = query_db('SELECT COUNT(*) as count FROM audit_log', one=True)['count']
    pagination = get_pagination(total_count, cfg.AUDIT_LOG_PER_PAGE)
    entries = query_db(
        'SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?',
        (pagination['per_page'], pagination['offset'])
    )
    return render_template('audit_log.html', entries=entries, pagination=pagination)


@app.route('/admin/student/<int:student_id>/delete', methods=['POST'])
def delete_student(student_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401

    student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
    
    # 1. Delete attendance records
    execute_db('DELETE FROM attendance WHERE student_id=?', (student_id,))
    
    # 2. Delete face profile from FaceBase.db
    face_conn = sqlite3.connect(FACE_DATABASE_PATH)
    face_cursor = face_conn.cursor()
    face_cursor.execute('DELETE FROM people WHERE id=?', (student_id,))
    face_conn.commit()
    face_conn.close()
    
    # 3. Delete face images from Datasets directory
    try:
        for f in os.listdir(DATA_DIR):
            if f.startswith(f'User.{student_id}.'):
                os.remove(os.path.join(DATA_DIR, f))
    except Exception as e:
        print(f"Error deleting images: {e}")
    
    # 4. Delete student from app.db
    execute_db('DELETE FROM students WHERE id=?', (student_id,))
    
    # 5. Delete this student's face embeddings. No retraining step is
    # needed — unlike the old LBPH approach, which had to reprocess the
    # entire dataset from scratch on every add/delete, embeddings are an
    # incremental gallery: removing a student is just deleting their rows.
    delete_student_embeddings(student_id)

    target = f'{student["name"]} ({student["roll_no"]})' if student else f'student #{student_id}'
    log_audit('admin', session.get('admin_user'), 'student_deleted', target=target)
    
    flash('Student and all related records deleted successfully', 'success')
    return redirect(url_for('admin_students'))


@app.route('/admin/student/<int:student_id>/reset-password', methods=['POST'])
def reset_student_password(student_id):
    """Admin-initiated password reset for a student. There's no email/SMTP
    infrastructure in this project (see README), so a fully self-service
    "forgot password" isn't available — this is the practical alternative:
    the student contacts an admin, who generates a temporary password here
    and communicates it to the student out-of-band. The student can then
    set their own password from /student/profile."""
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401

    student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))

    temp_password = secrets.token_urlsafe(9)  # short, readable-enough random password
    execute_db('UPDATE students SET password=?, failed_attempts=0, locked_until=NULL WHERE id=?',
               (generate_password_hash(temp_password), student_id))

    log_audit('admin', session.get('admin_user'), 'student_password_reset',
              target=f'{student["name"]} ({student["roll_no"]})')

    flash(f'Password for {student["name"]} ({student["roll_no"]}) has been reset to: {temp_password} — share this with the student securely; it will not be shown again.', 'success')
    return redirect(url_for('admin_students'))


@app.route('/admin/sessions', methods=['GET', 'POST'])
def admin_sessions():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title')
        subject_name = request.form.get('subject_name')
        subject_code = request.form.get('subject_code')
        date = request.form.get('date')
        
        start_h = int(request.form.get('start_h', 12))
        start_m = int(request.form.get('start_m', 0))
        start_p = request.form.get('start_p', 'AM')
        
        end_h = int(request.form.get('end_h', 12))
        end_m = int(request.form.get('end_m', 0))
        end_p = request.form.get('end_p', 'PM')
        
        def to_24h(h, m, p):
            if p == 'PM' and h < 12: h += 12
            if p == 'AM' and h == 12: h = 0
            return f"{h:02d}:{m:02d}"
            
        time = to_24h(start_h, start_m, start_p)
        end_time = to_24h(end_h, end_m, end_p)
        
        end_date = request.form.get('end_date')
        is_recurring = 1 if request.form.get('is_recurring') else 0
        
        # Convert yyyy-mm-dd to dd-mm-yyyy for internal consistency
        def convert_date(d):
            if not d: return d
            try:
                return datetime.strptime(d, '%Y-%m-%d').strftime('%d-%m-%Y')
            except:
                return d
        
        date = convert_date(date)
        end_date = convert_date(end_date)
        
        if title and subject_name and subject_code and date and time and end_date and end_time:
            conflict = None
            if not cfg.ALLOW_OVERLAPPING_SESSIONS:
                conflict = find_conflicting_session(date, end_date, time, end_time)
            if conflict:
                flash(
                    f'This overlaps with an existing session: "{conflict["title"]}" '
                    f'({conflict["date"]} {conflict["time"]}-{conflict["end_time"]}). '
                    'A student can\'t be marked present in two places at once — adjust the time or delete the conflicting session first.',
                    'error'
                )
            else:
                subject = query_db('SELECT * FROM subjects WHERE code=?', (subject_code,), one=True)
                if not subject:
                    subject_id = execute_db('INSERT INTO subjects(name, code) VALUES(?, ?)', (subject_name, subject_code))
                else:
                    subject_id = subject['id']
                execute_db('''INSERT INTO sessions(subject_id, title, date, time, end_date, end_time, is_recurring, active) 
                              VALUES(?, ?, ?, ?, ?, ?, ?, ?)''', 
                           (subject_id, title, date, time, end_date, end_time, is_recurring, 0))
                flash('Session scheduled successfully', 'success')
        else:
            flash('Please fill all session fields', 'error')
    sessions_raw = query_db('SELECT s.*, subj.name as subject_name, subj.code as subject_code FROM sessions s LEFT JOIN subjects subj ON s.subject_id=subj.id')
    sessions = []
    for s in sessions_raw:
        sd = dict(s)
        try:
            sd['time_12h'] = datetime.strptime(s['time'], '%H:%M').strftime('%I:%M %p')
            sd['end_time_12h'] = datetime.strptime(s['end_time'], '%H:%M').strftime('%I:%M %p')
        except:
            sd['time_12h'] = s['time']
            sd['end_time_12h'] = s['end_time']
        sessions.append(sd)
    return render_template('admin_sessions.html', sessions=sessions)


@app.route('/admin/session/<int:session_id>/delete', methods=['POST'])
def delete_session(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Delete attendance records for this session
    execute_db('DELETE FROM attendance WHERE session_id=?', (session_id,))
    
    # Delete the session
    execute_db('DELETE FROM sessions WHERE id=?', (session_id,))
    
    log_audit('admin', session.get('admin_user'), 'session_deleted', target=f'session #{session_id}')
    flash('Session and related attendance records deleted successfully', 'success')
    return redirect(url_for('admin_sessions'))


@app.route('/admin/session/<int:session_id>/start', methods=['POST'])
def start_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    execute_db('UPDATE sessions SET active=1 WHERE id=?', (session_id,))
    log_audit('admin', session.get('admin_user'), 'session_started', target=f'session #{session_id}')
    return redirect(url_for('attendance_session', session_id=session_id))


@app.route('/admin/session/<int:session_id>/stop', methods=['POST'])
def stop_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    execute_db('UPDATE sessions SET active=0 WHERE id=?', (session_id,))
    log_audit('admin', session.get('admin_user'), 'session_stopped', target=f'session #{session_id}')
    return redirect(url_for('admin_sessions'))


@app.route('/admin/session/<int:session_id>/attendance-data')
def session_attendance_data(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Get all students with their attendance status for this session
    students_with_attendance = query_db('''
        SELECT s.id, s.name, s.roll_no, s.branch, s.semester,
               a.status, a.timestamp,
               CASE WHEN a.status IS NOT NULL THEN 1 ELSE 0 END as has_attendance
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.session_id = ?
        ORDER BY s.roll_no
    ''', (session_id,))
    
    # Debug: Print attendance data
    print(f'DEBUG: Session {session_id} attendance data:')
    for student in students_with_attendance:
        print(f'  Student {student["roll_no"]} ({student["name"]}): {student["status"]} at {student["timestamp"]}')
    
    # Calculate summary stats
    total = len(students_with_attendance)
    present = len([s for s in students_with_attendance if s['status'] == 'Present'])
    absent = total - present
    
    print(f'DEBUG: Summary - Total: {total}, Present: {present}, Absent: {absent}')
    
    return jsonify({
        'students': [dict(s) for s in students_with_attendance],
        'summary': {
            'total': total,
            'present': present,
            'absent': absent
        }
    })


@app.route('/admin/session/<int:session_id>')
def attendance_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    session_info = query_db('SELECT s.*, subj.name as subject_name, subj.code as subject_code FROM sessions s LEFT JOIN subjects subj ON s.subject_id=subj.id WHERE s.id=?', (session_id,), one=True)
    
    # Get all students with their attendance status for this session
    students_with_attendance = query_db('''
        SELECT s.id, s.name, s.roll_no, s.branch, s.semester,
               a.status, a.timestamp,
               CASE WHEN a.status IS NOT NULL THEN 1 ELSE 0 END as has_attendance
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.session_id = ?
        ORDER BY s.roll_no
    ''', (session_id,))
    
    return render_template('attendance_session.html', session_info=session_info, students=students_with_attendance)


@app.route('/admin/session/<int:session_id>/recognize', methods=['POST'])
def session_recognize(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    payload = request.get_json() or {}
    image_data = payload.get('image')
    if not image_data:
        return jsonify({'error': 'No image sent'}), 400
    student_id, similarity, error = recognize_face(image_data)
    if error:
        return jsonify({'status': 'error', 'message': error}), 200
    if student_id is None:
        return jsonify({'status': 'not_found'})
    existing = query_db('SELECT 1 FROM attendance WHERE session_id=? AND student_id=?', (session_id, student_id), one=True)
    if not existing:
        execute_db('INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES(?, ?, ?, ?)', (student_id, session_id, 'Present', datetime.now().isoformat()))
    profile = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    score_out_of_100 = max(0, min(similarity, 1.0)) * 100
    return jsonify({'status': 'present', 'student': {'id': profile['id'], 'name': profile['name'], 'roll_no': profile['roll_no']}, 'confidence': score_out_of_100})


@app.route('/admin/session/<int:session_id>/override', methods=['POST'])
def session_override(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    student_id = request.form.get('student_id')
    action = request.form.get('action')
    if not student_id or not action:
        return redirect(url_for('attendance_session', session_id=session_id))
    status = 'Present' if action == 'present' else 'Absent'
    execute_db('DELETE FROM attendance WHERE session_id=? AND student_id=?', (session_id, student_id))
    execute_db('INSERT INTO attendance(student_id, session_id, status, timestamp, note) VALUES(?, ?, ?, ?, ?)', (student_id, session_id, status, datetime.now().isoformat(), 'Manual override'))
    log_audit('admin', session.get('admin_user'), 'attendance_overridden',
              target=f'student #{student_id} in session #{session_id}', details=f'set to {status}')
    return redirect(url_for('attendance_session', session_id=session_id))


@app.route('/admin/session/<int:session_id>/export')
def export_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    session_info = query_db('SELECT s.*, subj.name as subject_name, subj.code as subject_code FROM sessions s LEFT JOIN subjects subj ON s.subject_id=subj.id WHERE s.id=?', (session_id,), one=True)
    records = query_db('SELECT a.*, st.name, st.roll_no, st.branch, st.semester FROM attendance a LEFT JOIN students st ON a.student_id=st.id WHERE a.session_id=?', (session_id,))
    csv_file = io.StringIO()
    writer = csv.writer(csv_file)
    writer.writerow(['Student ID', 'Name', 'Roll No', 'Branch', 'Semester', 'Status', 'Timestamp', 'Note'])
    for row in records:
        writer.writerow([row['student_id'], row['name'], row['roll_no'], row['branch'], row['semester'], row['status'], row['timestamp'], row['note']])
    csv_file.seek(0)
    return send_file(io.BytesIO(csv_file.getvalue().encode('utf-8')), mimetype='text/csv', as_attachment=True, download_name=f'session_{session_id}_attendance.csv')


@app.route('/student/register', methods=['GET', 'POST'])
def student_register():
    if request.method == 'POST':
        data = request.get_json() or {}
        name = data.get('name')
        roll_no = data.get('roll_no')
        branch = data.get('branch')
        semester = data.get('semester')
        password = data.get('password')
        gender = data.get('gender', 'other')
        images = data.get('images', [])
        if not name or not roll_no or not branch or not semester or not password or not images:
            return jsonify({'error': 'Missing fields'}), 400

        password_error = validate_password_strength(password)
        if password_error:
            return jsonify({'error': password_error}), 400

        # Payload-size guardrails, on top of the global MAX_CONTENT_LENGTH:
        # cap how many images and how large any single one can be.
        if len(images) > cfg.MAX_REGISTRATION_IMAGES:
            return jsonify({'error': f'Too many images (max {cfg.MAX_REGISTRATION_IMAGES}).'}), 400
        for img_data in images:
            if not isinstance(img_data, str) or len(img_data) > cfg.MAX_IMAGE_BASE64_CHARS:
                return jsonify({'error': 'One or more images is too large.'}), 400

        # Check if roll_no already exists
        existing_roll = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
        if existing_roll:
            return jsonify({'error': 'Roll number already registered'}), 400

        # Check if a face is actually present in the provided images
        has_face = False
        face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
        for img_data in images:
            img = parse_base64_image(img_data)
            frame = np.array(img.convert('RGB'))
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            if len(face_cascade.detectMultiScale(gray, **cfg.REGISTRATION_DETECT_PARAMS)) > 0:
                has_face = True
                break
                
        if not has_face:
            return jsonify({'error': 'No face detected in the images! Please look directly at the camera in a well-lit area.'}), 400

        # Check if this face already exists (skips cleanly if the
        # embedding gallery is still empty — recognize_face() handles
        # that case on its own).
        existing_student_id = None
        for img_data in images[:5]:  # Check the first 5 images
            found_id, similarity, _error = recognize_face(img_data)
            if found_id is not None and similarity >= cfg.DUPLICATE_FACE_MATCH_THRESHOLD:  # Strict threshold for duplicate check
                existing_student_id = found_id
                break
        
        if existing_student_id is not None:
            existing_student = query_db('SELECT * FROM students WHERE id=?', (existing_student_id,), one=True)
            if existing_student:
                return jsonify({'error': f'Face already registered to {existing_student["name"]} ({existing_student["roll_no"]})'}), 400

        student_id = execute_db('INSERT INTO students(name, roll_no, branch, semester, password) VALUES(?, ?, ?, ?, ?)', (name, roll_no, branch, semester, generate_password_hash(password)))
        save_face_profile(student_id, name, gender, branch)
        saved, skip_reasons = save_face_images(student_id, images)
        if saved == 0:
            # Every image failed quality/detection checks — roll back the
            # student record rather than leaving an account with zero
            # usable face data.
            execute_db('DELETE FROM students WHERE id=?', (student_id,))
            reason_summary = '; '.join(sorted(set(skip_reasons))) if skip_reasons else 'no usable face images'
            return jsonify({'error': f'None of the photos could be used ({reason_summary}). Please retake in good, even lighting with only your face in frame.'}), 400
        execute_db('UPDATE students SET photo_count=? WHERE id=?', (saved, student_id))
        response = {'status': 'ok', 'student_id': student_id}
        if skip_reasons:
            response['warning'] = f'{len(skip_reasons)} of {len(images)} photos were skipped ({", ".join(sorted(set(skip_reasons)))}); {saved} were used.'
        return jsonify(response)
    return render_template('student_register.html')


@app.route('/student/login', methods=['GET', 'POST'])
@limiter.limit(lambda: cfg.RATE_LIMIT_LOGIN, methods=['POST'])
def student_login():
    if session.get('user_type') == 'student':
        return render_template('student_login.html', logged_in=True)

    if request.method == 'POST':
        data = request.get_json() or {}
        roll_no = data.get('roll_no')
        password = data.get('password')
        captcha_response = data.get('captcha')

        if not roll_no or not password:
            return jsonify({'status': 'error', 'message': 'Roll number and password required'}), 400

        if not _check_captcha(captcha_response):
            log_audit('anonymous', roll_no, 'student_login_failed', details='captcha failed')
            return jsonify({'status': 'error', 'message': 'Incorrect CAPTCHA'}), 400

        student = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
        if not student:
            log_audit('anonymous', roll_no, 'student_login_failed', details='no such roll number')
            return jsonify({'status': 'error', 'message': 'Student not found. Please register first.'}), 404

        if _is_locked_out(student):
            log_audit('anonymous', roll_no, 'student_login_blocked', details='account locked')
            return jsonify({'status': 'error', 'message': f'This account is temporarily locked due to repeated failed login attempts. Please try again in a few minutes.'}), 403

        if not check_password_hash(student['password'], password):
            attempts, locked_until = _register_failed_login('students', student['id'])
            log_audit('anonymous', roll_no, 'student_login_failed', details='wrong password')
            if locked_until:
                return jsonify({'status': 'error', 'message': f'Too many failed attempts. This account is locked for {cfg.LOCKOUT_DURATION_MINUTES} minutes.'}), 403
            return jsonify({'status': 'error', 'message': 'Incorrect password'}), 401

        _clear_failed_logins('students', student['id'])
        session.clear()
        session.permanent = True
        session['student_id'] = student['id']
        session['student_name'] = student['name']
        session['user_type'] = 'student'
        log_audit('student', roll_no, 'student_login_success')

        student_data = dict(student)
        student_data.pop('password', None)  # never send the password hash to the client
        return jsonify({'status': 'ok', 'student': student_data})
    
    return render_template('student_login.html', logged_in=False)


@app.route('/student/search')
def student_search():
    roll_no = request.args.get('roll_no', '').strip()
    if not roll_no:
        return jsonify({'status': 'error', 'message': 'Roll number required'}), 400
    student = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
    if student:
        return jsonify({'status': 'ok', 'student': dict(student)})
    return jsonify({'status': 'error', 'message': 'Student not found'}), 404


@app.route('/get-active-sessions')
def get_active_sessions():
    now = datetime.now()
    current_date = now.strftime('%d-%m-%Y')
    current_time = now.strftime('%H:%M')
    
    sessions = query_db('''
        SELECT s.*, sub.name as subject_name, sub.code as subject_code 
        FROM sessions s 
        LEFT JOIN subjects sub ON s.subject_id = sub.id 
        ORDER BY s.date DESC, s.time DESC
    ''')
    
    active_sessions = []
    for s in sessions:
        is_active = False
        if s['active'] == 1:
            is_active = True
        elif s['date'] and s['time'] and s['end_date'] and s['end_time']:
            try:
                start_dt = datetime.strptime(f"{s['date']} {s['time']}", '%d-%m-%Y %H:%M')
                end_dt = datetime.strptime(f"{s['end_date']} {s['end_time']}", '%d-%m-%Y %H:%M')
                
                if s['is_recurring'] == 1:
                    # Recurring every day: check if current date is in range and current time is in range
                    curr_date_dt = datetime.strptime(current_date, '%d-%m-%Y')
                    start_date_dt = datetime.strptime(s['date'], '%d-%m-%Y')
                    end_date_dt = datetime.strptime(s['end_date'], '%d-%m-%Y')
                    
                    if start_date_dt <= curr_date_dt <= end_date_dt:
                        # Date is in range, now check time
                        if s['time'] <= current_time <= s['end_time']:
                            is_active = True
                else:
                    # Not recurring: just check if now is between start and end
                    if start_dt <= now <= end_dt:
                        is_active = True
            except Exception as e:
                print(f"Error checking session bounds for ID {s['id']}: {e}")
        
        if is_active:
            # Add formatted info for the UI
            session_dict = dict(s)
            session_dict['is_auto_active'] = (s['active'] == 0)
            try:
                session_dict['time_12h'] = datetime.strptime(s['time'], '%H:%M').strftime('%I:%M %p')
                session_dict['end_time_12h'] = datetime.strptime(s['end_time'], '%H:%M').strftime('%I:%M %p')
            except:
                session_dict['time_12h'] = s['time']
                session_dict['end_time_12h'] = s['end_time']
            active_sessions.append(session_dict)
            
    return jsonify({'sessions': active_sessions})


@app.route('/student/attend')
def student_attend():
    session_id = request.args.get('session_id')
    student_id = session.get('student_id')
    
    if not student_id:
        return redirect(url_for('student_login'))
    
    try:
        session_id = int(session_id) if session_id else None
    except:
        session_id = None
    
    if not session_id:
        flash('Please select a session first', 'warning')
        return redirect(url_for('student_login'))
    
    session_info = query_db('SELECT s.*, sub.name as subject_name, sub.code as subject_code FROM sessions s LEFT JOIN subjects sub ON s.subject_id = sub.id WHERE s.id=?', (session_id,), one=True)
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    
    if not session_info or not student:
        flash('Session or student record not found', 'error')
        return redirect(url_for('student_login'))
    
    return render_template('student_attend.html', session_info=dict(session_info), student=dict(student))


@app.route('/student/test-image', methods=['POST'])
def test_image():
    """Test endpoint to check if image is being received properly"""
    try:
        data = request.get_json() or {}
        image = data.get('image')
        
        if not image:
            return jsonify({'status': 'error', 'message': 'No image received'}), 400
        
        # Process image data
        if not image.startswith('data:image/'):
            return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400
        
        img_data = image.split(',')[1] if ',' in image else image
        img_bytes = base64.b64decode(img_data)
        img = Image.open(io.BytesIO(img_bytes))
        
        # Convert to numpy array and grayscale
        nparr = np.array(img)
        if len(nparr.shape) == 3 and nparr.shape[2] == 3:
            gray = cv2.cvtColor(nparr, cv2.COLOR_RGB2GRAY)
        elif len(nparr.shape) == 2:
            gray = nparr
        else:
            return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400
        
        # Save the image for inspection
        test_img_path = cfg.TEST_CAPTURE_PATH
        cv2.imwrite(test_img_path, gray)
        
        return jsonify({
            'status': 'success', 
            'message': f'Image received and saved to {test_img_path}',
            'shape': gray.shape,
            'size': len(img_bytes)
        })
        
    except Exception as e:
        print(f'Test image error: {e}')
        return jsonify({'status': 'error', 'message': f'Error: {str(e)}'}), 500


def decode_base64_to_gray(image):
    """Decode a data-URL base64 image string into a grayscale numpy array,
    or return None if it isn't a valid image."""
    if not image or not image.startswith('data:image/'):
        return None
    img_data = image.split(',')[1] if ',' in image else image
    img_bytes = base64.b64decode(img_data)
    img = Image.open(io.BytesIO(img_bytes))
    nparr = np.array(img)
    if len(nparr.shape) == 3 and nparr.shape[2] == 3:
        return cv2.cvtColor(nparr, cv2.COLOR_RGB2GRAY)
    elif len(nparr.shape) == 2:
        return nparr
    return None


def decode_base64_to_rgb(image):
    """Decode a data-URL base64 image string into an RGB numpy array (for
    embedding computation, which needs color), or None if invalid."""
    if not image or not image.startswith('data:image/'):
        return None
    img_data = image.split(',')[1] if ',' in image else image
    img_bytes = base64.b64decode(img_data)
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    return np.array(img)


@app.route('/student/attend/mark', methods=['POST'])
@limiter.limit(lambda: cfg.RATE_LIMIT_ATTENDANCE)
def student_mark_attendance():
    try:
        # Defense-in-depth: the before_request handler already requires a
        # logged-in student for this endpoint, but we check again here so
        # this function is safe even if that gate is ever changed.
        student_id = session.get('student_id')
        if not student_id:
            return jsonify({'status': 'error', 'message': 'Student login required'}), 401

        # Optional network restriction ("geofencing" by IP rather than
        # GPS — see ATTENDANCE_ALLOWED_NETWORKS in config.py). No-op if
        # unconfigured.
        if not is_ip_allowed_for_attendance(request.remote_addr):
            log_audit('student', str(student_id), 'attendance_blocked_network', details=f'ip={request.remote_addr}')
            return jsonify({'status': 'error', 'message': 'Attendance can only be marked from an allowed network.'}), 403

        data = request.get_json() or {}
        session_id = data.get('session_id')
        images = data.get('images')
        # Back-compat: accept the older two-field {image, image2} shape
        # too, padding it out (this just means the blink signal won't
        # have much of a burst to work with — motion-only, effectively).
        if images is None:
            legacy_image, legacy_image2 = data.get('image'), data.get('image2')
            images = [legacy_image, legacy_image2] if legacy_image and legacy_image2 else None
        # student_id is intentionally NOT read from the request body — it is
        # always the currently logged-in student, taken from the server-side
        # session, so one student can't mark attendance as another by
        # editing the payload.

        # Convert to integers with validation
        try:
            session_id = int(session_id) if session_id else None
        except (ValueError, TypeError):
            return jsonify({'status': 'error', 'message': 'Invalid session_id'}), 400

        if not session_id or not images or len(images) < 2:
            return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        if len(images) > cfg.LIVENESS_FRAME_COUNT:
            images = images[:cfg.LIVENESS_FRAME_COUNT]

        for img in images:
            if not isinstance(img, str) or len(img) > cfg.MAX_IMAGE_BASE64_CHARS:
                return jsonify({'status': 'error', 'message': 'Image too large.'}), 400
        
        # Check if session is active
        session_row = query_db('SELECT * FROM sessions WHERE id=?', (session_id,), one=True)
        if not session_row or not session_row['active']:
            return jsonify({'status': 'error', 'message': 'Session not active'}), 400
        
        # Check if already marked
        existing = query_db('SELECT * FROM attendance WHERE student_id=? AND session_id=?', (student_id, session_id), one=True)
        if existing:
            return jsonify({'status': 'already_marked', 'message': 'Already marked attendance'}), 200
        
        # Try to recognize face
        try:
            if not all(isinstance(img, str) and img.startswith('data:image/') for img in images):
                return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400

            frames_gray = [decode_base64_to_gray(img) for img in images]
            if any(g is None for g in frames_gray):
                return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400

            if not os.path.exists(CASCADE_PATH):
                return jsonify({'status': 'error', 'message': 'Face cascade file not found'}), 500

            # Debug: save the first frame to check what the server saw
            cv2.imwrite(cfg.DEBUG_IMAGE_PATH, frames_gray[0])

            cascade = cv2.CascadeClassifier(CASCADE_PATH)
            eye_cascade = get_eye_cascade()

            # Multi-face rejection on the reference (first) frame: catches
            # someone else visibly in frame during the capture.
            primary_box, multi_face_error = detect_single_face(cascade, frames_gray[0], cfg.MARK_ATTENDANCE_DETECT_PRIMARY_PARAMS)
            if primary_box is None and multi_face_error and 'Multiple' in multi_face_error:
                return jsonify({'status': 'error', 'message': multi_face_error}), 200
            if primary_box is None:
                # Fall back to the looser detection params before giving up.
                primary_box, multi_face_error = detect_single_face(cascade, frames_gray[0], cfg.MARK_ATTENDANCE_DETECT_FALLBACK_PARAMS)
                if primary_box is None:
                    return jsonify({'status': 'error', 'message': multi_face_error}), 200

            # Liveness: motion across the frame burst, plus (if enabled) a
            # detectable blink — see check_liveness_sequence()'s docstring
            # for what this does and doesn't defend against.
            is_live, _boxes = check_liveness_sequence(cascade, eye_cascade, frames_gray, cfg.MARK_ATTENDANCE_DETECT_PRIMARY_PARAMS)
            if not is_live:
                return jsonify({
                    'status': 'error',
                    'message': 'Could not confirm a live camera feed. Please make sure you are using a live camera (not a photo or screen), blink naturally, and try again.'
                }), 200
            
            # 1:1 verification: compare the captured face only against the
            # logged-in student's OWN stored embeddings (not the whole
            # gallery). This is both faster and a better fit for "confirm
            # this is who they claim to be" than open-ended identification.
            student_embeddings = get_student_embeddings(student_id)
            if not student_embeddings:
                return jsonify({'status': 'error', 'message': 'No registered face data found for your account. Please contact administrator.'}), 200

            rgb = decode_base64_to_rgb(images[0])
            if rgb is None:
                return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400
            face_crop_rgb = align_face_crop(frames_gray[0], rgb, primary_box, eye_cascade)

            embedding = compute_embedding(face_crop_rgb)
            _, similarity = find_best_match(embedding, [(student_id, e) for e in student_embeddings])
            
            print(f'DEBUG: Best match similarity: {similarity:.3f}, Student ID: {student_id}')

            # Cosine similarity: HIGHER is a better match (opposite
            # direction from the old LBPH "confidence" scale).
            if similarity >= cfg.MARK_ATTENDANCE_MATCH_THRESHOLD:
                status = 'Present'
                note = f'Face recognized (similarity: {similarity:.2f})'
                # Mark attendance
                attendance_id = execute_db('INSERT INTO attendance(student_id, session_id, status, timestamp, note) VALUES(?, ?, ?, ?, ?)', 
                          (student_id, session_id, status, datetime.now().isoformat(), note))
                # Return success with face match details
                profile = query_db('SELECT name FROM students WHERE id=?', (student_id,), one=True)
                score_out_of_100 = max(0, min(similarity, 1.0)) * 100
                match_msg = f'Face detected and matched to {profile["name"]} (match score: {score_out_of_100:.1f}/100)'
                return jsonify({'status': 'present', 'message': match_msg, 'confidence': score_out_of_100})
            else:
                score_out_of_100 = max(0, min(similarity, 1.0)) * 100
                return jsonify({'status': 'error', 'message': f'Face not recognized with high enough confidence (Score: {score_out_of_100:.1f}/100). Please improve lighting and try again.'}), 200
                
        except Exception as e:
            print(f'Face recognition error: {e}')
            return jsonify({'status': 'error', 'message': f'Face recognition failed: {str(e)}'}), 200
            
    except Exception as e:
        print(f'General error: {e}')
        return jsonify({'status': 'error', 'message': f'Server error: {str(e)}'}), 200


@app.route('/student/history')
def student_history():
    student_id = session.get('student_id')
    if not student_id:
        return redirect(url_for('student_login'))
        
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))
        
    attendance = query_db('SELECT a.*, s.title, s.date, s.time FROM attendance a LEFT JOIN sessions s ON a.session_id=s.id WHERE a.student_id=? ORDER BY a.timestamp DESC', (student_id,))
    total = len(attendance)
    present = len([r for r in attendance if r['status'] == 'Present'])
    percentage = round((present / total * 100) if total > 0 else 0, 1)
    
    return render_template('student_history.html', student=student, attendance=attendance, percentage=percentage)


@app.route('/student/register-face', methods=['GET', 'POST'])
def student_register_face():
    """Self-service face registration for a student who already has an
    account but no face photos yet — the case for anyone created via
    admin CSV bulk-import (see bulk_import_students()), which creates
    accounts without capturing faces. Reuses the same per-image
    processing as registration (multi-face rejection, quality checks,
    alignment) but adds to the ALREADY-authenticated student from the
    session, never a client-supplied id."""
    student_id = session.get('student_id')
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))

    if request.method == 'GET':
        existing_count = len(get_student_embeddings(student_id))
        return render_template('student_register_face.html', student=student, existing_count=existing_count)

    data = request.get_json() or {}
    images = data.get('images') or []
    if not images:
        return jsonify({'error': 'No images provided'}), 400
    if len(images) > cfg.MAX_REGISTRATION_IMAGES:
        return jsonify({'error': f'Too many images (max {cfg.MAX_REGISTRATION_IMAGES}).'}), 400
    for img in images:
        if not isinstance(img, str) or len(img) > cfg.MAX_IMAGE_BASE64_CHARS:
            return jsonify({'error': 'One or more images is too large.'}), 400

    saved, skip_reasons = save_face_images(student_id, images)
    if saved == 0:
        reason_summary = '; '.join(sorted(set(skip_reasons))) if skip_reasons else 'no usable face images'
        return jsonify({'error': f'None of the photos could be used ({reason_summary}). Please retake in good, even lighting with only your face in frame.'}), 400

    new_total = len(get_student_embeddings(student_id))
    execute_db('UPDATE students SET photo_count=? WHERE id=?', (new_total, student_id))
    log_audit('student', student['roll_no'], 'student_added_face_photos', details=f'+{saved} photo(s)')

    response = {'status': 'ok', 'saved': saved, 'total_photos': new_total}
    if skip_reasons:
        response['warning'] = f'{len(skip_reasons)} of {len(images)} photos were skipped ({", ".join(sorted(set(skip_reasons)))}); {saved} were used.'
    return jsonify(response)


@app.route('/student/profile')
def student_profile():
    student_id = session.get('student_id')
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))

    embedding_count = len(get_student_embeddings(student_id))
    attendance = query_db('SELECT status FROM attendance WHERE student_id=?', (student_id,))
    total = len(attendance)
    present = len([r for r in attendance if r['status'] == 'Present'])
    percentage = round((present / total * 100) if total > 0 else 0, 1)

    return render_template(
        'student_profile.html',
        student=student,
        embedding_count=embedding_count,
        total_sessions=total,
        percentage=percentage,
    )


@app.route('/student/profile/change-password', methods=['POST'])
def student_change_password():
    student_id = session.get('student_id')
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))

    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if not check_password_hash(student['password'], current_password or ''):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('student_profile'))

    if new_password != confirm_password:
        flash('New password and confirmation do not match.', 'error')
        return redirect(url_for('student_profile'))

    password_error = validate_password_strength(new_password)
    if password_error:
        flash(password_error, 'error')
        return redirect(url_for('student_profile'))

    execute_db('UPDATE students SET password=? WHERE id=?', (generate_password_hash(new_password), student_id))
    log_audit('student', student['roll_no'], 'student_changed_own_password')
    flash('Password updated.', 'success')
    return redirect(url_for('student_profile'))


if __name__ == '__main__':
    init_databases()
    # Safe-by-default: binds to localhost only, debug mode off.
    # Override deliberately via environment variables for local development,
    # e.g.: FLASK_DEBUG=1 FLASK_RUN_HOST=0.0.0.0 python app.py
    app.run(host=cfg.FLASK_RUN_HOST, port=cfg.FLASK_RUN_PORT, debug=cfg.FLASK_DEBUG)
