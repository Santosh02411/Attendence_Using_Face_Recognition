# Attendance Using Face Recognition

A comprehensive attendance management system powered by face recognition technology. This web application provides an automated, secure, and efficient way to track student attendance using facial recognition algorithms.

## 📑 Table of Contents

- [Features](#-features)
- [Technology Stack](#️-technology-stack)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Project Structure](#-project-structure)
- [How It Works](#-how-it-works)
- [Anti-Proxy / Face Recognition Security](#️-anti-proxy--face-recognition-security)
- [Session Management](#-session-management)
- [Attendance Workflow](#-attendance-workflow)
- [Face Enrollment & Biometric Management](#-face-enrollment--biometric-management)
- [Security Monitoring](#️-security-monitoring)
- [Notifications](#-notifications)
- [Self-Service Password Reset](#-self-service-password-reset)
- [SSO / Institutional Login](#-sso--institutional-login)
- [Configuration](#-configuration)
- [Database Schema](#-database-schema)
- [User Interface](#-user-interface)
- [Security Features](#-security-features)
- [Troubleshooting](#-troubleshooting)
- [Running Tests](#-running-tests)
- [Metrics](#-metrics)
- [Deployment](#-deployment)
- [Advanced Usage](#-advanced-usage)
- [Performance Optimization](#-performance-optimization)
- [Future Enhancements](#-future-enhancements)
- [Contributing](#-contributing)
- [License](#-license)

## 🚀 Features

- **Face Recognition Attendance**: Automated attendance marking using facial recognition
- **Admin Dashboard**: Complete control over sessions, students, and attendance records, with quick-action shortcuts, live stats, Low Attendance Alerts, and a Security Alerts widget for repeated spoof/liveness events
- **Student Portal**: Self-service registration, attendance marking, attendance history (with Present/Late/Absent status and a Request Correction action per session), and a profile page with account details and self-service password change
- **Session Management**: Create and manage attendance sessions for different subjects, with automatic overlap detection so two sessions can't double-book the same time window, a full status lifecycle (Scheduled → Active → Completed, or → Cancelled), rescheduling, and optional per-session attendance-window/grace-period/network overrides and student-group (branch/semester) assignment — see "Session Management" below
- **Attendance Workflow**: Present/Absent/Late status, a late-entry rule with a configurable grace period, admin manual editing (add/remove/change, with a reason) and an attendance freeze after a configurable window, a student correction-request workflow with admin approve/reject, and filterable subject/date-range/semester reports with percentage warnings and a trend chart — see "Attendance Workflow" below
- **Face Enrollment & Biometric Management**: Per-photo enrollment quality scores, an enrollment status (Not Enrolled/Pending/Complete) with automatic re-enrollment reminders, individual photo removal and a full re-enrollment workflow, embedding model versioning, and admin-panel recognition threshold configuration — see "Face Enrollment & Biometric Management" below
- **Security Monitoring**: Device fingerprinting and suspicious-device detection, concurrent-session and network-change/impossible-location detection, per-student risk scoring with automatic escalation, admin security notifications, Low/Medium/High/Critical severity levels, a security dashboard with trends, and configurable security policies — see "Security Monitoring" below
- **Notifications**: Opt-in email (SMTP) and SMS (Twilio) alerts when a student's attendance is marked and when their overall attendance drops below the low-attendance threshold — see "Notifications" below
- **Self-Service Password Reset**: A student who has an email on file can reset their own forgotten password via an emailed, single-use, expiring link — no admin needed — see "Self-Service Password Reset" below
- **SSO / Institutional Login**: Optional "Sign in with Google" for students, linked to an existing account by email — see "SSO / Institutional Login" below
- **Real-time Recognition**: Live face detection and recognition during attendance sessions
- **Bulk Student Import**: Onboard a whole class at once via CSV upload (name/roll number/branch/semester, with auto-generated temporary passwords); each student adds their own face photos afterward
- **Export Functionality**: Export attendance records, session rosters, and attendance reports as CSV files
- **Admin Account Management**: Add/remove admin accounts, self-service password change, audit log of admin actions with a Security Events filter
- **Pagination**: Student lists, attendance records, and the audit log all page through results instead of rendering everything at once
- **Web Camera Capture**: Uses the browser's webcam (via `getUserMedia`) for face capture — works with whichever camera the browser selects by default; no in-app multi-camera picker
- **Dark Mode**: Toggleable, persisted per-browser

## 🛠️ Technology Stack

- **Backend**: Flask (Python Web Framework)
- **Face Recognition**: Deep-learning embeddings (OpenFace CNN via OpenCV's `cv2.dnn`) + cosine similarity
- **Database**: SQLite (for both application data and face recognition data)
- **Frontend**: HTML5, CSS3 (hand-written, no framework), vanilla JavaScript
- **Image Processing**: PIL (Python Imaging Library) — also used to render the login CAPTCHA image
- **Face Detection**: Haar Cascade Classifiers
- **Security**: Flask-WTF (CSRF), Flask-Limiter (rate limiting)

## 📋 Prerequisites

- Python 3.9 or higher
- Webcam or camera device
- Git (for cloning the repository)

## 🚀 Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Santosh02411/Attendance_Using_Face_Recognition.git
   cd Attendance_Using_Face_Recognition
   ```

2. **Create and activate virtual environment**
   ```bash
   python -m venv venv
   
   # On Windows
   venv\Scripts\activate
   
   # On macOS/Linux
   source venv/bin/activate
   ```

3. **Install required dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure your environment (recommended)**
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and set at least `FLASK_SECRET_KEY` and `DEFAULT_ADMIN_PASSWORD`. All other settings — paths, attendance thresholds, face-recognition confidence thresholds and detection tuning — have working defaults and only need to be touched if you want to change behavior; see `config.py` for the full list. `.env` is gitignored, so your real values never get committed.

   If you'd rather not use a `.env` file, you can set the same variables directly in your shell:
   ```bash
   # macOS/Linux
   export FLASK_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

   # Windows (PowerShell)
   $env:FLASK_SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
   ```
   If skipped entirely, the app generates a temporary key for that run only — logins won't persist across restarts, and this should never be relied on in production.

5. **Run the application**
   ```bash
   python app.py
   ```
   By default this binds to `127.0.0.1:5000` with debug mode off. For local development with auto-reload and network access from other devices:
   ```bash
   FLASK_DEBUG=1 FLASK_RUN_HOST=0.0.0.0 python app.py
   ```

6. **Access the application**
   - Open your web browser and navigate to `http://localhost:5000`
   - Default admin credentials (only if `DEFAULT_ADMIN_PASSWORD` wasn't set — see step 4):
     - Username: `admin`
     - Password: `admin123`
   - Change this immediately after first login — either from
     `/admin/settings` ("Change My Password") or by setting
     `DEFAULT_ADMIN_PASSWORD` before the first run

## 📁 Project Structure

```
Attendance_Using_Face_Recognition/
├── app.py                          # Main Flask application
├── config.py                       # Centralized paths, thresholds, and detection tuning
├── db_migrations.py                 # Runs Alembic migrations against database/app.db — see "Database Migrations"
├── alembic.ini                     # Alembic configuration (script location; sqlalchemy.url set at runtime)
├── migrations/                     # Alembic migration environment
│   ├── env.py                     # Alembic runtime environment
│   ├── script.py.mako              # Template for new migration files
│   └── versions/                   # Ordered migration scripts (schema history)
├── backup.py                       # Creates a timestamped backup of the DBs + face images — see "Backup & Restore"
├── restore.py                      # Restores a backup.py archive back into place
├── logging_config.py               # Structured (JSON) logging + request-id plumbing — see "Structured Logging"
├── error_reporting.py              # Optional Sentry integration — see "Error Alerting"
├── notifications.py                 # Optional email/SMS alerts — see "Notifications"
├── face_security.py                 # Active liveness challenges + anti-spoof heuristics — see "Anti-Proxy / Face Recognition Security"
├── wsgi.py                         # Production WSGI entrypoint (gunicorn/waitress) — see "Deployment"
├── gunicorn.conf.py                # Gunicorn server tuning (workers, timeouts, JSON logging)
├── healthcheck.py                  # Standalone HTTP healthcheck probe, used by Docker HEALTHCHECK
├── migrate_embeddings.py           # One-time backfill for pre-embedding registrations
├── reset_admin_password.py         # CLI recovery tool if the admin account gets locked out
├── set_admin_recovery_email.py     # CLI-only way to set an admin's password-reset recovery email
├── requirements.txt                # Python dependencies
├── requirements-dev.txt            # + pytest, ruff, mypy, pip-audit — for tests/lint/type-check/security scan
├── requirements-prod.txt           # + gunicorn, sentry-sdk, for running in production (Linux/macOS/Docker)
├── pyproject.toml                  # ruff + mypy configuration — see "Lint & type-check gate"
├── pytest.ini                      # pytest configuration
├── .env.example                    # Template for local environment overrides
├── Dockerfile                      # Production container image (gunicorn + non-root user)
├── docker-compose.yml              # Local/production container run, with persistent volumes
├── .dockerignore                   # Files excluded from the Docker build context
├── .github/workflows/ci.yml        # GitHub Actions: tests, lint/type-check, and dependency scan on every push/PR
├── .github/dependabot.yml          # Weekly pip + GitHub Actions dependency update PRs
├── haarcascade_frontalface_default.xml  # Face detection cascade file
├── models/                         # Pretrained face embedding model
│   ├── openface_nn4.small2.v1.t7  # OpenFace CNN (128-d embeddings)
│   └── README.md                  # Model source, checksum, license
├── Datasets/                       # Student face crop images (gitignored)
├── database/                       # SQLite databases, incl. face_embeddings and audit_log (gitignored)
├── templates/                      # HTML templates
│   ├── base.html                 # Base template (nav, theme toggle, flash messages)
│   ├── _pagination.html          # Shared pagination-controls macro
│   ├── index.html                # Landing page
│   ├── login.html                # Admin login page (+ CAPTCHA, forgot-password link when enabled)
│   ├── admin_forgot_password.html # Admin: request a password-reset email
│   ├── admin_reset_password.html  # Admin: set a new password from an emailed link
│   ├── admin_dashboard.html      # Admin dashboard
│   ├── admin_sessions.html       # Session management (+ overlap detection)
│   ├── admin_students.html       # Student management (search, pagination, reset, delete)
│   ├── admin_bulk_import.html    # CSV bulk student import + results summary
│   ├── admin_attendance.html     # Paginated all-attendance-records view
│   ├── admin_settings.html       # Admin account management + self password change
│   ├── audit_log.html            # Paginated audit log viewer
│   ├── attendance_session.html   # Live attendance session (admin)
│   ├── student_register.html     # Student registration
│   ├── student_register_face.html # Add face photos to an existing (e.g. bulk-imported) account
│   ├── student_login.html        # Student login (+ CAPTCHA, forgot-password link, optional Google sign-in)
│   ├── student_forgot_password.html # Request a password-reset email
│   ├── student_reset_password.html  # Set a new password from an emailed link
│   ├── student_profile.html      # Student account details + self password change
│   ├── student_attend.html       # Student self-service attendance marking
│   └── student_history.html      # Attendance history
├── static/                        # CSS and shared JS (styles.css, app.js)
├── tests/                          # pytest test suite (464 tests) — see "Running Tests"
└── archive/                       # Superseded standalone CLI scripts, kept
    └── legacy_scripts/            # for reference only — not used by app.py
```

## 🎯 How It Works

### 1. Student Registration
- Students register with their details (name, roll number, branch, semester)
- Multiple face images are captured
- An embedding is computed and stored for each one immediately — there's
  no separate training step to wait for

### 2. Admin Session Management
- Admins create attendance sessions for specific subjects
- Sessions can be scheduled with date and time
- Sessions can be activated/deactivated as needed

### 3. Attendance Marking
- **Admin Mode**: Live face recognition during active sessions
- **Student Mode**: Self-service attendance marking with face verification
- Real-time face detection and recognition
- Automatic attendance recording with timestamps

### 4. Face Recognition Process
- Haar cascade locates a face in the frame; if more than one face is
  found, the frame is rejected rather than silently picking the largest
  (see `REJECT_MULTIPLE_FACES`)
- The detected face is aligned using eye positions (from the bundled Haar
  eye cascade) before embedding — see "Detection: still Haar cascade, now
  with alignment" in `models/README.md` for why, and the honest limits of
  this approach vs. a modern learned detector
- A pretrained OpenFace CNN (`models/openface_nn4.small2.v1.t7`, loaded via
  `cv2.dnn`) turns that aligned crop into a 128-dimensional embedding vector
- Matching is cosine similarity between embeddings, not raw pixel
  comparison — this is far more tolerant of lighting and angle changes
  than the LBPH approach this project started with
- Self-service attendance marking does 1:1 verification (compare only
  against the logged-in student's own stored embeddings); the admin live
  session view does 1:N identification (compare against everyone)
- A multi-signal liveness pipeline runs before matching: passive checks
  (pixel motion across a burst of frames, plus — if enabled — a
  detectable blink), a randomized active challenge (blink / turn your
  head / nod — a different one picked at random each attempt), and a
  heuristic screen/print replay detector — see "Anti-Proxy / Face
  Recognition Security" below for what each layer catches and its honest
  limits
- Registration photos are quality-checked (blur, brightness) before being
  turned into an embedding — a bad photo is skipped, not silently stored
  (see `IMAGE_QUALITY_CHECK_ENABLED`); the same check now also runs on
  self-service attendance-marking captures, not just registration
- Self-service attendance marking can optionally be restricted to an IP
  allowlist (see `ATTENDANCE_ALLOWED_NETWORKS`) — not GPS-based
  geofencing, but a practical network-level restriction

## 🛡️ Anti-Proxy / Face Recognition Security

"Proxy attendance" — someone marking attendance on behalf of a person who
isn't actually there — is the central threat model for this feature set.
Three independent layers work together against it, each catching a
different kind of attempt:

### 1. Active liveness challenge (the main defense against a video replay)

The older passive check (motion across a frame burst, plus a detectable
blink) has an honest weakness: a **pre-recorded video** of the real
person, played back to the camera, already contains natural motion and
probably a blink somewhere in it — so it can pass a purely passive check.

To close that gap, the server now picks **one random challenge** right
before each capture starts — `blink`, `head_turn`, or `head_nod` (see
`ACTIVE_LIVENESS_CHALLENGE_TYPES`) — and the captured burst must actually
show that specific action happening:

1. `GET /student/liveness-challenge` picks a random challenge, stores it
   server-side (session-bound, single-use, expires after
   `ACTIVE_LIVENESS_CHALLENGE_TTL_SECONDS`), and returns a token plus an
   on-screen prompt ("Slowly turn your head to one side and back", etc.)
2. The student performs the action while `student_attend.html` captures
   the frame burst.
3. `POST /student/attend/mark` is sent the same token; the server looks
   up what it actually issued (never trusts a client-reported challenge
   type), verifies the requested motion happened
   (`face_security.verify_challenge_response`), and — regardless of the
   outcome — consumes the token so it can never be reused.

A fixed pre-recorded clip would need to happen to contain the
randomly-chosen action at the right moment, for whichever of several
possible challenges gets picked, which a simple loop can't guarantee.
`head_turn`/`head_nod` are verified by tracking how much the detected
face box's center moves between frames — deliberately direction-agnostic
(either left or right satisfies `head_turn`) since the raw captured frame
isn't mirrored the way the on-screen preview is, so specifying "must
turn left" vs. "right" would depend on camera setup in a confusing way.

Toggle with `ACTIVE_LIVENESS_ENABLED` (default on). See `face_security.py`
for the implementation and `config.py`'s `ACTIVE_LIVENESS_*` comments for
the full reasoning and tuning knobs.

### 2. Screen/print replay detection (the main defense against "hold a phone up to the camera")

`face_security.compute_screen_replay_score()` is a heuristic, frequency-
domain check: a phone/tablet/monitor screen, photographed by another
camera, produces a periodic pixel-grid pattern ("moire") that shows up as
unusually strong, spatially periodic peaks in the image's 2D FFT
magnitude spectrum. A real face's spectrum is comparatively smooth. The
score is the strongest such peak's energy divided by the surrounding
band's average — high means "looks like a screen".

Worth knowing about this specific heuristic: ordinary **JPEG compression
itself** introduces periodic 8x8 block artifacts, which could otherwise
look like screen-grid periodicity — the check deliberately excludes the
pure horizontal/vertical frequency axes (where JPEG blocking
concentrates almost entirely) from its analysis, and the default
threshold (`ANTI_SPOOF_MAX_PERIODICITY_RATIO`, 30.0) was calibrated with
real synthetic tests to sit well above what even heavily-compressed
ordinary photos produce, while staying far below genuine screen-replay
territory (typically in the hundreds) — see the comment on that setting
in `config.py` and `tests/test_face_security.py` for the numbers behind
that choice.

Applied at three points: student self-service attendance marking,
registration (a spoofed photo is rejected rather than silently added to
someone's face gallery), and the admin kiosk recognition endpoint.
Toggle with `ANTI_SPOOF_ENABLED` (default on).

**Honest limits**: this catches a screen replay specifically — it has no
real signal against a good-quality *printed* photo (no screen grid to
detect). The registration/attendance-marking blur-brightness quality
gate (`IMAGE_QUALITY_CHECK_ENABLED`, now applied at attendance-marking
time too, not just registration) is the closest thing to a defense
there, since a re-photographed print is often noticeably softer than a
direct live capture — but it's not a dedicated print-detection check.

### 3. Motion-based liveness for the admin kiosk view

The admin-supervised live session view (`/admin/session/<id>/recognize`)
previously ran recognition against a single static image with no
liveness signal of any kind — a photo held up to the camera would have
been recognized. It now optionally accepts a second, previous frame
(`image_prev`) from the same live feed and requires actual pixel motion
between the two before running recognition at all — the same underlying
`check_liveness()` used elsewhere, applied here as a lighter-weight check
appropriate for an admin-supervised setting rather than the fuller active
challenge used for unsupervised self-service marking.

### 4. Ensemble embedding matching (accuracy, not just anti-proxy)

`find_best_match()` can average a candidate's top-`MATCH_TOP_K` closest
stored embeddings instead of just taking the single closest one —
reducing how much one unusually good (for an impostor) or unusually poor
(for the genuine student) stored photo affects the outcome. Off by
default (`MATCH_TOP_K=1`, an exact no-op matching prior behavior) because
averaging generally shifts similarity scores downward, which typically
needs the match thresholds re-tuned for your own registered photos — see
the setting's comment in `config.py`.

### 5. Attendance-marking abuse lockout (closing the "retry forever" gap)

Every spoof-suspected or failed-liveness-challenge attempt above was
already written to the audit log — but on its own, that's just a record;
nothing stopped someone from retrying indefinitely. `student_id`-scoped
lockout closes that gap: `ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS`
consecutive failures (default 5) lock that account out of marking
attendance for `ATTENDANCE_LOCKOUT_DURATION_MINUTES` (default 30), with
its own `attendance_security_lockout` audit event so it's clearly
flagged rather than blending into the rest of the log. Deliberately
narrow: an ordinary "face not recognized" miss (bad lighting, a stale
photo) never counts — only the two anti-proxy checks above do, and a
genuine successful mark resets the counter. An admin can lift a lockout
early from `/admin/students` ("Clear Lock"), since a flaky camera can
trip this too, not just an actual proxy attempt.

The admin dashboard's **Security Alerts** widget and the audit log's
**Security Events** filter (`/admin/audit-log?filter=security`) both
build on this — see "Security Features" below.

## 📅 Session Management

Beyond creating sessions and detecting time-overlap conflicts:

- **Status lifecycle** — every session is `scheduled` → `active` →
  `completed`, or → `cancelled`. This sits alongside the original
  `active` flag (which stays the source of truth for "can attendance be
  marked right now") rather than replacing it, so it adds richer states
  (distinguishing "hasn't started yet" from "already finished", and
  cancellation as its own explicitly-blocked state) without changing
  how anything that already read `active` behaves.
- **Cancellation** (`/admin/session/<id>/cancel`) — records a reason,
  blocks attendance marking with its own explicit message, and frees up
  its time slot for overlap detection (a cancelled session never
  happened, so it can't conflict with a new one).
- **Rescheduling** (`/admin/session/<id>/reschedule`) — changes a
  session's date/time, reusing the same overlap check as creating a new
  one, and revives a cancelled session back to `scheduled`. A
  `completed` session can't be rescheduled — attendance has already
  happened for it; create a new session instead.
- **Per-session attendance window & grace period** — `Attendance window
  (minutes)` and `Grace period (minutes)`, set per-session under
  "Advanced" when creating one, override the global
  `LATE_ENTRY_LATE_WINDOW_MINUTES`/`LATE_ENTRY_GRACE_MINUTES` for just
  that session. Unset (the default) falls back to the global config.
- **Per-session network restriction** — a session's own `Allowed
  networks` list *replaces* the global `ATTENDANCE_ALLOWED_NETWORKS` for
  that one session (stricter or looser, either direction), rather than
  adding to it.
- **Student-group assignment** — `Restrict to branch` / `Restrict to
  semester` limit a session to one group of students. Neither set (the
  default) keeps a session open to everyone, unchanged from before this
  existed. Enforced server-side at marking time (not just hidden from
  the session list) and reflected in the per-session attendance
  summary's totals.
- **Attendance summary** — the session detail page and its
  auto-refreshing `/admin/session/<id>/attendance-data` endpoint show
  Present/Late/Absent counts (scoped to the session's assigned group, if
  any) plus a Failed Attempts count pulled from the audit log's
  spoof-suspected/liveness-challenge-failed events for that session.
- **Real-time updates** — the session-monitor page long-polls
  `/admin/session/<id>/updates` for near-instant refreshes when a
  student marks attendance, rather than waiting for its periodic poll.
  Deliberately long-polling instead of a persistent SSE/websocket
  stream: this app runs under gunicorn's sync worker model (see
  `gunicorn.conf.py`), where an indefinitely-open stream would tie up an
  entire worker for as long as an admin keeps that tab open. Each
  long-poll cycle instead blocks for at most
  `REALTIME_LONGPOLL_TIMEOUT_SECONDS` (default 8) and always returns
  fresh data regardless of why it woke up, so correctness doesn't depend
  on the update signal actually being seen — though with multiple
  gunicorn worker *processes* (the default), a change handled by one
  worker won't wake a long-poll parked in another; that request just
  waits out its timeout and returns current data anyway (correct, just
  not instant). A slower interval poll remains as a safety net, and
  `REALTIME_UPDATES_ENABLED=0` disables long-polling entirely if
  preferred. Don't open more concurrent session-monitor tabs than you
  have spare `GUNICORN_WORKERS`.
- **Automatic closing after the attendance window** — once an `active`
  session's attendance window has fully elapsed, it's moved to
  `completed` automatically (`SESSION_AUTO_CLOSE_ENABLED`, on by
  default) — checked both lazily on the hot marking path (so a stale
  session is caught immediately) and swept in bulk whenever a
  session-listing page loads. There's no background scheduler in this
  project (see "What this project does *not* provide" below), so this
  is deliberately request-triggered rather than time-triggered.

## 📈 Attendance Workflow

- **Present / Absent / Late status** — a self-service mark can be
  `Present` or `Late` (see the late-entry rule below); `Absent` is shown
  wherever a student has no attendance row for a past session, without
  needing a row inserted for every miss.
- **Late-entry rule** — off by default (`LATE_ENTRY_ENFORCEMENT_ENABLED`,
  since it only makes sense once sessions carry real-world start times):
  when on, marking within `LATE_ENTRY_GRACE_MINUTES` of a session's
  scheduled start is `Present`; within the following
  `LATE_ENTRY_LATE_WINDOW_MINUTES` it's `Late`; beyond that, self-service
  marking is refused outright (the student needs an admin override or an
  approved correction request). Whether `Late` counts the same as
  `Present` toward a percentage is `LATE_COUNTS_AS_PRESENT` (default on).
- **Admin manual editing** (`/admin/session/<id>/override`) — set a
  student's status to Present/Late/Absent, or clear their record back to
  "Not Marked", always with an optional reason recorded in both the
  attendance row's note and the audit log.
- **Attendance freeze** — once a session is more than
  `ATTENDANCE_EDIT_LOCK_DAYS` old (default 30), its attendance record
  becomes read-only: no further admin override, and no new student
  correction request for it. There's deliberately no "unlock" endpoint.
- **Correction requests** — a student who believes their attendance for
  a session is wrong can file a request (with a reason) from
  `/student/history`; an admin approves or rejects it from
  `/admin/attendance/corrections`. Approving applies the requested
  status the same way a manual override would. Blocked by the same
  freeze window as a direct override.
- **Reports** (`/admin/reports`) — one filterable report (subject,
  semester, and/or date range) rather than four separate pages, showing
  each student's session count / Present / Late / Absent / percentage,
  with rows below `LOW_ATTENDANCE_THRESHOLD_PERCENT` highlighted, a
  daily/weekly/monthly attendance-rate trend chart, and a CSV export
  that respects the same filters.
- **Duplicate-marking prevention** — enforced at the application layer
  (an early check before any face-processing work runs), hardened with
  an atomic `INSERT ... WHERE NOT EXISTS` to close the narrow race
  between that check and the actual insert. No hard database `UNIQUE`
  constraint on `(student_id, session_id)`, since the `attendance` table
  is also used, deliberately, as an append-only per-mark log in a couple
  of places.

## 🧬 Face Enrollment & Biometric Management

- **Enrollment quality score** — every registered photo gets a 0-100
  heuristic score (`_compute_quality_score()`) from the same sharpness
  (Laplacian variance) and exposure (mean brightness) signals as the
  pass/fail quality gate at capture time, but continuous rather than
  binary. Shown per-photo on both the student's and admin's face-photos
  pages.
- **Enrollment status** — every student is `Not Enrolled` (zero photos),
  `Pending` (fewer than `ENROLLMENT_MIN_PHOTOS`, or below-threshold
  average quality — see `ENROLLMENT_QUALITY_REENROLL_THRESHOLD`), or
  `Complete`. Shown on the admin student list, the admin/student
  face-photos pages, and the student's own profile.
- **Remove individual photos** — `/student/face-photos` (self-service)
  and `/admin/student/<id>/face-photos` (admin) list every registered
  photo with a thumbnail, its quality score, and a Remove button —
  deletes that one `face_embeddings` row and its underlying image file,
  distinct from wiping everything.
- **Face re-enrollment/update workflow** — `/student/reenroll` (or the
  admin's "Reset Face Data") wipes ALL of a student's existing face data
  and sends them to recapture from scratch, for when adding a few more
  photos to an already-weak gallery isn't enough (a materially changed
  appearance, or persistently poor quality).
- **Automatic re-enrollment reminders** — no email/SMS in this project
  (a documented trade-off), so the reminder is in-app: a banner on the
  student's own profile once their status is `Pending`, and an
  **Enrollment Reminders** widget on the admin dashboard listing
  affected students, both driven by the same enrollment-status logic.
- **Embedding versioning** — every stored embedding is tagged with
  `EMBEDDER_MODEL_VERSION` (a label you bump when you change
  `EMBEDDER_MODEL_PATH` or the input size). `/admin/recognition-settings`
  shows a breakdown of stored embeddings by model version, flagging any
  that don't match the currently-configured model as `Outdated` — since
  embeddings from different model weights aren't directly comparable.
- **Recognition model/configuration management & threshold
  configuration from the admin panel** — `/admin/recognition-settings`
  shows the effective model config (path, input size, ensemble
  `MATCH_TOP_K`) and feature toggles (read-only — those touch enough
  other code paths that they stay environment-configured), plus five
  numeric thresholds (`RECOGNIZE_MATCH_THRESHOLD`,
  `MARK_ATTENDANCE_MATCH_THRESHOLD`, `DUPLICATE_FACE_MATCH_THRESHOLD`,
  `ANTI_SPOOF_MAX_PERIODICITY_RATIO`, `IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE`)
  that ARE editable right there, saved to a small `app_settings` table
  and taking effect immediately — no restart needed. Each can be reset
  back to its `.env`/`config.py` default individually.
- **Recognition confidence reporting** — every Present/Late mark
  (self-service and kiosk) now records the match similarity score
  (`attendance.confidence`), surfaced in the admin attendance-records
  browser and in both CSV exports.
- **Unknown-face detection/reporting** — when the admin-supervised kiosk
  (`/admin/session/<id>/recognize`) detects a face but it doesn't match
  anyone in the gallery well enough, that's logged as a distinct
  `unknown_face_detected` audit event (with the best similarity it did
  find) — different from a plain detection failure, and picked up by
  the audit log's Security Events filter.
- **Face enrollment audit trail** — registration, adding photos, removing
  a photo, re-enrollment, admin face-data resets, and recognition-
  settings changes are all logged to the audit log with their actor.

## 🕵️ Security Monitoring

Everything here is heuristic signal built entirely from data this app
already has (IPs, user agents, its own audit log) — there is no
third-party fingerprinting or GeoIP service involved, and that's stated
plainly in each feature below rather than oversold.

- **Device/browser fingerprinting** — a lightweight, self-hosted
  fingerprint computed client-side (`getDeviceFingerprint()` in
  `static/app.js`, hashing user agent/screen/timezone/language signals),
  sent with student login. Not a commercial-grade fingerprinting
  library, and best-effort: a client that blocks it just means this
  signal is skipped for that login, nothing fails.
- **Suspicious-device detection** — a login from a fingerprint never
  seen before for that student, when they already have at least one
  other known device on file, logs `suspicious_device_detected` (their
  very first-ever device is never flagged) and raises a High-severity
  notification.
- **Concurrent-session detection** — a second active login (no logout
  recorded, last active within `CONCURRENT_SESSION_WINDOW_MINUTES`) from
  a different device or IP than an already-active session for the same
  student logs `concurrent_session_detected` and a High-severity
  notification. Re-logging in from the same device isn't flagged.
- **Impossible-location/network-change detection** — an IP-address
  heuristic only (there's no bundled GeoIP database, so it cannot
  measure true physical distance): a login from a different network
  than the student's last one, within `NETWORK_CHANGE_WINDOW_MINUTES`,
  logs `network_change_detected` (Medium); within the tighter
  `NETWORK_CHANGE_IMPOSSIBLE_MINUTES` specifically, it's
  `impossible_location_suspected` (Critical) instead, with its own
  notification.
- **IP/device history per student** — `/admin/student/<id>/security-history`
  lists every device fingerprint seen for them (first/last seen, times
  seen), their recent login sessions with IPs, and their recent
  security-relevant audit events.
- **Security risk score per student** — a 0-100 score
  (`_compute_risk_score()`) from weighted counts of a student's security
  events (spoof attempts, suspicious devices, concurrent sessions,
  network changes, etc.) over `SECURITY_RISK_WINDOW_DAYS` — a rough
  signal for review, not a certainty. Shown on the security dashboard's
  leaderboard and the student's security-history page, with a
  point-by-point breakdown.
- **Automatic escalation of high-risk accounts** — a score at or above
  `SECURITY_RISK_ESCALATION_THRESHOLD` automatically escalates the
  student, blocking their attendance marking the same way the
  spoof/liveness lockout does (see "Anti-Proxy / Face Recognition
  Security"), until an admin reviews and explicitly clears it
  (`/admin/student/<id>/clear-escalation`) — a broader, longer-lived
  flag than that narrower lockout counter.
- **Admin security alert notifications** — no email/SMS in this project
  (a documented trade-off), so the higher-severity events above
  (suspicious device, concurrent session, impossible location,
  escalation) raise an in-app notification, shown on the security
  dashboard as a read/unread feed.
- **Security event severity levels** — Low/Medium/High/Critical,
  computed by action name (`event_severity()`) rather than stored per
  row, so every event — including ones logged before this feature
  existed — gets a consistent severity. Shown throughout the audit log,
  security-history pages, and the security dashboard.
- **Security dashboard with trends** — `/admin/security-dashboard`:
  daily security-event counts stacked by severity, a risk-score
  leaderboard (top 10), and the notification feed, all over the same
  `SECURITY_ALERT_WINDOW_DAYS` window as the existing dashboard widget.
- **Repeated failed-attempt analytics** — the trend chart above and each
  student's risk-score breakdown are, together, this: a view of how
  failed/suspicious attempts cluster over time and per student, without
  a separate dedicated analytics page.
- **Configurable security policies from the admin panel** —
  `/admin/security-settings` edits `SECURITY_RISK_ESCALATION_THRESHOLD`,
  `CONCURRENT_SESSION_WINDOW_MINUTES`, `NETWORK_CHANGE_WINDOW_MINUTES`,
  and `NETWORK_CHANGE_IMPOSSIBLE_MINUTES` at runtime — the same
  DB-backed override mechanism as `/admin/recognition-settings`, no
  restart needed, each individually resettable to its `.env` default.

## 🔔 Notifications

Optional email (SMTP, via the standard library's `smtplib`) and SMS (via
Twilio's plain HTTPS REST API — no `twilio` SDK dependency needed) alerts,
handled by `notifications.py` following the same opt-in, safe-no-op
pattern as Sentry error alerting (see `error_reporting.py`): nothing is
sent anywhere unless explicitly enabled and configured.

- **Attendance-marked notification** — right after a successful
  self-service Present/Late mark, `notify_attendance_marked()` emails/
  texts the student, if `NOTIFY_ON_ATTENDANCE_MARK` is on (default) and
  the student has an email/phone on file with their own
  `notify_email`/`notify_sms` preference also on (set from
  `/student/profile`).
- **Low-attendance alert** — after each mark, `_maybe_notify_low_attendance()`
  in `app.py` checks the student's overall percentage; if it's below
  `LOW_ATTENDANCE_THRESHOLD_PERCENT`, it sends an alert — but no more
  than once per `LOW_ATTENDANCE_ALERT_COOLDOWN_HOURS` (tracked in
  `students.last_low_attendance_alert_at`), so a persistently-low-
  attendance student isn't emailed after every single subsequent mark.
- **Per-student opt-out** — `notify_email`/`notify_sms` (default on
  once an email/phone is added) are editable from `/student/profile`,
  independent of the two global switches above.
- **Where email/phone come from** — optional fields at registration
  (`/student/register`), optional CSV columns at bulk import
  (`email`/`phone_number`), or added later from `/student/profile`.
  Neither is required for anything else in the app to work.

Configure via `EMAIL_NOTIFICATIONS_ENABLED` + `SMTP_HOST`/`SMTP_PORT`/
`SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_USE_TLS`/`SMTP_FROM_EMAIL`/
`SMTP_FROM_NAME`, and/or `SMS_NOTIFICATIONS_ENABLED` +
`TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_FROM_NUMBER` — see
`.env.example`. A failed send is logged and swallowed; it never breaks
the attendance-marking (or password-reset) request that triggered it.

## 🔑 Self-Service Password Reset

A student who has added an email to their account (see "Notifications"
above) can reset their own forgotten password without an admin, from
`/student/forgot-password`:

1. The student submits their roll number and the email on file. The
   response message is the same generic
   *"if that account exists and has an email on file, a link was sent"*
   regardless of whether they actually matched — this is deliberate, to
   avoid letting the form be used to enumerate registered roll numbers
   or which accounts have an email set (the audit log still records an
   unmatched attempt, just without naming which roll number it was for).
2. On a match, a random token (`secrets.token_urlsafe(32)`) is generated;
   only its SHA-256 hash is stored in `password_reset_tokens`, alongside
   an expiry (`PASSWORD_RESET_TOKEN_TTL_MINUTES`, default 30) — the same
   "never store the usable secret itself" principle as password hashing.
3. `notifications.send_password_reset_email()` emails a link containing
   the raw token: `/student/reset-password/<token>`.
4. Visiting that link (while unexpired and unused) lets the student set
   a new password, subject to the same `validate_password_strength()`
   policy as registration. The token is marked used immediately —
   reusing the link afterward is rejected the same way an expired one is.

This student flow is enabled by default (`PASSWORD_RESET_ENABLED`);
rate-limited per IP via `RATE_LIMIT_PASSWORD_RESET` (default
5/minute) the same way login is. If `EMAIL_NOTIFICATIONS_ENABLED`/SMTP
aren't configured, the request endpoint still "succeeds" with the same
generic message (nothing here should reveal whether email is even set
up), but no email actually goes out — a token is issued but the
student never receives its link, so in practice this feature needs
email configured to be useful end-to-end.

### Admin Accounts

Admin accounts now have a self-service reset too (`/admin/forgot-password`
/ `/admin/reset-password/<token>`), but it's deliberately more locked
down than the student version, since the admin account is a much
higher-value target:

1. **Off by default** — `ADMIN_PASSWORD_RESET_ENABLED` defaults to `0`,
   unlike the student flow's default-on `PASSWORD_RESET_ENABLED`. An
   operator has to consciously accept the added attack surface.
2. **The recovery email can't be set from the web app at all.** There is
   no HTTP route to set or change `admins.recovery_email` — the only
   way is `python set_admin_recovery_email.py <username> <email>` from
   the server, mirroring `reset_admin_password.py`'s own reasoning: if
   a web route could change that address, a stolen admin session, a
   CSRF gap, or an XSS bug could quietly redirect future reset links to
   an attacker, defeating the point of a recovery mechanism.
3. **Shorter token lifetime and a stricter rate limit** —
   `ADMIN_PASSWORD_RESET_TOKEN_TTL_MINUTES` (default 15, vs. the
   student flow's 30) and `RATE_LIMIT_ADMIN_PASSWORD_RESET` (default
   3/hour, vs. 5/minute).
4. **Extra security signal** — every request and completion also calls
   `error_reporting.capture_message()` (routed to Sentry if
   `SENTRY_DSN` is configured), in addition to the audit log, since an
   admin-account reset attempt is a higher-value signal worth surfacing
   through the same channel as other alerting.

`python reset_admin_password.py <username>` from the server remains
available as always and doesn't require any of the above to be
configured — it's the fallback if email/SMTP isn't set up at all, or if
an admin's recovery email was never set.

## 🔐 SSO / Institutional Login

Optional "Sign in with Google" for students, via
[Authlib](https://docs.authlib.org/)'s OAuth 2.0 / OIDC client
(`configure_oauth()` in `app.py`, following the same lazy-import,
safe-no-op pattern as `error_reporting.init_sentry()` — an unconfigured
deployment just doesn't show the button and its routes redirect back to
the login page with a flash message, rather than erroring).

**Deliberately does not auto-create accounts.** A Google sign-in only
ever logs into an **existing** student account, matched by email:

1. `/auth/google/login` redirects to Google's consent screen.
2. `/auth/google/callback` receives the authorization code, exchanges it
   for the signed-in user's email and stable Google account id (`sub`).
3. If a student record's `oauth_google_sub` already matches, that's an
   instant match. Otherwise, it looks for a student whose `email` column
   (see "Notifications" above) matches, case-insensitively — and if
   found, links `oauth_google_sub` to that account for next time.
4. No match on either → the student is told to register normally (which
   captures face-enrollment data an OAuth login can't provide) or to add
   this email to their existing account from `/student/profile` first.

This keeps registration's face-capture step mandatory for every account
while still letting an institution's Google Workspace accounts serve as
a second, no-separate-password way to log into an *already-registered*
account. The same account-lockout check as password login applies
(`_is_locked_out`), and successful OAuth logins run through the same
security-monitoring pipeline as a password login (concurrent-session,
network-change detection, risk-based escalation — see "Security
Monitoring" above), minus the device-fingerprint signal (there's no
client-side JS fingerprint step in the OAuth redirect flow).

Configure via `OAUTH_GOOGLE_ENABLED=1`, `OAUTH_GOOGLE_CLIENT_ID`, and
`OAUTH_GOOGLE_CLIENT_SECRET` (from a Google Cloud Console OAuth 2.0
"Web application" client) — the authorized redirect URI to register with
Google is `<your-deployment-url>/auth/google/callback`. Only Google is
implemented; a SAML/generic-OIDC/institutional-LDAP path is not (see
"Future Enhancements" below).

### Linking Google to an Account

A student can connect a Google account to their existing record two ways:

- **From `/student/profile`** — a "Connect Google Account" button (shown
  whenever `OAUTH_GOOGLE_ENABLED` and no `oauth_google_sub` is set yet)
  starts the same OAuth flow via `/auth/google/link`, but with `intent`
  set to `link` rather than `login` in the session — the callback links
  the Google account to *this already-logged-in* student directly,
  without needing an email match.
- **Right after registering** — since registration itself doesn't log
  the student in, `student_register()` stashes the new student's id in
  `session['pending_oauth_link_student_id']`; the success screen on
  `/student/register` offers a "Connect Google Account (optional)"
  button that uses that stashed id the same way, then logs the student
  straight in on success (skipping the "type your new password again"
  step).

Either path rejects the attempt if that Google account is already
linked to a *different* student (`students.oauth_google_sub` is
effectively unique in practice, even though the DB index itself allows
duplicate `NULL`s — see the index comment in
`0009_add_notifications_and_sso.py`).

## 🔧 Configuration

### Database Setup
The application automatically creates and initializes SQLite databases:
- `app.db`: Main application data (students, sessions, attendance, and
  `face_embeddings` — one row per registered face image)
- `FaceBase.db`: Face recognition profiles

### Face Recognition Settings
See `config.py` (and `.env.example`) for every tunable value. The
important ones:
- **Match thresholds**: cosine similarity, higher = stricter
  (`RECOGNIZE_MATCH_THRESHOLD`, `DUPLICATE_FACE_MATCH_THRESHOLD`,
  `MARK_ATTENDANCE_MATCH_THRESHOLD` — defaults 0.5 / 0.6 / 0.55)
- **Face detection parameters**: still Haar cascade, tuned per call site
  (registration / recognition / attendance marking)
- **Embedding model**: `EMBEDDER_MODEL_PATH`, see `models/README.md` for
  what it is and its license

There is no "training" step or model file to regenerate — registering a
student stores their embeddings directly; deleting one removes them. If
you're upgrading from an older version of this project that used LBPH,
run `python migrate_embeddings.py` once to backfill embeddings for
already-registered students from their saved photos.

### Security Settings
Also in `config.py` / `.env.example`:
- **Rate limiting**: `RATE_LIMIT_LOGIN`, `RATE_LIMIT_ATTENDANCE`,
  `RATE_LIMIT_ENABLED`, `RATE_LIMIT_STORAGE_URI`
- **Account lockout**: `LOCKOUT_MAX_FAILED_ATTEMPTS` (default 5),
  `LOCKOUT_DURATION_MINUTES` (default 15)
- **Attendance-marking abuse lockout**:
  `ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS` (default 5),
  `ATTENDANCE_LOCKOUT_DURATION_MINUTES` (default 30) — see "Anti-Proxy /
  Face Recognition Security"
- **Security-event dashboard**: `SECURITY_ALERT_WINDOW_DAYS` (default 7)
- **Security monitoring**: `SECURITY_RISK_WINDOW_DAYS` (default 30),
  `SECURITY_RISK_ESCALATION_THRESHOLD` (default 60),
  `CONCURRENT_SESSION_WINDOW_MINUTES` (default 15),
  `NETWORK_CHANGE_WINDOW_MINUTES` (default 60),
  `NETWORK_CHANGE_IMPOSSIBLE_MINUTES` (default 5) — see "Security
  Monitoring". The last four are also editable at runtime from
  `/admin/security-settings`.
- **Observability**: `METRICS_ENABLED` (default on), `METRICS_AUTH_TOKEN`
  (unset by default) — see "Metrics"
- **CAPTCHA**: `CAPTCHA_ENABLED`, `CAPTCHA_LENGTH`
- **Password policy**: `PASSWORD_MIN_LENGTH`,
  `PASSWORD_REQUIRE_LETTER_AND_DIGIT`
- **Session lifetime**: `SESSION_LIFETIME_MINUTES` (default 60)
- **Request size limits**: `MAX_CONTENT_LENGTH_MB`,
  `MAX_REGISTRATION_IMAGES`, `MAX_IMAGE_BASE64_CHARS`
- **Liveness/blink**: `LIVENESS_FRAME_COUNT` (default 5),
  `LIVENESS_REQUIRE_BLINK`
- **Active liveness challenge**: `ACTIVE_LIVENESS_ENABLED`,
  `ACTIVE_LIVENESS_CHALLENGE_TYPES`, `ACTIVE_LIVENESS_CHALLENGE_TTL_SECONDS`,
  `ACTIVE_LIVENESS_HEAD_TURN_MIN_SHIFT_RATIO`,
  `ACTIVE_LIVENESS_HEAD_NOD_MIN_SHIFT_RATIO` — see "Anti-Proxy / Face
  Recognition Security" below
- **Anti-spoof (screen/print replay) detection**: `ANTI_SPOOF_ENABLED`,
  `ANTI_SPOOF_MAX_PERIODICITY_RATIO`
- **Ensemble embedding matching**: `MATCH_TOP_K` (default 1 = unchanged
  single-best-embedding behavior; see the setting's comment in
  `config.py` before raising it)
- **Multi-face handling**: `REJECT_MULTIPLE_FACES`
- **Registration photo quality**: `IMAGE_QUALITY_CHECK_ENABLED`,
  `IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE`, `IMAGE_QUALITY_MIN_BRIGHTNESS`,
  `IMAGE_QUALITY_MAX_BRIGHTNESS`
- **Network restriction**: `ATTENDANCE_ALLOWED_NETWORKS` (comma-separated
  IPs/CIDR ranges, empty = unrestricted)

### Scheduling & UX Settings
Also in `config.py` / `.env.example`:
- **Session overlap checking**: `ALLOW_OVERLAPPING_SESSIONS` (default
  off — overlapping sessions are blocked)
- **Automatic session closing**: `SESSION_AUTO_CLOSE_ENABLED` (default
  on) — see "Session Management"
- **Real-time updates**: `REALTIME_UPDATES_ENABLED` (default on),
  `REALTIME_LONGPOLL_TIMEOUT_SECONDS` (default 8) — see "Session
  Management"
- **Attendance rules**: `LATE_ENTRY_ENFORCEMENT_ENABLED` (default off),
  `LATE_ENTRY_GRACE_MINUTES` (10), `LATE_ENTRY_LATE_WINDOW_MINUTES` (20),
  `LATE_COUNTS_AS_PRESENT` (default on), `ATTENDANCE_EDIT_LOCK_ENABLED`
  (default on), `ATTENDANCE_EDIT_LOCK_DAYS` (30),
  `CORRECTION_REASON_MAX_LENGTH` (500) — see "Attendance Workflow"
- **Pagination**: `STUDENTS_PER_PAGE` (25), `AUDIT_LOG_PER_PAGE` (50),
  `ATTENDANCE_RECORDS_PER_PAGE` (50)
- **Bulk import**: `MAX_BULK_IMPORT_ROWS` (500 rows per CSV)
- **Face enrollment**: `EMBEDDER_MODEL_VERSION`, `ENROLLMENT_MIN_PHOTOS`
  (3), `ENROLLMENT_QUALITY_REENROLL_THRESHOLD` (50.0) — see "Face
  Enrollment & Biometric Management". `RECOGNIZE_MATCH_THRESHOLD`,
  `MARK_ATTENDANCE_MATCH_THRESHOLD`, `DUPLICATE_FACE_MATCH_THRESHOLD`,
  `ANTI_SPOOF_MAX_PERIODICITY_RATIO`, and
  `IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE` (all listed above) can
  additionally be overridden at runtime from
  `/admin/recognition-settings`, which takes precedence over these.

### Operations Settings
Also in `config.py` / `.env.example` — see "Structured Logging", "Error
Alerting", and "Backup & Restore" below for the full picture:
- **Logging**: `LOG_LEVEL` (default `INFO`), `LOG_FORMAT` (`json` default
  or `plain`)
- **Error alerting**: `SENTRY_DSN` (unset = disabled), `SENTRY_ENVIRONMENT`,
  `SENTRY_TRACES_SAMPLE_RATE`
- **Backups**: `BACKUP_DIR` (default `./backups`), `BACKUP_RETENTION_COUNT`
  (default 14, `0` = keep forever)

### Notifications, Password Reset & SSO Settings
Also in `config.py` / `.env.example` — see "Notifications",
"Self-Service Password Reset", and "SSO / Institutional Login" above:
- **Base URL for emailed links**: `APP_BASE_URL`
- **Email (SMTP)**: `EMAIL_NOTIFICATIONS_ENABLED` (default off),
  `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `SMTP_USE_TLS` (default on), `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`,
  `SMTP_TIMEOUT_SECONDS` (10)
- **SMS (Twilio)**: `SMS_NOTIFICATIONS_ENABLED` (default off),
  `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
- **Notification triggers**: `NOTIFY_ON_ATTENDANCE_MARK` (default on),
  `NOTIFY_ON_LOW_ATTENDANCE` (default on),
  `LOW_ATTENDANCE_ALERT_COOLDOWN_HOURS` (24)
- **Password reset**: `PASSWORD_RESET_ENABLED` (default on),
  `PASSWORD_RESET_TOKEN_TTL_MINUTES` (30), `RATE_LIMIT_PASSWORD_RESET`
  (`5 per minute`)
- **Google SSO**: `OAUTH_GOOGLE_ENABLED` (default off),
  `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET`,
  `OAUTH_GOOGLE_DISCOVERY_URL`
- **Admin password reset**: `ADMIN_PASSWORD_RESET_ENABLED` (default
  off), `ADMIN_PASSWORD_RESET_TOKEN_TTL_MINUTES` (15),
  `RATE_LIMIT_ADMIN_PASSWORD_RESET` (`3 per hour`) — the admin's
  recovery email itself is set only via `set_admin_recovery_email.py`,
  never through config/env

## 📊 Database Schema

### Main Tables
- **admins**: Administrator credentials, plus lockout tracking
  (`failed_attempts`, `locked_until`) and an optional `recovery_email`
  (settable only via `set_admin_recovery_email.py` — see "Self-Service
  Password Reset" -> "Admin Accounts")
- **students**: Student information and credentials, plus login lockout
  tracking (`failed_attempts`, `locked_until`), attendance-marking
  abuse lockout tracking (`attendance_security_failures`,
  `attendance_locked_until`), security monitoring state
  (`last_login_ip`, `last_login_at`, `security_escalated`,
  `security_escalated_at`) — see "Security Monitoring" — and optional
  contact/notification/SSO fields (`email`, `phone_number`,
  `notify_email`, `notify_sms`, `last_low_attendance_alert_at`,
  `oauth_google_sub`) — see "Notifications" and "SSO / Institutional
  Login"
- **subjects**: Subject/course information
- **sessions**: Attendance session details, plus lifecycle (`status`,
  `cancelled_at`, `cancellation_reason`) and optional per-session
  overrides (`attendance_window_minutes`, `grace_period_minutes`,
  `allowed_networks`, `restrict_branch`, `restrict_semester`) — see
  "Session Management"
- **attendance**: Attendance records with timestamps, a status
  (`Present`/`Absent`/`Late`), and a recognition-confidence score
- **attendance_correction_requests**: Student-filed requests to correct
  an attendance record, with the admin's resolution — see "Attendance
  Workflow"
- **face_embeddings**: One row per registered face image (128-d vector,
  linked to a student), plus its saved image filename, a 0-100
  enrollment quality score, and the embedding-model version that
  produced it — see "Face Enrollment & Biometric Management"
- **app_settings**: Generic key/value store backing runtime overrides of
  recognition thresholds and security policies from
  `/admin/recognition-settings` and `/admin/security-settings`
- **device_fingerprints**: One row per (student, device) pairing seen at
  login — see "Security Monitoring"
- **student_login_sessions**: One row per student login, used only to
  detect a second concurrently-active login for the same student
- **security_notifications**: Admin-facing alerts for higher-severity
  security events, read/unread like an inbox
- **audit_log**: Timestamped record of logins, lockouts, spoof/liveness
  events, session lifecycle changes, face-enrollment changes,
  device/network/session anomalies, and admin-initiated changes
- **password_reset_tokens**: One row per issued self-service
  password-reset link — a SHA-256 hash of the token (never the raw
  token), an expiry, and a used-once marker — see "Self-Service
  Password Reset"
- **admin_password_reset_tokens**: The admin-account equivalent of
  `password_reset_tokens`, kept as a separate table since the two
  flows' trust models differ (see "Self-Service Password Reset" ->
  "Admin Accounts")

### Face Recognition Tables
- **people**: Face profile information linked to student IDs

## 🎨 User Interface

### Admin Features
- Dashboard with attendance statistics, quick-action shortcuts, a
  low-attendance meter per student, a Security Alerts widget for
  repeated spoof/liveness events, and an Enrollment Reminders widget
  for incomplete/poor-quality face enrollment
- Student management (searchable, paginated), including resetting a
  student's password, clearing an attendance-marking lockout or a
  security escalation, and managing/resetting their face enrollment
- Bulk student import via CSV, with a downloadable template and a
  per-row results summary (created / skipped, with reasons)
- Session creation and management, with automatic time-conflict
  detection, a status lifecycle (Scheduled/Active/Completed/Cancelled),
  cancellation and rescheduling, and optional per-session
  attendance-window/network overrides and student-group assignment
- Live attendance monitoring, with a Present/Late/Absent/Failed-Attempts
  summary per session
- Full paginated attendance record browser (with recognition confidence), plus CSV export
- Manual attendance editing (add/remove/change, with a reason) and a
  correction-request review queue (approve/reject)
- Filterable attendance reports (subject/date-range/semester) with
  percentage warnings, a trend chart, and CSV export
- Low attendance alerts
- Face enrollment management per student: view/remove individual
  registered photos with their quality score, see which embedding model
  version produced each, and reset a student's face data entirely
- Recognition model/configuration management and threshold configuration
  (`/admin/recognition-settings`) — view effective recognition config
  and edit the numeric match/quality thresholds without restarting
- Security dashboard (`/admin/security-dashboard`) with an event-severity
  trend chart, a per-student risk-score leaderboard, and a notification
  feed; per-student IP/device history and risk breakdown
  (`/admin/student/<id>/security-history`); and configurable security
  policies (`/admin/security-settings`) — see "Security Monitoring"
- Admin account management (add/remove admin accounts, change your own password)
- Paginated audit log of logins, lockouts, face-enrollment changes,
  device/network/session anomalies, and admin-initiated changes, with a
  Security Events filter and Low/Medium/High/Critical severity levels

### Student Features
- Self-registration with face capture
- Login with credentials
- Profile page: account details, enrollment status, registered-photo
  count, attendance summary with a visual meter, self-service password
  change, and a re-enrollment reminder when quality is low
- Add face photos after the fact (`/student/register-face`) — for
  accounts created via admin bulk import, which don't capture a face at
  creation time
- View, and individually remove, their own registered face photos
  (`/student/face-photos`), or wipe all of them to re-enroll from scratch
- View attendance history, including Present/Late/Absent status for
  every session (not just ones they were marked for)
- Mark attendance for active sessions
- Attendance percentage calculation, with a banner on their profile/history if it's below the low-attendance threshold
- File a correction request for a session they believe is recorded
  wrong, and track its approve/reject status

## 🔒 Security Features

- **Admin and student authentication** — session-based, with hashed
  passwords (`werkzeug.security`), not plaintext
- **CSRF protection** on every state-changing request (Flask-WTF)
- **Session-derived identity** — attendance is always recorded against
  the logged-in student's own session, never a client-supplied ID
- **SQL injection protection** — all queries use parameterized
  placeholders, no string-built SQL anywhere in the codebase
- **Environment-based secrets** — session secret key and default admin
  password come from environment variables / `.env`, not hardcoded
- **Face recognition validation** — cosine-similarity matching against
  stored embeddings, only one face permitted in frame
  (`REJECT_MULTIPLE_FACES`), a two-signal passive liveness check (pixel
  motion across a frame burst, and — if enabled — a detectable blink),
  PLUS a randomized active liveness challenge and a heuristic
  screen/print replay detector — see "Anti-Proxy / Face Recognition
  Security" below for the full picture
- **Network restriction for attendance marking** — optional IP allowlist
  (`ATTENDANCE_ALLOWED_NETWORKS`); unconfigured by default, and
  overridable per-session (see "Session Management" above)
- **Attendance-marking abuse lockout** — repeated spoof-suspected /
  failed-liveness-challenge attempts on one account lock it out of
  marking attendance for a configurable period, distinct from the login
  lockout below — see "Anti-Proxy / Face Recognition Security" above
- **Rate limiting** (Flask-Limiter) on admin and student login, per IP —
  see `RATE_LIMIT_*` in `config.py`
- **Account lockout** — an account locks itself out for a configurable
  period after repeated failed login attempts, independent of rate
  limiting (protects one specific account even from a distributed
  attempt) — see `LOCKOUT_*` in `config.py`
- **CAPTCHA** on both login forms — a self-hosted, distorted-text image
  (no third-party service or API key required); see `CAPTCHA_ENABLED`
- **Password strength policy** for student registration and admin
  account creation — see `PASSWORD_MIN_LENGTH` /
  `PASSWORD_REQUIRE_LETTER_AND_DIGIT`
- **Session expiry** — login sessions expire after a period of
  inactivity (`SESSION_LIFETIME_MINUTES`, default 60)
- **Admin account management** — add/remove admin accounts from
  `/admin/settings`; the last remaining admin account and your own
  currently-logged-in account can't be deleted
- **Self-service password change** — both admins (`/admin/settings`) and
  students (`/student/profile`) can change their own password given
  their current one, without needing an admin-assisted reset
- **Audit log** — every login (success/failure/lockout), student/session
  deletion, attendance override/correction, session cancellation,
  face-enrollment change, unknown-face detection, device/network/session
  anomaly, and admin-account change is recorded with a timestamp, actor,
  and IP address, viewable at `/admin/audit-log` — with a **Security
  Events** filter (`?filter=security`), a Low/Medium/High/Critical
  **severity level** per event (see "Security Monitoring"), an
  admin-dashboard **Security Alerts** widget that groups spoof/liveness
  events per student, and a fuller **Security Dashboard**
  (`/admin/security-dashboard`) with trends and risk scores
- **Account recovery** — a student can reset their own forgotten
  password via an emailed link if they've added an email to their
  account (see "Self-Service Password Reset"), or an admin can reset it
  from `/admin/students` (a one-time temporary password to share
  out-of-band) either way; for the admin account itself,
  `python reset_admin_password.py <username>` from the server always
  works, and a more restricted self-service option also exists if
  explicitly enabled (see "Self-Service Password Reset" -> "Admin
  Accounts")
- **Request size limits** — a global body-size cap
  (`MAX_CONTENT_LENGTH_MB`) plus tighter, more specific caps on
  registration image count/size and attendance-marking frame size
- **`.gitignore`'d local data** — the SQLite databases and face images
  are excluded from version control by default

**What this project does *not* provide** (worth knowing before relying on
it for anything beyond a class project or internal tool):
- No encryption at rest for the SQLite databases or stored face images —
  anyone with filesystem access to the server can read them directly
- The liveness check and CAPTCHA are both basic measures. The active
  liveness challenge (see "Anti-Proxy / Face Recognition Security")
  raises the bar specifically against a pre-recorded video replay
  compared to passive motion+blink alone, and the screen-replay detector
  catches an unprepared "hold a phone up to the camera" attempt — but
  none of this is production-grade anti-spoofing against a motivated,
  well-resourced attacker (e.g. a live deepfake, or a video prepared
  specifically to react to whichever challenge is shown)
- IP allowlisting restricts by network, not physical location — it's not
  GPS-based geofencing and can be defeated by a VPN/proxy that appears to
  originate from an allowed network
- No HTTPS/TLS is configured here — that's the deploying environment's
  responsibility (e.g. a reverse proxy)
- Admin self-service password reset is **off by default**
  (`ADMIN_PASSWORD_RESET_ENABLED`) and, even when enabled, only works
  for an admin whose recovery email was set via
  `set_admin_recovery_email.py` on the server — there is no web route
  to set it (see "Self-Service Password Reset" -> "Admin Accounts").
  `python reset_admin_password.py` remains the unconditional fallback.
  Students have self-service reset on by default, but only once
  they've added an email to their account.
- SSO covers Google only, and a login-intent match is only ever to an
  **already-registered** student account matched by email — there's no
  SAML/generic-OIDC support, and it can't substitute for registration's
  face-enrollment step. Linking a Google account to an account directly
  (from `/student/profile` or right after registering) doesn't need
  that email match, but still can't create a new account by itself
  (see "SSO / Institutional Login").

## 🚨 Troubleshooting

### Common Issues

1. **Face Recognition Not Working**
   - Ensure `opencv-contrib-python` is installed
   - Check if webcam is properly connected
   - Verify the embedding model exists at `models/openface_nn4.small2.v1.t7`
     (see `models/README.md`)
   - Verify students have registered face embeddings (check the
     `face_embeddings` table, or the student's `photo_count`)

2. **Camera Not Detected**
   - Check camera permissions
   - Ensure no other application is using the camera
   - Try restarting the application

3. **Database Errors**
   - Delete existing `.db` files and restart the application
   - Ensure write permissions in the project directory

4. **Face Detection Issues**
   - Ensure proper lighting conditions
   - Position face clearly in front of camera
   - Check if `haarcascade_frontalface_default.xml` exists

5. **"Could not confirm a live camera feed" during attendance marking**
   - Make sure only one person is in frame (see `REJECT_MULTIPLE_FACES`)
   - Blink naturally when prompted during the ~1.6s capture — the check
     looks for a genuine eyes-visible/hidden transition
   - Glasses, poor lighting, or an off-angle face can make the Haar eye
     cascade miss real blinks; if this causes too many false rejections
     in your environment, relax or disable it via `LIVENESS_REQUIRE_BLINK`

6. **Locked out after failed login attempts**
   - Wait out `LOCKOUT_DURATION_MINUTES` (default 15), or an existing
     admin can adjust `LOCKOUT_MAX_FAILED_ATTEMPTS` /
     `LOCKOUT_DURATION_MINUTES`
   - If the *only* admin account is locked out, use
     `python reset_admin_password.py <username>` from the server — this
     also clears the lockout (see the script's `--help`)

7. **CAPTCHA image not loading**
   - It's rendered server-side with PIL using a bundled system font; if
     it 500s, check the server log — a missing font path is the most
     likely cause (see `render_captcha_image()` in `app.py`)

### Debug Mode
The application includes debug features:
- Debug images saved as `debug_face.jpg`
- Console output for face detection results
- Match similarity scores displayed in logs (cosine similarity, 0-1 — higher is a better match)

## ✅ Running Tests

The test suite (464 tests) covers authentication (password hashing, login
flows, account lockout), CSRF protection, rate limiting, CAPTCHA,
password strength policy, self-service and admin-assisted password
changes, SQL-injection resistance, the embedding-based face recognition
core (computation, matching, gallery storage/deletion), face alignment,
multi-face rejection, blink-based liveness, registration image quality
checks, IP allowlisting, request size limits, admin account management,
the audit log, the student profile page, bulk CSV import, session
overlap detection, pagination, reverse-proxy IP handling, the
attendance-marking security properties (session-derived identity, 1:1
verification, no cross-identity leakage), and the notifications/
password-reset/SSO additions (safe-no-op behavior when unconfigured,
token issuance/expiry/single-use for both the student and admin reset
flows, the email-matching/no-auto-create rule for Google login, and the
account-linking flows from registration and from the profile page —
see `tests/test_notifications_and_sso.py`).

Every test runs against a temporary, isolated database and Datasets
folder — the suite never touches your real `database/` or `Datasets/`.
CAPTCHA and rate limiting are disabled by default in tests (via a shared
fixture) so unrelated tests don't have to solve a CAPTCHA; `test_captcha.py`
and `test_rate_limiting.py` re-enable them explicitly to test them
directly. That same fixture also resets the rate limiter's counters
before every test, so `test_rate_limiting.py` behaves the same whether
it's run alone or as part of the full suite.

```bash
pip install -r requirements-dev.txt
pytest
```

### Continuous Integration

`.github/workflows/ci.yml` runs the full test suite automatically on
every push and every pull request, against Python 3.9 and 3.12, so a
change that breaks a test can't land unnoticed. It fails fast
(`--maxfail=1`) and uploads the pytest cache as a build artifact if
something fails, to help debug from the Actions tab without needing to
reproduce locally first. No repository secrets are required — the
workflow uses a throwaway `FLASK_SECRET_KEY` and disables CAPTCHA/rate
limiting the same way the local test fixtures do.

If you fork or self-host this repo, CI runs automatically the first time
GitHub Actions is enabled for the fork — no extra setup needed.

### Lint & type-check gate

A second CI job (`lint`) runs [ruff](https://docs.astral.sh/ruff/) and
[mypy](https://mypy-lang.org/) on every push/PR, configured in
`pyproject.toml`. Ruff catches real bugs and import hygiene issues (unused
imports/variables, bare `except:`, etc.); mypy is non-strict — it catches
clear type errors without requiring annotations everywhere, since a lot of
this codebase (Flask view functions, `sqlite3.Row` access, numpy/cv2
arrays) isn't realistic to fully annotate. Run both locally with:

```bash
pip install -r requirements-dev.txt
ruff check .
mypy
```

`ruff check . --fix` will auto-fix most of what it flags.

### Dependency vulnerability scanning

A third CI job (`security`) runs [pip-audit](https://pypi.org/project/pip-audit/)
against everything `requirements.txt`, `requirements-dev.txt`, and
`requirements-prod.txt` resolve to, checking for known CVEs in the PyPA
Advisory Database. It fails the job (`--strict`) on any finding rather than
just warning. Run it locally the same way:

```bash
pip install -r requirements.txt -r requirements-dev.txt -r requirements-prod.txt
pip-audit --strict
```

[Dependabot](https://docs.github.com/en/code-security/dependabot) is also
configured (`.github/dependabot.yml`) to open a weekly PR for outdated pip
dependencies and GitHub Actions versions, so vulnerable/stale pins don't
just sit there between manual audits. It's told not to propose a major-version
bump of `opencv-contrib-python` on its own, since 5.x removes the Torch
model loader the face embedder depends on (see the pin comment in
`requirements.txt`) — that upgrade needs a person to also port
`get_embedder()` to an ONNX equivalent, not just accept a version bump.

## 🗄️ Database Migrations

Schema changes to `database/app.db` are managed with
[Alembic](https://alembic.sqlalchemy.org/) instead of hand-rolled
`ALTER TABLE` checks. Migration scripts live in `migrations/versions/`;
`app.py`'s `init_databases()` calls `db_migrations.run_migrations()` on
every startup, which:

- Creates a brand-new database from scratch by running every migration in
  order, or
- Brings an existing database up to date by applying only the migrations
  it hasn't seen yet (tracked in an `alembic_version` table), or
- Detects a database created by an older, pre-Alembic version of this app
  (it has tables but no `alembic_version` row), stamps it at the specific
  revision matching what that old ad-hoc code actually created, and then
  upgrades normally from there — so any migration added after that point
  still gets applied, rather than the database being incorrectly marked
  as fully up to date.

This all happens automatically — there's no extra command to run for
normal use. `database/FaceBase.db` (the legacy `people` table) isn't part
of this migration chain; it has no schema history and is still created
directly by `init_databases()`.

To add a new migration by hand (autogenerate isn't used here — there's no
SQLAlchemy ORM/model layer to diff against):

```bash
# Creates a new, empty revision file under migrations/versions/ chained
# after the current head — fill in upgrade()/downgrade() yourself.
alembic revision -m "add some_column to some_table"
```

To inspect or manage a database's migration state directly:

```bash
alembic -x database_path=database/app.db current   # show current revision
alembic -x database_path=database/app.db history    # show the full chain
```

## 💾 Backup & Restore

`backup.py` and `restore.py` handle the SQLite databases
(`database/app.db`, `database/FaceBase.db`) and the registered face
images (`Datasets/`).

**Creating a backup:**

```bash
python backup.py                       # full backup (DBs + face images) to BACKUP_DIR (default: ./backups)
python backup.py --skip-images         # DBs only — much faster/smaller
python backup.py --output-dir /mnt/backups
python backup.py --keep 30             # override BACKUP_RETENTION_COUNT for this run
```

Each run produces one self-contained `backup-YYYYmmdd-HHMMSS.tar.gz`
archive containing both databases, the face images, and a small
`manifest.json`. The databases are copied using
[SQLite's own online backup API](https://www.sqlite.org/backup.html)
(`sqlite3.Connection.backup()`), not a raw file copy — that's what makes
it safe to run against a live database while the app is still serving
requests: a plain `cp`/`shutil.copy` can catch app.db mid-write and
produce a corrupt snapshot, where the backup API reads through SQLite's
normal locking and always produces a consistent one. After creating the
archive, old backups beyond `BACKUP_RETENTION_COUNT` (default: 14, set to
`0` to keep every backup forever) are deleted automatically, so a
scheduled job doesn't grow `BACKUP_DIR` without bound.

**Restoring a backup:**

```bash
python restore.py backups/backup-20260820-020000.tar.gz
python restore.py backups/backup-20260820-020000.tar.gz --force        # needed if destination already has data
python restore.py backups/backup-20260820-020000.tar.gz --skip-images  # databases only, even if the archive has images
```

Restore has a few safety properties worth knowing about:
- It refuses to overwrite an existing, non-empty database or `Datasets/`
  folder unless you pass `--force` — you won't accidentally clobber live
  data by running the wrong archive.
- Even with `--force`, nothing is deleted: existing data is moved aside
  to `<name>.pre-restore-<timestamp>` first, so a restore is itself
  recoverable if it turns out to have been the wrong archive.
- The restored database is checked with `PRAGMA integrity_check` before
  the restore is considered successful, so a truncated or corrupted
  archive is caught immediately rather than leaving a silently-broken
  database in place.
- The restored database is then run through the app's own Alembic
  migrations (see "Database Migrations" above) automatically — a backup
  made by an older version of this app, with an older schema, comes out
  fully up to date, the same way a normal app startup would upgrade it.

**Automating it.** Neither script schedules itself — something needs to
invoke `backup.py` periodically. Two common options:

```bash
# cron (add via `crontab -e`; adjust paths for your setup):
0 2 * * * cd /path/to/app && /path/to/venv/bin/python backup.py >> backup.log 2>&1

# Running inside a Docker container via docker compose, from the host's cron:
0 2 * * * docker compose -f /path/to/app/docker-compose.yml exec -T attendance-app python backup.py >> /var/log/attendance-backup.log 2>&1
```

Or a systemd timer, if you'd rather not touch crontab:

```ini
# /etc/systemd/system/attendance-backup.service
[Unit]
Description=Attendance app backup

[Service]
Type=oneshot
WorkingDirectory=/path/to/app
ExecStart=/path/to/venv/bin/python backup.py
```

```ini
# /etc/systemd/system/attendance-backup.timer
[Unit]
Description=Run attendance-backup.service daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now attendance-backup.timer
```

## 📋 Structured Logging

Every log line — this app's own, plus (under gunicorn) gunicorn's access
and error logs — is emitted as one JSON object per line by default (see
`logging_config.py`), tagged with a `request_id` that's consistent across
every log line produced while handling the same request. Configured via:

- `LOG_LEVEL` (default `INFO`)
- `LOG_FORMAT` (`json`, the default — recommended for production/log
  shippers — or `plain`, a human-readable single-line format that's
  easier to read in a local terminal during development)

A request id is generated for every request (or reused from an inbound
`X-Request-ID` header, if a trusted upstream reverse proxy already set
one) and echoed back as an `X-Request-ID` response header, so a client,
proxy, and this app's own logs can all be correlated for the same
request. One structured "access log" line is emitted per request —
method, path, status code, duration, remote address, and the logged-in
user (if any) — deliberately **never** the request/response body, since
those can carry face-image payloads or, on some legacy code paths,
plaintext passwords.

Example line (`LOG_FORMAT=json`, the default):

```json
{"timestamp": "2026-08-20T02:15:04.123Z", "level": "INFO", "logger": "attendance_app", "message": "GET /admin/dashboard 200", "request_id": "a4deac119e794146bf371479667467e6", "event": "http_request", "http_method": "GET", "http_path": "/admin/dashboard", "http_status": 200, "duration_ms": 42.7, "remote_addr": "127.0.0.1", "actor": "admin"}
```

Under gunicorn, `gunicorn.conf.py` also reformats gunicorn's own access
and error logs as JSON (`JsonGunicornLogger` in `logging_config.py`), and
sets `access_log_format` to include the same `X-Request-ID` the app set
on the response — so gunicorn's access-log line for a request and this
app's own access-log line for that same request share one `request_id`,
even though they're logged from two different places. Set
`GUNICORN_LOG_FORMAT=plain` to fall back to gunicorn's normal
Apache-combined-style access log text instead (e.g. for a quick local run
where JSON is harder to eyeball).

An unhandled exception anywhere in the app is caught by a generic error
handler that logs the full traceback (tagged with that request's id) and
returns a generic message to the client — the traceback itself is never
leaked in the response.

## 📈 Metrics

`GET /metrics` exposes request counts and latency (by route and status),
audit-event counts (by action), attendance-marking outcomes, and a few
current-state gauges (student count, active sessions, unread security
notifications) in Prometheus text exposition format — hand-rolled rather
than depending on the `prometheus_client` package, matching this
project's general preference for no third-party dependency where a
straightforward one suffices.

```
# HELP http_requests_total Total HTTP requests by route, method, and status class.
# TYPE http_requests_total counter
http_requests_total{endpoint="login",method="GET",status="2xx"} 42
...
```

Configured via `METRICS_ENABLED` (default on) and optional
`METRICS_AUTH_TOKEN` (accepted as `?token=` or `Authorization: Bearer
<token>` — request counts aren't secret, but they do reveal usage
patterns, so an internet-facing deployment may want to gate it). Two
things worth knowing before wiring up a scraper:

- **In-memory, per-process** — counters reset on restart and, under
  gunicorn's default multi-worker setup, each worker only reports its
  own share of traffic; there's no cross-worker aggregation. Scrape
  every worker (or run `GUNICORN_WORKERS=1`) for an accurate aggregate.
- Route labels use Flask's endpoint name (e.g. `login`, `admin_sessions`),
  not the raw URL path, to keep the label set small and fixed rather than
  exploding with every distinct session/student id that appears in a URL.

## 🚨 Error Alerting

Optional integration with [Sentry](https://sentry.io) for crash
reporting (`error_reporting.py`) — entirely opt-in: nothing is sent
anywhere, and no network call is made, unless `SENTRY_DSN` is set.

```bash
# .env or environment
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
SENTRY_ENVIRONMENT=production      # tags events, so staging noise doesn't mix with production alerts
SENTRY_TRACES_SAMPLE_RATE=0.0      # 0 = error reporting only; >0 also samples performance traces
```

`sentry-sdk[flask]` is listed in `requirements-prod.txt` (not
`requirements.txt`), so a local/dev install doesn't pull it in unasked —
but every function in `error_reporting.py` is a safe no-op if the DSN
isn't set or the package isn't installed, so the rest of the app never
needs to check "is Sentry configured?" before calling them.

Every captured event is tagged with the request's `request_id` (see
"Structured Logging" above), so a Sentry alert and the matching JSON log
line for the same request can be cross-referenced. Request/response
bodies are never sent to Sentry (`send_default_pii=False`,
`max_request_body_size='never'`), for the same reason they're never
logged — they can carry face-image payloads or plaintext passwords.

`backup.py` and `restore.py` also report failures to Sentry (as a
message, not just an exception) if it's configured, so a broken scheduled
backup doesn't go unnoticed until someone needs a restore that isn't
there.

## 🚀 Deployment

The instructions above (`python app.py`) run Flask's built-in development
server — fine for local use and testing, but it prints its own warning
for a reason: no real concurrency, no process supervision (a crashed
worker just stays down), and it isn't hardened for the open internet.
This section covers running it the way you actually would in production.

### Running with gunicorn (Linux/macOS)

```bash
pip install -r requirements.txt -r requirements-prod.txt
FLASK_SECRET_KEY=... DEFAULT_ADMIN_PASSWORD=... gunicorn --config gunicorn.conf.py wsgi:app
```

`wsgi.py` is the entrypoint gunicorn imports — it calls the same
`init_databases()` step `python app.py` runs, so the SQLite files,
`database/`, and `Datasets/` directories are created (or migrated) the
first time a worker boots, same as before. `gunicorn.conf.py` sizes the
worker count off available CPUs and raises the per-request timeout past
gunicorn's 30s default, since a registration/attendance request runs
face detection and embedding inference synchronously — tune both via the
`GUNICORN_WORKERS` / `GUNICORN_TIMEOUT` environment variables if needed.

### Deploying on Windows

gunicorn doesn't support Windows (it relies on `fcntl`/`os.fork`). Use
[waitress](https://docs.pylonsproject.org/projects/waitress/) instead:

```bash
pip install -r requirements.txt waitress
waitress-serve --host 0.0.0.0 --port 5000 wsgi:app
```

### Running with Docker

```bash
cp .env.example .env   # then set FLASK_SECRET_KEY and DEFAULT_ADMIN_PASSWORD
docker compose up --build -d
```

This builds the image from the included `Dockerfile` (Python 3.12-slim,
the system libraries `opencv-contrib-python` needs, a non-root
`appuser`, and a `HEALTHCHECK` that polls `/login`) and starts it via
`docker-compose.yml`, which also bind-mounts `./database` and
`./Datasets` into the container so student data and attendance records
survive container restarts and image rebuilds — they are **not** baked
into the image.

Without Compose, the equivalent is:

```bash
docker build -t attendance-face-recognition .
docker run -d --name attendance -p 5000:5000 \
  --env-file .env \
  -v "$(pwd)/database:/app/database" \
  -v "$(pwd)/Datasets:/app/Datasets" \
  attendance-face-recognition
```

**First-run note on bind mounts:** if `./database` and `./Datasets`
don't already exist on the host, Docker creates them owned by `root`,
which can block writes from the container's non-root `appuser` (uid
1000). Either let the app create them itself on first successful write
after `chown -R 1000:1000 database Datasets`, or pre-create them with
that ownership before the first `docker compose up`.

### Deploying behind a reverse proxy

Putting nginx (or a cloud load balancer) in front of gunicorn is the
normal production setup — for TLS termination, and to let one host serve
multiple apps. Doing this changes what `request.remote_addr` sees inside
Flask: without extra configuration, **every** request appears to come
from the proxy's own IP, not the real visitor's. That silently defeats
per-IP rate limiting, account lockout, the attendance IP allowlist
(`ATTENDANCE_ALLOWED_NETWORKS`), and the audit log's recorded IPs — all
four read `request.remote_addr` directly.

Set these two environment variables when deploying behind a proxy:

```bash
BEHIND_REVERSE_PROXY=1
PROXY_FIX_NUM_PROXIES=1   # number of trusted proxy hops in front of the app
```

This wraps the WSGI app in werkzeug's `ProxyFix`
(`configure_proxy_fix()` in `app.py`), which trusts `X-Forwarded-For`
from exactly that many hops back. It's off by default — turning it on
when there's no trusted proxy in front of the app would let any client
spoof its own IP via that header, so only enable it when a proxy you
control is actually terminating the connection.

If that proxy is also terminating TLS (the normal case — see the nginx
config below), also set:

```bash
SESSION_COOKIE_SECURE=1
```

so browsers refuse to send the session cookie over a plain HTTP
connection. This defaults to on automatically when `BEHIND_REVERSE_PROXY=1`,
but has its own switch in case that proxy *isn't* doing TLS (e.g. an
internal staging box reached over plain HTTP) — setting it without HTTPS
actually in front will just make login stop working, since the browser
will silently drop the cookie.

Minimal nginx config to sit in front of the container:

```nginx
server {
    listen 80;
    server_name your-domain.example;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Terminate TLS at nginx (e.g. via `certbot`/Let's Encrypt) rather than in
gunicorn — the app itself only ever needs to speak plain HTTP to the
proxy sitting in front of it on the same host/network.

## 🔄 Advanced Usage

### Migrating from the old LBPH-based version
If you're upgrading an existing deployment that used the old LBPH
recognizer, run this once to backfill embeddings for already-registered
students from their saved photos (no re-registration needed):
```bash
python migrate_embeddings.py
```
Safe to re-run — it skips images it's already migrated.

### Tuning recognition accuracy
All detection and match-threshold tuning lives in `config.py` /
`.env.example` — no code changes needed. See the "Face Recognition
Settings" section above.

The old standalone CLI scripts (`Trainer.py`, `Detector.py`,
`Dataset_Creator.py`, etc.) have been superseded by the web app and moved
to `archive/legacy_scripts/` for reference — see `archive/README.md`.

## 📈 Performance Optimization

- **Image Resolution**: Optimize camera resolution for faster processing
- **Match Thresholds**: Adjust based on environment conditions (see `config.py`)
- **Database Indexing**: Automatic indexing for faster queries
- **Caching**: Session data caching for improved performance

## 🔮 Future Enhancements

- [ ] Multi-language support
- [ ] Mobile application
- [ ] Cloud storage integration
- [ ] Deeper analytics (cohort comparisons, predictive risk scoring) beyond the current subject/date-range/semester reports
- [x] ~~SMS/email notifications~~ — done: see "Notifications" (opt-in SMTP email + Twilio SMS for attendance marks and low-attendance alerts)
- [ ] Biometric integration (fingerprint, iris)
- [ ] AI-powered attendance predictions
- [ ] Browser/E2E test coverage (the current suite is unit/integration level, mocking the camera pipeline)
- [x] ~~SSO/institutional login~~ — partially done: see "SSO / Institutional Login" (Google only, login-only for an existing account, no SAML/generic-OIDC yet)
- [ ] A path to Postgres/concurrent-write scaling beyond the current single-SQLite-file design
- [ ] SAML/generic-OIDC/LDAP SSO providers beyond Google, and OAuth-based self-registration (currently login-only, matched to an existing account by email)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

This project's original code is licensed under the MIT License — see the
[LICENSE](LICENSE) file for details. The pretrained face-embedding model
(`models/openface_nn4.small2.v1.t7`) carries a separate, non-commercial
license from the OpenFace project — see `models/README.md`.

## 👥 Authors

- **Santosh Suresh Madannavar** - *Initial development* - [Santosh02411](https://github.com/Santosh02411)

## 🙏 Acknowledgments

- OpenCV community for face recognition algorithms
- OpenFace project (CMU) for the pretrained embedding model
- Flask framework for web development
- All contributors and users of this project

## 📞 Support

For support and queries:
- Open an issue in the [GitHub repository](https://github.com/Santosh02411/Attendance_Using_Face_Recognition/issues)
- Email: santoshmadannavar@gmail.com
- This README is the primary documentation — see `config.py` and
  `.env.example` for every tunable setting, and `models/README.md` for
  details on the face embedding model

---

**Note**: This application is designed for educational and institutional use. Ensure compliance with privacy laws and regulations when implementing face recognition systems.