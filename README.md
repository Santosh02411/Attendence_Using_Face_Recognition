# Attendance Using Face Recognition

A comprehensive attendance management system powered by face recognition technology. This web application provides an automated, secure, and efficient way to track student attendance using facial recognition algorithms.

## 📑 Table of Contents

- [Features](#-features)
- [Technology Stack](#️-technology-stack)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Project Structure](#-project-structure)
- [How It Works](#-how-it-works)
- [Configuration](#-configuration)
- [Database Schema](#-database-schema)
- [User Interface](#-user-interface)
- [Security Features](#-security-features)
- [Troubleshooting](#-troubleshooting)
- [Running Tests](#-running-tests)
- [Deployment](#-deployment)
- [Advanced Usage](#-advanced-usage)
- [Performance Optimization](#-performance-optimization)
- [Future Enhancements](#-future-enhancements)
- [Contributing](#-contributing)
- [License](#-license)

## 🚀 Features

- **Face Recognition Attendance**: Automated attendance marking using facial recognition
- **Admin Dashboard**: Complete control over sessions, students, and attendance records, with quick-action shortcuts and live stats
- **Student Portal**: Self-service registration, attendance marking, attendance history, and a profile page with account details and self-service password change
- **Session Management**: Create and manage attendance sessions for different subjects, with automatic overlap detection so two sessions can't double-book the same time window
- **Real-time Recognition**: Live face detection and recognition during attendance sessions
- **Bulk Student Import**: Onboard a whole class at once via CSV upload (name/roll number/branch/semester, with auto-generated temporary passwords); each student adds their own face photos afterward
- **Export Functionality**: Export attendance records as CSV files
- **Low Attendance Alerts**: Automatic alerts (with a visual meter) for students below a configurable attendance threshold
- **Admin Account Management**: Add/remove admin accounts, self-service password change, audit log of admin actions
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
├── wsgi.py                         # Production WSGI entrypoint (gunicorn/waitress) — see "Deployment"
├── gunicorn.conf.py                # Gunicorn server tuning (workers, timeouts, logging)
├── healthcheck.py                  # Standalone HTTP healthcheck probe, used by Docker HEALTHCHECK
├── migrate_embeddings.py           # One-time backfill for pre-embedding registrations
├── reset_admin_password.py         # CLI recovery tool if the admin account gets locked out
├── requirements.txt                # Python dependencies
├── requirements-dev.txt            # + pytest, for running the test suite
├── requirements-prod.txt           # + gunicorn, for running in production (Linux/macOS/Docker)
├── pytest.ini                      # pytest configuration
├── .env.example                    # Template for local environment overrides
├── Dockerfile                      # Production container image (gunicorn + non-root user)
├── docker-compose.yml              # Local/production container run, with persistent volumes
├── .dockerignore                   # Files excluded from the Docker build context
├── .github/workflows/ci.yml        # GitHub Actions: runs the test suite on every push/PR
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
│   ├── login.html                # Admin login page (+ CAPTCHA)
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
│   ├── student_login.html        # Student login (+ CAPTCHA)
│   ├── student_profile.html      # Student account details + self password change
│   ├── student_attend.html       # Student self-service attendance marking
│   └── student_history.html      # Attendance history
├── static/                        # CSS and shared JS (styles.css, app.js)
├── tests/                          # pytest test suite (180 tests) — see "Running Tests"
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
- A multi-signal liveness check runs before matching: pixel motion across
  a burst of frames, plus (if enabled) a detectable blink — see
  `config.LIVENESS_CHECK_ENABLED` / `LIVENESS_REQUIRE_BLINK`, and the
  honest caveats in `config.py` about what this can and can't catch (a
  prepared video replay containing a blink at the right moment could
  still pass)
- Registration photos are quality-checked (blur, brightness) before being
  turned into an embedding — a bad photo is skipped, not silently stored
  (see `IMAGE_QUALITY_CHECK_ENABLED`)
- Self-service attendance marking can optionally be restricted to an IP
  allowlist (see `ATTENDANCE_ALLOWED_NETWORKS`) — not GPS-based
  geofencing, but a practical network-level restriction

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
- **CAPTCHA**: `CAPTCHA_ENABLED`, `CAPTCHA_LENGTH`
- **Password policy**: `PASSWORD_MIN_LENGTH`,
  `PASSWORD_REQUIRE_LETTER_AND_DIGIT`
- **Session lifetime**: `SESSION_LIFETIME_MINUTES` (default 60)
- **Request size limits**: `MAX_CONTENT_LENGTH_MB`,
  `MAX_REGISTRATION_IMAGES`, `MAX_IMAGE_BASE64_CHARS`
- **Liveness/blink**: `LIVENESS_FRAME_COUNT` (default 5),
  `LIVENESS_REQUIRE_BLINK`
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
- **Pagination**: `STUDENTS_PER_PAGE` (25), `AUDIT_LOG_PER_PAGE` (50),
  `ATTENDANCE_RECORDS_PER_PAGE` (50)
- **Bulk import**: `MAX_BULK_IMPORT_ROWS` (500 rows per CSV)

## 📊 Database Schema

### Main Tables
- **admins**: Administrator credentials, plus lockout tracking
  (`failed_attempts`, `locked_until`)
- **students**: Student information and credentials, plus the same
  lockout tracking
- **subjects**: Subject/course information
- **sessions**: Attendance session details
- **attendance**: Attendance records with timestamps
- **face_embeddings**: One row per registered face image (128-d vector,
  linked to a student)
- **audit_log**: Timestamped record of logins, lockouts, and
  admin-initiated changes

### Face Recognition Tables
- **people**: Face profile information linked to student IDs

## 🎨 User Interface

### Admin Features
- Dashboard with attendance statistics, quick-action shortcuts, and a
  low-attendance meter per student
- Student management (searchable, paginated), including resetting a
  student's password
- Bulk student import via CSV, with a downloadable template and a
  per-row results summary (created / skipped, with reasons)
- Session creation and management, with automatic time-conflict detection
- Live attendance monitoring
- Full paginated attendance record browser, plus CSV export
- Low attendance alerts
- Admin account management (add/remove admin accounts, change your own password)
- Paginated audit log of logins and admin-initiated changes

### Student Features
- Self-registration with face capture
- Login with credentials
- Profile page: account details, registered-photo count, attendance
  summary with a visual meter, and self-service password change
- Add face photos after the fact (`/student/register-face`) — for
  accounts created via admin bulk import, which don't capture a face at
  creation time
- View attendance history
- Mark attendance for active sessions
- Attendance percentage calculation

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
  (`REJECT_MULTIPLE_FACES`), plus a two-signal liveness check (pixel
  motion across a frame burst, and — if enabled — a detectable blink; see
  `config.LIVENESS_CHECK_ENABLED` / `LIVENESS_REQUIRE_BLINK`) to make
  casual photo/screen spoofing harder
- **Network restriction for attendance marking** — optional IP allowlist
  (`ATTENDANCE_ALLOWED_NETWORKS`); unconfigured by default
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
  deletion, attendance override, and admin-account change is recorded
  with a timestamp, actor, and IP address, viewable at `/admin/audit-log`
- **Account recovery** — if a student forgets their password, an admin
  can reset it from `/admin/students` (generates a one-time temporary
  password to share with the student out-of-band), after which the
  student can set their own password from `/student/profile`; for the
  admin account itself, use `python reset_admin_password.py <username>`
  from the server if locked out (there's no email/SMTP infrastructure in
  this project to build a self-service "forgot password" flow on top of
  — see the script's docstring)
- **Request size limits** — a global body-size cap
  (`MAX_CONTENT_LENGTH_MB`) plus tighter, more specific caps on
  registration image count/size and attendance-marking frame size
- **`.gitignore`'d local data** — the SQLite databases and face images
  are excluded from version control by default

**What this project does *not* provide** (worth knowing before relying on
it for anything beyond a class project or internal tool):
- No encryption at rest for the SQLite databases or stored face images —
  anyone with filesystem access to the server can read them directly
- The liveness check and CAPTCHA are both basic measures — not
  production-grade anti-spoofing or bot protection against a motivated,
  well-prepared attacker (e.g. a video replay containing a blink at the
  right moment could still defeat the liveness check)
- IP allowlisting restricts by network, not physical location — it's not
  GPS-based geofencing and can be defeated by a VPN/proxy that appears to
  originate from an allowed network
- No HTTPS/TLS is configured here — that's the deploying environment's
  responsibility (e.g. a reverse proxy)
- No self-service "forgot password" for a student who doesn't remember
  their current password (see Account Recovery above — an admin-assisted
  reset is still required as the first step)

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

The test suite (180 tests) covers authentication (password hashing, login
flows, account lockout), CSRF protection, rate limiting, CAPTCHA,
password strength policy, self-service and admin-assisted password
changes, SQL-injection resistance, the embedding-based face recognition
core (computation, matching, gallery storage/deletion), face alignment,
multi-face rejection, blink-based liveness, registration image quality
checks, IP allowlisting, request size limits, admin account management,
the audit log, the student profile page, bulk CSV import, session
overlap detection, pagination, reverse-proxy IP handling, and the
attendance-marking security properties (session-derived identity, 1:1
verification, no cross-identity leakage).

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
- [ ] Advanced analytics dashboard
- [ ] SMS/email notifications
- [ ] Biometric integration (fingerprint, iris)
- [ ] AI-powered attendance predictions

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