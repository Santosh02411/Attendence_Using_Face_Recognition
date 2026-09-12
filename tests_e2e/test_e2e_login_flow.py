"""E2E: the student login flow, driven through a real browser.

Includes a regression test for a real bug found and fixed during
manual browser testing of this project: the student dashboard used to
render completely blank after a successful login whenever there was no
currently active session (the common case) — see
_maybe_notify_low_attendance()... no, see loadActiveSessions() in
templates/student_login.html and its fix. This test pins that behavior
down with a real page load and the real ~1s auto-reload the login page
does after a successful sign-in.
"""
import sqlite3

from werkzeug.security import generate_password_hash


def _seed_student(db_path, roll_no='E2E-LOGIN', password='validPass123'):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO students(name, roll_no, branch, semester, password) VALUES (?, ?, 'CSE', '3', ?)",
        ('Login Test Student', roll_no, generate_password_hash(password))
    )
    conn.commit()
    conn.close()


def test_login_shows_dashboard_with_no_active_sessions(live_server, page):
    base_url = live_server['base_url']
    _seed_student(live_server['database_path'])

    page.goto(f'{base_url}/student/login', wait_until='load')
    page.fill('#roll_no', 'E2E-LOGIN')
    page.fill('#password', 'validPass123')
    page.click('button[type="submit"]')

    page.wait_for_selector('text=Active Attendance Sessions', timeout=5000)
    assert page.locator('text=Welcome').count() > 0
    assert page.locator('body').inner_text().strip() != ''

    # The page also auto-reloads ~1s after a successful login (see
    # student_login.html) -- confirm the panel survives that too, not
    # just the initial JS-driven render.
    page.wait_for_timeout(1800)
    assert page.locator('text=Active Attendance Sessions').count() > 0
    assert page.locator('body').inner_text().strip() != ''


def test_login_with_wrong_password_shows_error(live_server, page):
    base_url = live_server['base_url']
    _seed_student(live_server['database_path'])

    page.goto(f'{base_url}/student/login', wait_until='load')
    page.fill('#roll_no', 'E2E-LOGIN')
    page.fill('#password', 'wrongpassword')
    page.click('button[type="submit"]')

    page.wait_for_selector('.result-box.error', timeout=5000)
    # The login form itself must still be there to retry -- the failure
    # path must never hide it the way the (now-fixed) success path once did.
    assert page.locator('#roll_no').count() == 1
