"""
Centralized configuration for the attendance app.

Every value here can be overridden with an environment variable of the
same name (see the ENV_VAR comment above each setting), so nothing about
paths, thresholds, or detection tuning needs to be hunted down inside
app.py. Defaults match the values the app previously had hardcoded, so
behavior is unchanged unless you deliberately override something.

Values can be set directly as OS environment variables, or placed in a
`.env` file in the project root (see `.env.example`) — python-dotenv
loads that file automatically if present.
"""
import os
import cv2
from dotenv import load_dotenv

load_dotenv()


def _env_int(name, default):
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name, default):
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    try:
        return float(value)
    except ValueError:
        return default


# --- Paths --------------------------------------------------------------
# ENV_VAR: BASE_DIR
BASE_DIR = os.environ.get('BASE_DIR') or os.path.abspath(os.path.dirname(__file__))
# ENV_VAR: DATA_DIR
DATA_DIR = os.environ.get('DATA_DIR') or os.path.join(BASE_DIR, 'Datasets')
# ENV_VAR: MODELS_DIR
MODELS_DIR = os.environ.get('MODELS_DIR') or os.path.join(BASE_DIR, 'models')
# ENV_VAR: DATABASE_DIR
DATABASE_DIR = os.environ.get('DATABASE_DIR') or os.path.join(BASE_DIR, 'database')
# ENV_VAR: DATABASE_PATH
DATABASE_PATH = os.environ.get('DATABASE_PATH') or os.path.join(DATABASE_DIR, 'app.db')
# ENV_VAR: FACE_DATABASE_PATH
FACE_DATABASE_PATH = os.environ.get('FACE_DATABASE_PATH') or os.path.join(DATABASE_DIR, 'FaceBase.db')
# ENV_VAR: CASCADE_PATH
CASCADE_PATH = os.environ.get('CASCADE_PATH') or os.path.join(BASE_DIR, 'haarcascade_frontalface_default.xml')
# ENV_VAR: EMBEDDER_MODEL_PATH
# Pretrained OpenFace CNN (see models/README.md) used to turn a face crop
# into a 128-d embedding vector, loaded via cv2.dnn.readNetFromTorch().
EMBEDDER_MODEL_PATH = os.environ.get('EMBEDDER_MODEL_PATH') or os.path.join(MODELS_DIR, 'openface_nn4.small2.v1.t7')
# ENV_VAR: DEBUG_IMAGE_PATH
DEBUG_IMAGE_PATH = os.environ.get('DEBUG_IMAGE_PATH') or os.path.join(BASE_DIR, 'debug_face.jpg')
# ENV_VAR: TEST_CAPTURE_PATH
TEST_CAPTURE_PATH = os.environ.get('TEST_CAPTURE_PATH') or os.path.join(BASE_DIR, 'test_capture.jpg')

# --- Secret key / server ------------------------------------------------
# ENV_VAR: FLASK_SECRET_KEY
SECRET_KEY_ENV_VAR = 'FLASK_SECRET_KEY'
# ENV_VAR: DEFAULT_ADMIN_PASSWORD
DEFAULT_ADMIN_PASSWORD = os.environ.get('DEFAULT_ADMIN_PASSWORD', 'admin123')
# ENV_VAR: FLASK_DEBUG
FLASK_DEBUG = os.environ.get('FLASK_DEBUG', '0') == '1'
# ENV_VAR: FLASK_RUN_HOST
FLASK_RUN_HOST = os.environ.get('FLASK_RUN_HOST', '127.0.0.1')
# ENV_VAR: FLASK_RUN_PORT
FLASK_RUN_PORT = _env_int('FLASK_RUN_PORT', 5000)

# --- Reverse proxy -------------------------------------------------------
# Set to 1 when this app is deployed behind a reverse proxy (nginx, a cloud
# load balancer, etc. — see the Dockerfile/README for the intended
# production setup). Wraps the WSGI app in werkzeug's ProxyFix so
# request.remote_addr is read from X-Forwarded-For instead of always being
# the proxy's own address. Leave off (default) for direct/local access, so
# a spoofed X-Forwarded-For header from an untrusted client can't be used
# to fake request.remote_addr.
# ENV_VAR: BEHIND_REVERSE_PROXY
BEHIND_REVERSE_PROXY = os.environ.get('BEHIND_REVERSE_PROXY', '0') == '1'
# ENV_VAR: PROXY_FIX_NUM_PROXIES
# How many trusted reverse proxy hops sit in front of the app (usually 1).
# Passed as ProxyFix's x_for/x_proto/x_host/x_port counts — see
# https://werkzeug.palletsprojects.com/en/latest/middleware/proxy_fix/.
PROXY_FIX_NUM_PROXIES = _env_int('PROXY_FIX_NUM_PROXIES', 1)

# --- Attendance rules -----------------------------------------------------
# ENV_VAR: LOW_ATTENDANCE_THRESHOLD_PERCENT
# Below this attendance percentage, a student shows up in the admin
# dashboard's low-attendance alert list.
LOW_ATTENDANCE_THRESHOLD_PERCENT = _env_float('LOW_ATTENDANCE_THRESHOLD_PERCENT', 75)

# --- Face embedding match thresholds --------------------------------------
# EMBEDDER_MODEL_PATH points at the OpenFace CNN; it expects a 96x96 crop.
EMBEDDING_INPUT_SIZE = 96
# Cosine similarity between two 128-d OpenFace embeddings: HIGHER is a
# better match (opposite direction from the old LBPH "confidence" scale).
# Embeddings are L2-normalized, so cosine similarity is just a dot
# product, in [-1, 1] — same-person pairs typically land around 0.5-0.9,
# different-person pairs typically land well below that. These starting
# defaults are a reasonable middle ground, not a guarantee for your
# specific camera/lighting — tune based on your own false-accept /
# false-reject experience.
#
# ENV_VAR: RECOGNIZE_MATCH_THRESHOLD
# Used by recognize_face() for 1:N identification (admin live-session
# recognition, and the registration duplicate-face check).
RECOGNIZE_MATCH_THRESHOLD = _env_float('RECOGNIZE_MATCH_THRESHOLD', 0.5)
# ENV_VAR: DUPLICATE_FACE_MATCH_THRESHOLD
# Stricter (higher) threshold used at registration time to detect "this
# face is already registered to someone else" — a higher bar than plain
# recognition, to avoid rejecting a legitimate new registration on a
# borderline resemblance.
DUPLICATE_FACE_MATCH_THRESHOLD = _env_float('DUPLICATE_FACE_MATCH_THRESHOLD', 0.6)
# ENV_VAR: MARK_ATTENDANCE_MATCH_THRESHOLD
# Used for 1:1 verification when a student self-marks attendance: this is
# checked only against the *logged-in* student's own stored embeddings,
# not the whole gallery, so it can afford to be a bit stricter than
# open-ended identification.
MARK_ATTENDANCE_MATCH_THRESHOLD = _env_float('MARK_ATTENDANCE_MATCH_THRESHOLD', 0.55)

# --- Haar cascade face-detection parameters -------------------------------
# Each dict maps directly onto cv2.CascadeClassifier.detectMultiScale()
# keyword arguments. Face *detection* (finding a face box) still uses the
# Haar cascade, same as before — only face *recognition* (matching a
# detected face to an identity) was upgraded to embeddings. Different call
# sites use different tuning:
#   - registration capture: looser, so registration doesn't fail easily
#   - recognition / duplicate-check: looser scaleFactor, larger min size
#   - attendance marking: two-pass (strict, then a looser fallback) to
#     resist false positives on backgrounds while still tolerating a
#     less-than-perfect first frame
REGISTRATION_DETECT_PARAMS = {
    'scaleFactor': _env_float('REGISTRATION_DETECT_SCALE_FACTOR', 1.1),
    'minNeighbors': _env_int('REGISTRATION_DETECT_MIN_NEIGHBORS', 5),
    'minSize': (
        _env_int('REGISTRATION_DETECT_MIN_SIZE', 50),
        _env_int('REGISTRATION_DETECT_MIN_SIZE', 50),
    ),
}

RECOGNIZE_DETECT_PARAMS = {
    'scaleFactor': _env_float('RECOGNIZE_DETECT_SCALE_FACTOR', 1.2),
    'minNeighbors': _env_int('RECOGNIZE_DETECT_MIN_NEIGHBORS', 5),
    'minSize': (
        _env_int('RECOGNIZE_DETECT_MIN_SIZE', 100),
        _env_int('RECOGNIZE_DETECT_MIN_SIZE', 100),
    ),
}

MARK_ATTENDANCE_DETECT_PRIMARY_PARAMS = {
    'scaleFactor': _env_float('MARK_ATTENDANCE_DETECT_SCALE_FACTOR', 1.1),
    'minNeighbors': _env_int('MARK_ATTENDANCE_DETECT_MIN_NEIGHBORS', 6),
    'minSize': (
        _env_int('MARK_ATTENDANCE_DETECT_MIN_SIZE', 100),
        _env_int('MARK_ATTENDANCE_DETECT_MIN_SIZE', 100),
    ),
}
MARK_ATTENDANCE_DETECT_FALLBACK_PARAMS = {
    'scaleFactor': _env_float('MARK_ATTENDANCE_FALLBACK_SCALE_FACTOR', 1.2),
    'minNeighbors': _env_int('MARK_ATTENDANCE_FALLBACK_MIN_NEIGHBORS', 5),
    'minSize': (
        _env_int('MARK_ATTENDANCE_FALLBACK_DETECT_MIN_SIZE', 80),
        _env_int('MARK_ATTENDANCE_FALLBACK_DETECT_MIN_SIZE', 80),
    ),
}

# --- Basic liveness / anti-spoofing check ---------------------------------
# This is a lightweight, single-signal check — NOT a substitute for
# production-grade liveness detection (e.g. blink challenges, depth
# sensing, or a trained anti-spoofing model). It works by capturing two
# webcam frames a short moment apart and requiring some pixel-level
# change in the face region between them. A live person naturally has
# micro-movements (blinking, breathing, minor head motion) even when
# trying to hold still; a printed photo or a phone showing a static image
# does not, unless the spoofer deliberately moves it. This raises the bar
# against the most casual spoofing attempts without adding new
# dependencies or requiring the student to perform a visible challenge.
#
# ENV_VAR: LIVENESS_CHECK_ENABLED
LIVENESS_CHECK_ENABLED = os.environ.get('LIVENESS_CHECK_ENABLED', '1') == '1'
# ENV_VAR: LIVENESS_FRAME_GAP_MS
# How far apart (in milliseconds) the two captured frames should be.
LIVENESS_FRAME_GAP_MS = _env_int('LIVENESS_FRAME_GAP_MS', 350)
# ENV_VAR: LIVENESS_MIN_MEAN_PIXEL_DIFF
# Minimum mean absolute pixel-intensity difference (0-255 scale) required
# between the two face crops for the frame pair to count as "live". Kept
# low deliberately to avoid rejecting genuine attempts where the person
# held reasonably still — this is meant to catch a fully static
# photo/screen, not to be a strict motion test.
LIVENESS_MIN_MEAN_PIXEL_DIFF = _env_float('LIVENESS_MIN_MEAN_PIXEL_DIFF', 1.5)

# --- Rate limiting & brute-force lockout ----------------------------------
# ENV_VAR: RATE_LIMIT_ENABLED
# Global on/off switch — useful to disable in tests or a trusted internal
# network. Login/attendance endpoints stay protected by account lockout
# below either way.
RATE_LIMIT_ENABLED = os.environ.get('RATE_LIMIT_ENABLED', '1') == '1'
# ENV_VAR: RATE_LIMIT_LOGIN
# Flask-Limiter rate string for login endpoints (admin + student).
RATE_LIMIT_LOGIN = os.environ.get('RATE_LIMIT_LOGIN', '10 per minute')
# ENV_VAR: RATE_LIMIT_ATTENDANCE
# Rate string for the attendance-marking endpoint.
RATE_LIMIT_ATTENDANCE = os.environ.get('RATE_LIMIT_ATTENDANCE', '20 per minute')
# ENV_VAR: RATE_LIMIT_STORAGE_URI
# Flask-Limiter's storage backend. In-memory by default (fine for a
# single-process deployment); point at Redis etc. for multi-process.
RATE_LIMIT_STORAGE_URI = os.environ.get('RATE_LIMIT_STORAGE_URI', 'memory://')

# Account lockout is separate from rate limiting: rate limiting throttles
# by IP regardless of which account is targeted; lockout protects one
# specific account even from a distributed/many-IP attempt, at the cost
# of being a (mild, time-limited) denial-of-service vector against that
# account — the tradeoff most login systems make.
# ENV_VAR: LOCKOUT_MAX_FAILED_ATTEMPTS
LOCKOUT_MAX_FAILED_ATTEMPTS = _env_int('LOCKOUT_MAX_FAILED_ATTEMPTS', 5)
# ENV_VAR: LOCKOUT_DURATION_MINUTES
LOCKOUT_DURATION_MINUTES = _env_int('LOCKOUT_DURATION_MINUTES', 15)

# --- CAPTCHA ---------------------------------------------------------------
# A simple self-hosted, distorted-text image CAPTCHA (PIL-rendered) shown
# on both login forms. This is NOT equivalent to a service like reCAPTCHA
# — it raises the bar against naive scripted login attempts without
# depending on a third-party service or API key, which fits this
# project's scope. A sufficiently motivated attacker with OCR could still
# defeat it.
# ENV_VAR: CAPTCHA_ENABLED
CAPTCHA_ENABLED = os.environ.get('CAPTCHA_ENABLED', '1') == '1'
# ENV_VAR: CAPTCHA_LENGTH
CAPTCHA_LENGTH = _env_int('CAPTCHA_LENGTH', 5)

# --- Password policy ---------------------------------------------------
# Applied at student registration (and available for any future
# admin-account-creation UI). Kept intentionally modest — this is a
# student attendance tool, not a bank — but rules out empty/trivial
# passwords.
# ENV_VAR: PASSWORD_MIN_LENGTH
PASSWORD_MIN_LENGTH = _env_int('PASSWORD_MIN_LENGTH', 8)
# ENV_VAR: PASSWORD_REQUIRE_LETTER_AND_DIGIT
PASSWORD_REQUIRE_LETTER_AND_DIGIT = os.environ.get('PASSWORD_REQUIRE_LETTER_AND_DIGIT', '1') == '1'

# --- Session lifetime ---------------------------------------------------
# ENV_VAR: SESSION_LIFETIME_MINUTES
# How long a login session stays valid without activity. Applies to both
# admin and student sessions.
SESSION_LIFETIME_MINUTES = _env_int('SESSION_LIFETIME_MINUTES', 60)

# --- Request size limits ------------------------------------------------
# ENV_VAR: MAX_CONTENT_LENGTH_MB
# Flask-enforced cap on any single request body (protects against
# oversized-payload resource exhaustion). Applies globally.
MAX_CONTENT_LENGTH_MB = _env_int('MAX_CONTENT_LENGTH_MB', 15)
# ENV_VAR: MAX_REGISTRATION_IMAGES
# Extra, more specific cap on how many face images one registration
# request may include.
MAX_REGISTRATION_IMAGES = _env_int('MAX_REGISTRATION_IMAGES', 20)
# ENV_VAR: MAX_IMAGE_BASE64_CHARS
# Cap on the length of any single base64 image string in a request body
# (registration images, attendance-marking frames). ~4MB of decoded image
# data by default.
MAX_IMAGE_BASE64_CHARS = _env_int('MAX_IMAGE_BASE64_CHARS', 6_000_000)

# --- Eye cascade (for blink-based liveness and face alignment) -----------
# Bundled with opencv-contrib-python — no download needed, unlike the
# frontal-face cascade's path (which is user-configurable above).
# ENV_VAR: EYE_CASCADE_PATH
EYE_CASCADE_PATH = os.environ.get('EYE_CASCADE_PATH') or os.path.join(cv2.data.haarcascades, 'haarcascade_eye.xml')

# --- Enhanced liveness: blink challenge -----------------------------------
# The original two-frame motion check (still active — see
# LIVENESS_MIN_MEAN_PIXEL_DIFF above) is a weak signal on its own: a video
# replay of the real person has genuine motion and would pass it. This
# adds a second, independent signal — requiring a detectable blink
# (an eyes-visible -> eyes-not-visible -> eyes-visible transition) within
# the captured frame burst. A prepared attacker with a video containing a
# blink at the right moment could still defeat this; it is not
# production-grade liveness detection, but it meaningfully raises the bar
# beyond pixel motion alone, at the cost of asking the person to blink
# during capture.
#
# ENV_VAR: LIVENESS_FRAME_COUNT
# How many frames the client captures for one liveness check (spread over
# LIVENESS_FRAME_GAP_MS apart each). More frames = better chance of
# catching a genuine blink, at the cost of a slightly longer capture.
LIVENESS_FRAME_COUNT = _env_int('LIVENESS_FRAME_COUNT', 5)
# ENV_VAR: LIVENESS_REQUIRE_BLINK
# Can be turned off if it causes too many false rejections in your
# environment (low webcam resolution, poor lighting, glasses, etc. all
# degrade Haar eye-cascade reliability) — the motion check still applies
# either way.
LIVENESS_REQUIRE_BLINK = os.environ.get('LIVENESS_REQUIRE_BLINK', '1') == '1'

# --- Multi-face handling ---------------------------------------------------
# ENV_VAR: REJECT_MULTIPLE_FACES
# When more than one face is detected in a recognition/registration
# frame, reject the frame instead of silently picking the largest face.
# Applies to registration, admin live-session recognition, and student
# self-service attendance marking.
REJECT_MULTIPLE_FACES = os.environ.get('REJECT_MULTIPLE_FACES', '1') == '1'

# --- Registration image quality checks ------------------------------------
# Applied to each registration photo's detected face crop before an
# embedding is computed and stored, so a blurry or poorly-lit photo
# doesn't quietly degrade that student's future match accuracy. An image
# that fails is skipped (not stored), not silently accepted.
# ENV_VAR: IMAGE_QUALITY_CHECK_ENABLED
IMAGE_QUALITY_CHECK_ENABLED = os.environ.get('IMAGE_QUALITY_CHECK_ENABLED', '1') == '1'
# ENV_VAR: IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE
# Blur measure (variance of the Laplacian) — lower means blurrier. This
# default is a starting point tuned for a typical webcam face crop, not a
# universal constant; adjust if legitimate photos are getting rejected.
IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE = _env_float('IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE', 40.0)
# ENV_VAR: IMAGE_QUALITY_MIN_BRIGHTNESS
# Mean pixel intensity (0-255) below which an image is considered too dark.
IMAGE_QUALITY_MIN_BRIGHTNESS = _env_float('IMAGE_QUALITY_MIN_BRIGHTNESS', 40.0)
# ENV_VAR: IMAGE_QUALITY_MAX_BRIGHTNESS
# Mean pixel intensity above which an image is considered blown-out/overexposed.
IMAGE_QUALITY_MAX_BRIGHTNESS = _env_float('IMAGE_QUALITY_MAX_BRIGHTNESS', 220.0)

# --- Network-based attendance restriction ("geofencing") ------------------
# Optional IP allowlist for the self-service attendance-marking endpoint
# only. Empty (default) = no restriction. Comma-separated list of IPs
# and/or CIDR ranges, e.g. "10.0.0.0/24,192.168.1.50". Uses
# request.remote_addr — if this app sits behind a reverse proxy, that
# proxy must be configured (e.g. via werkzeug's ProxyFix) so
# remote_addr reflects the real client IP, or every request will appear
# to come from the proxy's own address.
# ENV_VAR: ATTENDANCE_ALLOWED_NETWORKS
ATTENDANCE_ALLOWED_NETWORKS = [
    n.strip() for n in os.environ.get('ATTENDANCE_ALLOWED_NETWORKS', '').split(',') if n.strip()
]

# --- Session scheduling ----------------------------------------------------
# ENV_VAR: ALLOW_OVERLAPPING_SESSIONS
# By default, creating a session whose date+time range overlaps an
# existing session (any subject — a student can't physically be in two
# places at once) is blocked with a clear error. Set to allow it anyway.
ALLOW_OVERLAPPING_SESSIONS = os.environ.get('ALLOW_OVERLAPPING_SESSIONS', '0') == '1'

# --- Pagination --------------------------------------------------------
# ENV_VAR: STUDENTS_PER_PAGE
STUDENTS_PER_PAGE = _env_int('STUDENTS_PER_PAGE', 25)
# ENV_VAR: AUDIT_LOG_PER_PAGE
AUDIT_LOG_PER_PAGE = _env_int('AUDIT_LOG_PER_PAGE', 50)
# ENV_VAR: ATTENDANCE_RECORDS_PER_PAGE
ATTENDANCE_RECORDS_PER_PAGE = _env_int('ATTENDANCE_RECORDS_PER_PAGE', 50)

# --- Bulk student import --------------------------------------------------
# ENV_VAR: MAX_BULK_IMPORT_ROWS
# Upper bound on how many rows a single CSV bulk-import can contain, to
# keep one request bounded.
MAX_BULK_IMPORT_ROWS = _env_int('MAX_BULK_IMPORT_ROWS', 500)

