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
SECRET_KEY_ENV_VAR = 'FLASK_SECRET_KEY'  # nosec B105 -- this is the *name* of the env var to read, not a secret value
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
# ENV_VAR: SESSION_COOKIE_SECURE
# Marks the session cookie Secure (browser will never send it over plain
# HTTP), so it must be off for local http://127.0.0.1 development. Default
# follows BEHIND_REVERSE_PROXY since that's the production-shaped setup
# (see README's "Deploying behind a reverse proxy"), but this has its own
# switch too in case that proxy doesn't terminate TLS (e.g. an internal
# staging box). Explicitly set SESSION_COOKIE_SECURE=1 for any real HTTPS
# deployment, and note it's meaningless without HTTPS actually in front —
# nothing here provides TLS itself (see README's "No HTTPS/TLS" note).
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', '1' if BEHIND_REVERSE_PROXY else '0') == '1'

# --- Attendance rules -----------------------------------------------------
# ENV_VAR: LOW_ATTENDANCE_THRESHOLD_PERCENT
# Below this attendance percentage, a student shows up in the admin
# dashboard's low-attendance alert list, and in /admin/reports' warning
# highlighting.
LOW_ATTENDANCE_THRESHOLD_PERCENT = _env_float('LOW_ATTENDANCE_THRESHOLD_PERCENT', 75)

# ENV_VAR: LATE_ENTRY_ENFORCEMENT_ENABLED
# Off by default — this is a workflow choice each deployment opts into,
# not a security control (unlike ANTI_SPOOF_ENABLED/ACTIVE_LIVENESS_ENABLED
# above), and it's meaningless unless sessions are created with realistic
# real-world start times. When on, self-service attendance marking
# (POST /student/attend/mark) checks elapsed time since the session's
# scheduled start (date + time) and classifies the mark as Present, Late,
# or refuses it outright — see LATE_ENTRY_GRACE_MINUTES/
# LATE_ENTRY_LATE_WINDOW_MINUTES below and _late_entry_status() in app.py.
# Does not affect the admin-supervised kiosk view
# (/admin/session/<id>/recognize) or manual overrides — those remain at
# the admin's discretion.
LATE_ENTRY_ENFORCEMENT_ENABLED = os.environ.get('LATE_ENTRY_ENFORCEMENT_ENABLED', '0') == '1'
# ENV_VAR: LATE_ENTRY_GRACE_MINUTES
# Marking within this many minutes of a session's scheduled start counts
# as on-time ("Present"). Only checked when LATE_ENTRY_ENFORCEMENT_ENABLED.
LATE_ENTRY_GRACE_MINUTES = _env_int('LATE_ENTRY_GRACE_MINUTES', 10)
# ENV_VAR: LATE_ENTRY_LATE_WINDOW_MINUTES
# After the grace period above, marking is still allowed (recorded as
# "Late") for this many additional minutes. Beyond
# LATE_ENTRY_GRACE_MINUTES + LATE_ENTRY_LATE_WINDOW_MINUTES, self-service
# marking is refused outright — the student needs an admin override or an
# approved correction request (see "Attendance correction requests" and
# `clear_attendance_lock`-style admin actions in app.py) to be marked for
# that session at all.
LATE_ENTRY_LATE_WINDOW_MINUTES = _env_int('LATE_ENTRY_LATE_WINDOW_MINUTES', 20)
# ENV_VAR: LATE_COUNTS_AS_PRESENT
# Whether a "Late" mark counts toward a student's attendance percentage
# the same as "Present" (still visually distinguished everywhere it's
# shown) — see _attendance_counts_as_present() in app.py. Many
# institutions count a late arrival as attended-but-flagged rather than
# a miss; set to '0' to instead count Late the same as Absent for
# percentage purposes.
LATE_COUNTS_AS_PRESENT = os.environ.get('LATE_COUNTS_AS_PRESENT', '1') == '1'

# ENV_VAR: ATTENDANCE_EDIT_LOCK_ENABLED
# Once a session is this many days in the past, its attendance records
# become read-only: no further admin manual override
# (/admin/session/<id>/override) and no new student correction request
# for that session — see _is_session_edit_locked() in app.py. Protects
# the historical record from being quietly reshuffled long after the
# fact; there is deliberately no "unlock" endpoint in this project.
ATTENDANCE_EDIT_LOCK_ENABLED = os.environ.get('ATTENDANCE_EDIT_LOCK_ENABLED', '1') == '1'
# ENV_VAR: ATTENDANCE_EDIT_LOCK_DAYS
ATTENDANCE_EDIT_LOCK_DAYS = _env_int('ATTENDANCE_EDIT_LOCK_DAYS', 30)

# ENV_VAR: CORRECTION_REASON_MAX_LENGTH
# Caps how long a student's correction-request reason (and an admin's
# rejection note) can be — plain abuse-of-input-size prevention, same
# spirit as MAX_IMAGE_BASE64_CHARS below.
CORRECTION_REASON_MAX_LENGTH = _env_int('CORRECTION_REASON_MAX_LENGTH', 500)

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

# ENV_VAR: EMBEDDER_MODEL_VERSION
# A human-readable label for the current embedding model/config, stamped
# onto every new face_embeddings row (see app.py's save_face_images()).
# Bump this whenever EMBEDDER_MODEL_PATH or EMBEDDING_INPUT_SIZE changes,
# so /admin/recognition-settings can show which students still have
# embeddings from a retired model version and may need re-enrollment —
# embeddings aren't automatically portable across different model
# weights. Purely a label; changing it does not itself trigger anything.
EMBEDDER_MODEL_VERSION = os.environ.get('EMBEDDER_MODEL_VERSION', 'openface-nn4-small2-v1')

# --- Face enrollment quality / status --------------------------------------
# ENV_VAR: ENROLLMENT_MIN_PHOTOS
# Fewer than this many stored photos -> enrollment status is "Pending"
# rather than "Complete", regardless of their quality — see
# _enrollment_status() in app.py.
ENROLLMENT_MIN_PHOTOS = _env_int('ENROLLMENT_MIN_PHOTOS', 3)
# ENV_VAR: ENROLLMENT_QUALITY_REENROLL_THRESHOLD
# A student's average quality_score (0-100, see _compute_quality_score())
# below this also keeps their status at "Pending" and surfaces the
# re-enrollment reminder — on their own profile, and in the admin
# dashboard's Enrollment Reminders widget.
ENROLLMENT_QUALITY_REENROLL_THRESHOLD = _env_float('ENROLLMENT_QUALITY_REENROLL_THRESHOLD', 50.0)

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

# --- Attendance-marking abuse lockout --------------------------------------
# Separate from the login lockout above, and from RATE_LIMIT_ATTENDANCE:
# rate limiting throttles the endpoint by IP regardless of outcome; this
# tracks repeated attendance_spoof_suspected / attendance_liveness_
# challenge_failed FAILURES specifically for one student account.
# Deliberately does NOT count an ordinary "face not recognized" miss
# (that can happen to a genuine student in bad lighting) — only the two
# anti-proxy checks above. Without this, someone could retry a
# presentation-attack attempt indefinitely: each attempt was already
# written to the audit log, but nothing stopped the retries or flagged
# the account for review. See _register_attendance_security_failure().
# ENV_VAR: ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS
ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS = _env_int('ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 5)
# ENV_VAR: ATTENDANCE_LOCKOUT_DURATION_MINUTES
ATTENDANCE_LOCKOUT_DURATION_MINUTES = _env_int('ATTENDANCE_LOCKOUT_DURATION_MINUTES', 30)

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
EYE_CASCADE_PATH = os.environ.get('EYE_CASCADE_PATH') or os.path.join(
    cv2.data.haarcascades,  # type: ignore[attr-defined]  # bundled with opencv-contrib-python; cv2's stubs omit the `data` submodule
    'haarcascade_eye.xml',
)

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

# --- Active liveness challenge (anti-proxy / anti-replay) -------------------
# See face_security.py. The passive checks above (motion + blink) are, by
# their own admission, not production-grade anti-spoofing: a video replay
# of the real person prepared in advance would satisfy both, since it
# already contains natural motion and a blink somewhere in it. This raises
# the bar specifically against a pre-recorded/looped video (the realistic
# "proxy attendance" attack — someone plays a clip of the absent student
# instead of appearing themselves): the server picks ONE random challenge
# per attempt (right before capture starts) and the person must perform
# that specific action within the captured burst. A fixed pre-recorded
# clip would need to happen to already contain the randomly-chosen action
# at the right moment, for every one of several possible challenges, which
# a simple loop can't guarantee. Still not unbeatable (a live deepfake or a
# very well-prepared attacker could adapt), but meaningfully raises the
# cost over motion+blink alone.
# ENV_VAR: ACTIVE_LIVENESS_ENABLED
ACTIVE_LIVENESS_ENABLED = os.environ.get('ACTIVE_LIVENESS_ENABLED', '1') == '1'
# ENV_VAR: ACTIVE_LIVENESS_CHALLENGE_TYPES
# Comma-separated subset of: blink, head_turn, head_nod. A challenge is
# picked at random from this list for each attempt.
ACTIVE_LIVENESS_CHALLENGE_TYPES = [
    c.strip() for c in os.environ.get('ACTIVE_LIVENESS_CHALLENGE_TYPES', 'blink,head_turn,head_nod').split(',') if c.strip()
]
# ENV_VAR: ACTIVE_LIVENESS_CHALLENGE_TTL_SECONDS
# A challenge must be answered within this many seconds of being issued
# (and is single-use regardless) — long enough for a genuine capture
# burst, short enough that a challenge can't be issued once and then
# answered later with a separately-prepared clip.
ACTIVE_LIVENESS_CHALLENGE_TTL_SECONDS = _env_int('ACTIVE_LIVENESS_CHALLENGE_TTL_SECONDS', 30)
# ENV_VAR: ACTIVE_LIVENESS_HEAD_TURN_MIN_SHIFT_RATIO
# For the head_turn challenge: the detected face box's horizontal center
# must shift by at least this fraction of the face's own width between
# frames to count as a genuine turn. Deliberately direction-agnostic
# (either left or right satisfies it) — the raw captured frame is not
# mirrored the way the on-screen preview is (see student_attend.html),
# so "must turn specifically left" vs "right" would depend on camera
# setup in a way that's confusing to specify correctly; requiring
# noticeable motion in either direction still requires genuine 3D head
# movement a flat photo/screen can't produce on command.
ACTIVE_LIVENESS_HEAD_TURN_MIN_SHIFT_RATIO = _env_float('ACTIVE_LIVENESS_HEAD_TURN_MIN_SHIFT_RATIO', 0.12)
# ENV_VAR: ACTIVE_LIVENESS_HEAD_NOD_MIN_SHIFT_RATIO
# Same idea as above, vertical axis, for the head_nod challenge.
ACTIVE_LIVENESS_HEAD_NOD_MIN_SHIFT_RATIO = _env_float('ACTIVE_LIVENESS_HEAD_NOD_MIN_SHIFT_RATIO', 0.10)

# --- Presentation-attack ("spoof") detection --------------------------------
# See face_security.py's compute_screen_replay_score(). A heuristic, not a
# trained anti-spoofing model (none was reachable from this project's
# build environment — same constraint noted in align_face_crop()'s
# docstring) — it looks for the periodic pixel-grid pattern (moire) a
# phone/tablet/monitor screen produces when photographed by another
# camera at close range, which a real face's skin texture doesn't have.
# Meaningful against "hold a phone playing a video up to the camera"; not
# a defense against a high-quality printed photo, which has no screen
# grid to detect (the existing IMAGE_QUALITY_* blur/brightness gate,
# applied here too now — see student_mark_attendance() — is the closest
# thing to a defense against that, since a re-photographed print is often
# noticeably softer than a direct live capture).
# ENV_VAR: ANTI_SPOOF_ENABLED
ANTI_SPOOF_ENABLED = os.environ.get('ANTI_SPOOF_ENABLED', '1') == '1'
# ENV_VAR: ANTI_SPOOF_MAX_PERIODICITY_RATIO
# How much stronger the single strongest high-frequency spectral peak is
# allowed to be than the surrounding band's average, before a frame is
# flagged as a likely screen replay. Lower = stricter (more false
# positives on e.g. patterned backgrounds or fabric visible near the
# face); higher = more permissive. Tuned conservatively (permissive) by
# default specifically to avoid rejecting genuine attendance attempts —
# ordinary JPEG compression alone (even of an otherwise perfectly
# genuine photo) can push this into the high-teens in pathological cases
# (very noisy/low-quality source images), so the default sits well above
# that with real margin before actual screen-replay territory (typically
# in the hundreds for a photographed screen's pixel grid) — tighten it if
# screen-replay attempts are a known problem in your deployment and false
# positives aren't.
ANTI_SPOOF_MAX_PERIODICITY_RATIO = _env_float('ANTI_SPOOF_MAX_PERIODICITY_RATIO', 30.0)

# --- Ensemble embedding matching (accuracy) ---------------------------------
# ENV_VAR: MATCH_TOP_K
# How many of a candidate's stored embeddings to average together when
# scoring a match, instead of just taking the single closest one. Default
# 1 = exactly today's behavior (single best embedding) — this is opt-in,
# not a silent accuracy change, because averaging generally produces
# lower similarity scores than a single best match, which can shift how
# often the existing RECOGNIZE_MATCH_THRESHOLD / MARK_ATTENDANCE_MATCH_THRESHOLD
# / DUPLICATE_FACE_MATCH_THRESHOLD trigger — raising this above 1 usually
# means those thresholds need re-tuning (typically lowering slightly) for
# your own registered photos. What it buys you: a single unusually good
# (for an impostor) or unusually poor (for the genuine student) stored
# embedding matters less, since the score reflects several of that
# person's photos rather than whichever one happens to be closest.
MATCH_TOP_K = _env_int('MATCH_TOP_K', 1)

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

# ENV_VAR: SESSION_AUTO_CLOSE_ENABLED
# Once a session's attendance window has fully elapsed (see
# _session_attendance_window_minutes() in app.py — a session's own
# attendance_window_minutes override if set, else
# LATE_ENTRY_GRACE_MINUTES + LATE_ENTRY_LATE_WINDOW_MINUTES), it's
# automatically moved from 'active' to 'completed' the next time a
# session-listing or marking route runs (there's no background scheduler
# in this project — see README). Independent of
# LATE_ENTRY_ENFORCEMENT_ENABLED: that toggle only controls whether
# self-service marking classifies Present vs Late vs refused: this one
# is about session bookkeeping (not leaving sessions "active" forever),
# and is on by default.
SESSION_AUTO_CLOSE_ENABLED = os.environ.get('SESSION_AUTO_CLOSE_ENABLED', '1') == '1'

# --- Pagination --------------------------------------------------------
# ENV_VAR: STUDENTS_PER_PAGE
STUDENTS_PER_PAGE = _env_int('STUDENTS_PER_PAGE', 25)
# ENV_VAR: AUDIT_LOG_PER_PAGE
AUDIT_LOG_PER_PAGE = _env_int('AUDIT_LOG_PER_PAGE', 50)

# --- Security-event dashboard ----------------------------------------------
# How far back the admin dashboard's "Security Alerts" widget looks when
# grouping and counting security-relevant audit_log events (spoof
# attempts, liveness-challenge failures, lockouts) per student — see
# SECURITY_AUDIT_ACTIONS in app.py and admin_dashboard().
# ENV_VAR: SECURITY_ALERT_WINDOW_DAYS
SECURITY_ALERT_WINDOW_DAYS = _env_int('SECURITY_ALERT_WINDOW_DAYS', 7)

# --- Security monitoring: fingerprinting, risk scoring, escalation --------
# ENV_VAR: SECURITY_RISK_WINDOW_DAYS
# How far back _compute_risk_score() looks when tallying a student's
# weighted security events (spoof attempts, suspicious devices, etc.)
# into a 0-100 risk score — see RISK_EVENT_WEIGHTS in app.py.
SECURITY_RISK_WINDOW_DAYS = _env_int('SECURITY_RISK_WINDOW_DAYS', 30)
# ENV_VAR: SECURITY_RISK_ESCALATION_THRESHOLD
# A risk score at or above this (0-100) automatically escalates the
# student (students.security_escalated) — which blocks their attendance
# marking the same way the attendance-abuse lockout does, until an admin
# reviews and clears it. Runtime-configurable from
# /admin/security-settings — see RUNTIME_CONFIGURABLE_SETTINGS in app.py.
SECURITY_RISK_ESCALATION_THRESHOLD = _env_int('SECURITY_RISK_ESCALATION_THRESHOLD', 60)
# ENV_VAR: CONCURRENT_SESSION_WINDOW_MINUTES
# Two active login sessions for the same student, both last-active
# within this many minutes of each other, are flagged as a possible
# concurrent/shared-account login — see _check_concurrent_session().
CONCURRENT_SESSION_WINDOW_MINUTES = _env_int('CONCURRENT_SESSION_WINDOW_MINUTES', 15)
# ENV_VAR: NETWORK_CHANGE_WINDOW_MINUTES
# A login from an IP on a different network than the student's last
# login, within this many minutes of that last login, is flagged as a
# network change; within NETWORK_CHANGE_IMPOSSIBLE_MINUTES specifically,
# it's flagged at the higher "impossible location" severity instead (see
# _check_network_change() in app.py). This is an IP-address heuristic
# only — there's no bundled GeoIP database, so it cannot measure actual
# physical distance or confirm true impossibility, only "a meaningfully
# different network, suspiciously soon after the last one."
NETWORK_CHANGE_WINDOW_MINUTES = _env_int('NETWORK_CHANGE_WINDOW_MINUTES', 60)
# ENV_VAR: NETWORK_CHANGE_IMPOSSIBLE_MINUTES
NETWORK_CHANGE_IMPOSSIBLE_MINUTES = _env_int('NETWORK_CHANGE_IMPOSSIBLE_MINUTES', 5)

# --- Observability ----------------------------------------------------------
# ENV_VAR: METRICS_ENABLED
# Exposes /metrics in Prometheus text exposition format (request counts
# and latency by route, audit-event counts by action, attendance-mark
# outcomes) — in-memory counters, hand-rolled rather than depending on
# the prometheus_client package. See app.py's "In-process metrics"
# comment for the multi-worker caveat.
METRICS_ENABLED = os.environ.get('METRICS_ENABLED', '1') == '1'
# ENV_VAR: METRICS_AUTH_TOKEN
# If set, /metrics requires this exact value as either a `?token=`
# query parameter or an `Authorization: Bearer <token>` header — request
# counts and latencies aren't secret, but they do reveal usage patterns
# (e.g. how many students are actively marking attendance right now),
# so a deployment exposed to the internet may want to gate the endpoint.
# Unset (the default) leaves /metrics open to anyone who can reach it,
# same as most self-hosted Prometheus exporters.
METRICS_AUTH_TOKEN = os.environ.get('METRICS_AUTH_TOKEN') or None

# ENV_VAR: REALTIME_UPDATES_ENABLED
# Whether the admin session-monitor page (/admin/session/<id>) uses
# long-polling for near-real-time updates when a student marks
# attendance, instead of relying solely on its periodic (interval) poll.
# See app.py's session_updates() docstring for exactly how this works
# and its trade-offs under gunicorn's default multi-worker sync setup —
# briefly: each open admin viewer ties up one worker for up to
# REALTIME_LONGPOLL_TIMEOUT_SECONDS at a time, repeating, so don't enable
# this with more concurrent session-monitor viewers open than you have
# spare GUNICORN_WORKERS. Turning it off falls back to plain interval
# polling only (still correct, just less immediate).
REALTIME_UPDATES_ENABLED = os.environ.get('REALTIME_UPDATES_ENABLED', '1') == '1'
# ENV_VAR: REALTIME_LONGPOLL_TIMEOUT_SECONDS
REALTIME_LONGPOLL_TIMEOUT_SECONDS = _env_int('REALTIME_LONGPOLL_TIMEOUT_SECONDS', 8)

# ENV_VAR: ATTENDANCE_RECORDS_PER_PAGE
ATTENDANCE_RECORDS_PER_PAGE = _env_int('ATTENDANCE_RECORDS_PER_PAGE', 50)

# --- Bulk student import --------------------------------------------------
# ENV_VAR: MAX_BULK_IMPORT_ROWS
# Upper bound on how many rows a single CSV bulk-import can contain, to
# keep one request bounded.
MAX_BULK_IMPORT_ROWS = _env_int('MAX_BULK_IMPORT_ROWS', 500)

# --- Logging -------------------------------------------------------------
# See logging_config.py. Every log line (this app's own, werkzeug's
# request log, and — under gunicorn — gunicorn's access/error logs) is
# emitted as one JSON object per line by default, tagged with the
# request_id of whichever request triggered it (see app.py's
# before_request/after_request hooks).
# ENV_VAR: LOG_LEVEL
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
# ENV_VAR: LOG_FORMAT
# 'json' (default, recommended for production/log shippers) or 'plain'
# (a human-readable single-line format, easier to read in a local
# terminal during development).
LOG_FORMAT = os.environ.get('LOG_FORMAT', 'json')

# --- Error alerting / crash reporting (Sentry) -----------------------------
# Entirely opt-in: nothing is sent anywhere unless SENTRY_DSN is set. See
# error_reporting.py and the README's "Error Alerting" section.
# ENV_VAR: SENTRY_DSN
SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
# ENV_VAR: SENTRY_ENVIRONMENT
# Tags every event so Sentry can separate e.g. staging noise from
# production alerts. Defaults to something obviously-a-placeholder so an
# operator notices and sets it, rather than everything silently landing
# under "production".
SENTRY_ENVIRONMENT = os.environ.get('SENTRY_ENVIRONMENT', 'unspecified')
# ENV_VAR: SENTRY_TRACES_SAMPLE_RATE
# Fraction (0.0-1.0) of requests to also capture full performance traces
# for, independent of error reporting (which always happens regardless of
# this value). 0 (default) = error reporting only, no tracing overhead.
SENTRY_TRACES_SAMPLE_RATE = _env_float('SENTRY_TRACES_SAMPLE_RATE', 0.0)

# --- Backups ---------------------------------------------------------------
# See backup.py / restore.py and the README's "Backup & Restore" section.
# ENV_VAR: BACKUP_DIR
BACKUP_DIR = os.environ.get('BACKUP_DIR') or os.path.join(BASE_DIR, 'backups')
# ENV_VAR: BACKUP_RETENTION_COUNT
# backup.py deletes older backups beyond this count each time it runs
# (keeping the N most recent), so a scheduled/automated backup job
# doesn't grow BACKUP_DIR without bound. Set to 0 to keep every backup
# ever made (no automatic deletion).
BACKUP_RETENTION_COUNT = _env_int('BACKUP_RETENTION_COUNT', 14)


# --- Base URL (for links sent in emails) -----------------------------------
# ENV_VAR: APP_BASE_URL
# Used to build absolute links (password-reset links, OAuth redirect
# construction fallback) inside outgoing emails, since a background-ish
# request context can't always reliably infer the right public host/scheme
# itself (e.g. behind a reverse proxy that isn't configured — see
# BEHIND_REVERSE_PROXY above). No trailing slash. Empty by default, in
# which case notifications.py falls back to Flask's request-derived
# url_for(..., _external=True) when called from within a request.
APP_BASE_URL = os.environ.get('APP_BASE_URL', '').rstrip('/')

# --- Email notifications (SMTP) --------------------------------------------
# Entirely opt-in, same pattern as Sentry above: nothing is sent anywhere
# unless EMAIL_NOTIFICATIONS_ENABLED is on AND SMTP_HOST is set. See
# notifications.py and the README's "Notifications" section. Every
# public function in notifications.py degrades to a safe no-op otherwise,
# so the rest of the app never needs to check "is email configured?"
# before calling them.
# ENV_VAR: EMAIL_NOTIFICATIONS_ENABLED
EMAIL_NOTIFICATIONS_ENABLED = os.environ.get('EMAIL_NOTIFICATIONS_ENABLED', '0') == '1'
# ENV_VAR: SMTP_HOST
SMTP_HOST = os.environ.get('SMTP_HOST', '')
# ENV_VAR: SMTP_PORT
SMTP_PORT = _env_int('SMTP_PORT', 587)
# ENV_VAR: SMTP_USERNAME
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
# ENV_VAR: SMTP_PASSWORD
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')  # nosec B105 -- env var name, not a secret value
# ENV_VAR: SMTP_USE_TLS
# STARTTLS on an ordinary connection (the common case for port 587).
SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', '1') == '1'
# ENV_VAR: SMTP_FROM_EMAIL
SMTP_FROM_EMAIL = os.environ.get('SMTP_FROM_EMAIL', 'no-reply@attendance.local')
# ENV_VAR: SMTP_FROM_NAME
SMTP_FROM_NAME = os.environ.get('SMTP_FROM_NAME', 'Attendance System')
# ENV_VAR: SMTP_TIMEOUT_SECONDS
SMTP_TIMEOUT_SECONDS = _env_int('SMTP_TIMEOUT_SECONDS', 10)

# --- SMS notifications (Twilio) ---------------------------------------------
# Also opt-in. Uses Twilio's plain HTTPS REST API directly (via the
# `requests` library, already a transitive dependency) rather than adding
# the full twilio SDK as a hard dependency — matches this project's general
# preference for no third-party dependency where a straightforward HTTPS
# call suffices (see the /metrics endpoint's same reasoning re:
# prometheus_client). No-op unless SMS_NOTIFICATIONS_ENABLED and all three
# TWILIO_* values are set.
# ENV_VAR: SMS_NOTIFICATIONS_ENABLED
SMS_NOTIFICATIONS_ENABLED = os.environ.get('SMS_NOTIFICATIONS_ENABLED', '0') == '1'
# ENV_VAR: TWILIO_ACCOUNT_SID
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
# ENV_VAR: TWILIO_AUTH_TOKEN
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')  # nosec B105 -- env var name, not a secret value
# ENV_VAR: TWILIO_FROM_NUMBER
TWILIO_FROM_NUMBER = os.environ.get('TWILIO_FROM_NUMBER', '')

# --- Notification triggers ---------------------------------------------------
# ENV_VAR: NOTIFY_ON_ATTENDANCE_MARK
# Sends a "your attendance was marked" email/SMS right after a successful
# self-service Present/Late mark (see student_mark_attendance() in app.py).
# Still governed by each student's own notify_email/notify_sms preference
# (set on /student/profile) and by whether they have an email/phone on
# file at all — this is a global on/off switch on top of that, not a
# replacement for it.
NOTIFY_ON_ATTENDANCE_MARK = os.environ.get('NOTIFY_ON_ATTENDANCE_MARK', '1') == '1'
# ENV_VAR: NOTIFY_ON_LOW_ATTENDANCE
# Sends a low-attendance alert the first time (see
# LOW_ATTENDANCE_ALERT_COOLDOWN_HOURS below) a student's overall
# percentage is found to be below LOW_ATTENDANCE_THRESHOLD_PERCENT at the
# moment a new attendance mark is recorded.
NOTIFY_ON_LOW_ATTENDANCE = os.environ.get('NOTIFY_ON_LOW_ATTENDANCE', '1') == '1'
# ENV_VAR: LOW_ATTENDANCE_ALERT_COOLDOWN_HOURS
# Minimum gap between two low-attendance alerts to the same student, so
# a student who is repeatedly below threshold doesn't get one email per
# attendance mark — see students.last_low_attendance_alert_at and
# _maybe_notify_low_attendance() in app.py.
LOW_ATTENDANCE_ALERT_COOLDOWN_HOURS = _env_int('LOW_ATTENDANCE_ALERT_COOLDOWN_HOURS', 24)

# --- Self-service password reset (email) -------------------------------------
# Student-only (there is still no email/SMTP-based recovery for the admin
# account itself — see reset_admin_password.py and the README's "Account
# Recovery" section for why). Requires EMAIL_NOTIFICATIONS_ENABLED and SMTP
# to be configured; the request endpoint still behaves the same way
# either way (a generic "if that account exists, an email was sent"
# response, to avoid leaking which roll numbers/emails are registered) —
# see request_student_password_reset() in app.py.
# ENV_VAR: PASSWORD_RESET_ENABLED
PASSWORD_RESET_ENABLED = os.environ.get('PASSWORD_RESET_ENABLED', '1') == '1'
# ENV_VAR: PASSWORD_RESET_TOKEN_TTL_MINUTES
PASSWORD_RESET_TOKEN_TTL_MINUTES = _env_int('PASSWORD_RESET_TOKEN_TTL_MINUTES', 30)
# ENV_VAR: RATE_LIMIT_PASSWORD_RESET
RATE_LIMIT_PASSWORD_RESET = os.environ.get('RATE_LIMIT_PASSWORD_RESET', '5 per minute')

# --- SSO / institutional login (Google OAuth 2.0 / OIDC) ---------------------
# Entirely opt-in and student-only for now (see README's "SSO /
# Institutional Login" section for the reasoning and its honest limits).
# Uses Authlib's Flask integration. Off unless OAUTH_GOOGLE_ENABLED is set
# AND both client credentials are present — see configure_oauth() in
# app.py, which follows the same lazy-import, safe-no-op pattern as
# error_reporting.init_sentry().
#
# An OAuth login only ever logs in an EXISTING student account, matched by
# email (students.email must already be set on the account, e.g. from
# /student/profile) — it deliberately does not auto-create accounts, since
# this project's registration flow also captures face-enrollment data that
# an OAuth login can't provide.
# ENV_VAR: OAUTH_GOOGLE_ENABLED
OAUTH_GOOGLE_ENABLED = os.environ.get('OAUTH_GOOGLE_ENABLED', '0') == '1'
# ENV_VAR: OAUTH_GOOGLE_CLIENT_ID
OAUTH_GOOGLE_CLIENT_ID = os.environ.get('OAUTH_GOOGLE_CLIENT_ID', '')
# ENV_VAR: OAUTH_GOOGLE_CLIENT_SECRET
OAUTH_GOOGLE_CLIENT_SECRET = os.environ.get('OAUTH_GOOGLE_CLIENT_SECRET', '')  # nosec B105 -- env var name, not a secret value
# ENV_VAR: OAUTH_GOOGLE_DISCOVERY_URL
OAUTH_GOOGLE_DISCOVERY_URL = os.environ.get(
    'OAUTH_GOOGLE_DISCOVERY_URL', 'https://accounts.google.com/.well-known/openid-configuration'
)

# --- SSO account linking (registration + profile) ---------------------------
# See README's "SSO / Institutional Login" section, "Linking Google to an
# Account" subsection. No separate enable flag — governed by
# OAUTH_GOOGLE_ENABLED above; these routes simply don't do anything
# useful if that's off (same as /auth/google/login itself).

# --- Admin self-service password reset (email) -------------------------------
# Deliberately more conservative than the student version above in
# several ways — see the README's "Self-Service Password Reset" ->
# "Admin Accounts" subsection for the full reasoning:
#   1. Off by default (PASSWORD_RESET_ENABLED for students defaults on;
#      this defaults off) — an operator has to consciously accept the
#      added attack surface on the single admin account.
#   2. An admin's recovery_email can ONLY be set via the
#      set_admin_recovery_email.py CLI script — there is no HTTP route
#      to set or change it, so a web-app-level compromise (a stolen
#      admin session, a CSRF gap, etc.) can never redirect where the
#      reset link goes.
#   3. Shorter token lifetime and a stricter rate limit than the student
#      flow.
#   4. Every request/completion additionally raises a Sentry
#      capture_message() (if configured — see error_reporting.py) since
#      an admin-account reset attempt is a higher-value security signal
#      than a student one.
# ENV_VAR: ADMIN_PASSWORD_RESET_ENABLED
ADMIN_PASSWORD_RESET_ENABLED = os.environ.get('ADMIN_PASSWORD_RESET_ENABLED', '0') == '1'
# ENV_VAR: ADMIN_PASSWORD_RESET_TOKEN_TTL_MINUTES
ADMIN_PASSWORD_RESET_TOKEN_TTL_MINUTES = _env_int('ADMIN_PASSWORD_RESET_TOKEN_TTL_MINUTES', 15)
# ENV_VAR: RATE_LIMIT_ADMIN_PASSWORD_RESET
RATE_LIMIT_ADMIN_PASSWORD_RESET = os.environ.get('RATE_LIMIT_ADMIN_PASSWORD_RESET', '3 per hour')
