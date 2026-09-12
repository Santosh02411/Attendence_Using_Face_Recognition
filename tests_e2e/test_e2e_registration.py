"""E2E: registers a student through the real browser UI, with
Chromium's fake camera device — see tests_e2e/conftest.py for how
face-detection/embedding are made deterministic so this doesn't depend
on the fake camera's synthetic pattern actually looking like a face.
"""
import sqlite3


def _fill_and_submit_registration(page, base_url, roll_no, email=None):
    page.goto(f'{base_url}/student/register', wait_until='load')
    page.fill('#name', 'E2E Test Student')
    page.fill('#roll_no', roll_no)
    page.fill('#branch', 'CSE')
    page.fill('#semester', '2')
    page.fill('#password', 'validPass123')
    if email:
        page.fill('#email', email)

    page.click('#registerButton')  # validates fields, then starts the camera
    page.wait_for_selector('#captureButton:not([disabled])', timeout=5000)

    for _ in range(5):
        page.click('#captureButton')
        page.wait_for_timeout(200)
    page.click('#captureButton')  # 6th click uploads/submits


def test_full_registration_flow(live_server, page):
    _fill_and_submit_registration(page, live_server['base_url'], 'E2E-001', email='e2e@example.edu')
    page.wait_for_selector('text=SUCCESS', timeout=5000)

    conn = sqlite3.connect(live_server['database_path'])
    row = conn.execute(
        "SELECT name, roll_no, email, photo_count FROM students WHERE roll_no='E2E-001'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 'E2E Test Student'
    assert row[2] == 'e2e@example.edu'
    assert row[3] == 5


def test_registration_rejects_duplicate_roll_number(live_server, page):
    base_url = live_server['base_url']
    _fill_and_submit_registration(page, base_url, 'E2E-DUP')
    page.wait_for_selector('text=SUCCESS', timeout=5000)

    _fill_and_submit_registration(page, base_url, 'E2E-DUP')
    page.wait_for_selector('.result-box.error', timeout=5000)
    assert 'already registered' in page.locator('.result-box.error').inner_text().lower()


def test_registration_button_relabels_at_five_photos(live_server, page):
    """Regression test for the "silent double-duty button" fix — the
    capture button must visibly change once 5/5 photos are captured,
    rather than quietly switching to an upload action with no cue."""
    base_url = live_server['base_url']
    page.goto(f'{base_url}/student/register', wait_until='load')
    page.fill('#name', 'Button Label Student')
    page.fill('#roll_no', 'E2E-BTN')
    page.fill('#branch', 'CSE')
    page.fill('#semester', '2')
    page.fill('#password', 'validPass123')
    page.click('#registerButton')
    page.wait_for_selector('#captureButton:not([disabled])', timeout=5000)

    for _ in range(4):
        page.click('#captureButton')
        page.wait_for_timeout(150)
    assert page.locator('#captureButton').inner_text() == 'Capture Frame'

    page.click('#captureButton')  # 5th capture
    page.wait_for_timeout(150)
    assert 'Upload' in page.locator('#captureButton').inner_text()
