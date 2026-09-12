"""E2E: the full student-facing pipeline (register -> log in -> mark
attendance on a live session) through a real browser, and a separate
check that an admin can create a session through the real form.
"""
import sqlite3


def test_full_register_login_and_mark_attendance(live_server, page):
    base_url = live_server['base_url']

    # Session *scheduling* through the admin UI is covered by
    # test_admin_can_create_session_via_ui below -- this test's focus
    # is the student-facing pipeline, so the active session it marks
    # attendance against is seeded directly (same pattern the unit test
    # suite uses — see tests/test_attendance_workflow.py's
    # _seed_session()).
    conn = sqlite3.connect(live_server['database_path'])
    conn.execute("INSERT INTO subjects(id, name, code) VALUES (1, 'Test Subject', 'T101')")
    conn.execute(
        "INSERT INTO sessions(id, subject_id, title, date, time, active) VALUES (1, 1, 'Live Session', '01-01-2026', '09:00', 1)"
    )
    conn.commit()
    conn.close()

    # --- Register ---
    page.goto(f'{base_url}/student/register', wait_until='load')
    page.fill('#name', 'Attend Test Student')
    page.fill('#roll_no', 'E2E-ATT')
    page.fill('#branch', 'CSE')
    page.fill('#semester', '3')
    page.fill('#password', 'validPass123')
    page.click('#registerButton')
    page.wait_for_selector('#captureButton:not([disabled])', timeout=5000)
    for _ in range(5):
        page.click('#captureButton')
        page.wait_for_timeout(200)
    page.click('#captureButton')
    page.wait_for_selector('text=SUCCESS', timeout=5000)

    # --- Log in ---
    page.goto(f'{base_url}/student/login', wait_until='load')
    page.fill('#roll_no', 'E2E-ATT')
    page.fill('#password', 'validPass123')
    page.click('button[type="submit"]')
    page.wait_for_selector('text=Active Attendance Sessions', timeout=5000)

    # --- Mark attendance on the active session ---
    page.click('text=Mark Attendance')
    page.wait_for_url('**/student/attend*', timeout=5000)
    page.wait_for_selector('#markAttendanceBtn', timeout=5000)
    page.wait_for_timeout(500)  # let getUserMedia settle before the click below
    page.click('#markAttendanceBtn')
    page.wait_for_selector('text=Attendance Marked', timeout=10000)

    conn = sqlite3.connect(live_server['database_path'])
    row = conn.execute("SELECT status FROM attendance WHERE session_id=1").fetchone()
    conn.close()
    assert row is not None
    assert row[0] in ('Present', 'Late')


def test_admin_can_create_session_via_ui(live_server, page):
    base_url = live_server['base_url']

    page.goto(f'{base_url}/login', wait_until='load')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    page.wait_for_selector('text=Admin Dashboard', timeout=5000)

    page.goto(f'{base_url}/admin/sessions', wait_until='load')
    page.fill('input[name="title"]', 'E2E Created Session')
    page.fill('input[name="subject_name"]', 'E2E Subject')
    page.fill('input[name="subject_code"]', 'E2E101')
    page.fill('input[name="date"]', '2026-06-01')
    page.fill('input[name="end_date"]', '2026-06-01')
    page.select_option('select[name="start_h"]', '9')
    page.select_option('select[name="start_m"]', '0')
    page.select_option('select[name="start_p"]', 'AM')
    page.select_option('select[name="end_h"]', '10')
    page.select_option('select[name="end_m"]', '0')
    page.select_option('select[name="end_p"]', 'AM')
    page.click('button:has-text("Create Session")')

    page.wait_for_selector('text=E2E Created Session', timeout=5000)

    conn = sqlite3.connect(live_server['database_path'])
    row = conn.execute("SELECT title FROM sessions WHERE title='E2E Created Session'").fetchone()
    conn.close()
    assert row is not None
