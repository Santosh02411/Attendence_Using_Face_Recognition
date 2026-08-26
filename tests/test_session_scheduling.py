"""Tests for session time-conflict detection."""
import sqlite3

import app as app_module
import config as cfg
from conftest import login_as_admin


def _seed_subject_and_session(db_path, date='15-08-2026', time='10:00', end_time='11:00', end_date=None):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO subjects(id, name, code) VALUES (1, 'Math', 'M1')")
    conn.execute(
        "INSERT INTO sessions(id, subject_id, title, date, time, end_date, end_time, active) VALUES (1, 1, 'Existing', ?, ?, ?, ?, 0)",
        (date, time, end_date or date, end_time)
    )
    conn.commit()
    conn.close()


class TestFindConflictingSession:
    def test_overlapping_time_same_day_is_a_conflict(self, isolated_paths):
        _seed_subject_and_session(isolated_paths['database_path'])
        conflict = app_module.find_conflicting_session('15-08-2026', '15-08-2026', '10:30', '11:30')
        assert conflict is not None
        assert conflict['title'] == 'Existing'

    def test_adjacent_non_overlapping_time_is_not_a_conflict(self, isolated_paths):
        _seed_subject_and_session(isolated_paths['database_path'])
        conflict = app_module.find_conflicting_session('15-08-2026', '15-08-2026', '11:00', '12:00')
        assert conflict is None

    def test_different_day_is_not_a_conflict(self, isolated_paths):
        _seed_subject_and_session(isolated_paths['database_path'])
        conflict = app_module.find_conflicting_session('16-08-2026', '16-08-2026', '10:00', '11:00')
        assert conflict is None

    def test_same_day_different_time_is_not_a_conflict(self, isolated_paths):
        _seed_subject_and_session(isolated_paths['database_path'])
        conflict = app_module.find_conflicting_session('15-08-2026', '15-08-2026', '12:00', '13:00')
        assert conflict is None

    def test_fully_contained_window_is_a_conflict(self, isolated_paths):
        _seed_subject_and_session(isolated_paths['database_path'], time='09:00', end_time='17:00')
        conflict = app_module.find_conflicting_session('15-08-2026', '15-08-2026', '10:00', '11:00')
        assert conflict is not None

    def test_multi_day_date_ranges_overlap_correctly(self, isolated_paths):
        _seed_subject_and_session(isolated_paths['database_path'], date='10-08-2026', end_date='20-08-2026')
        # Candidate window starts before the existing one ends -> overlap.
        conflict = app_module.find_conflicting_session('18-08-2026', '25-08-2026', '10:30', '11:30')
        assert conflict is not None

    def test_excludes_given_session_id(self, isolated_paths):
        _seed_subject_and_session(isolated_paths['database_path'])
        # Checking session 1 against itself (e.g. when editing) should not conflict.
        conflict = app_module.find_conflicting_session('15-08-2026', '15-08-2026', '10:00', '11:00', exclude_session_id=1)
        assert conflict is None

    def test_no_existing_sessions_means_no_conflict(self, isolated_paths):
        conflict = app_module.find_conflicting_session('15-08-2026', '15-08-2026', '10:00', '11:00')
        assert conflict is None


class TestSessionCreationBlocksOverlap:
    def _create_session_form(self, date='15-08-2026', start_h=10, end_h=11):
        # admin_sessions() expects yyyy-mm-dd from the HTML date input and
        # converts internally to dd-mm-yyyy.
        y, m, d = date.split('-')[::-1]
        iso_date = f'{y}-{m}-{d}'
        return {
            'title': 'New Session', 'subject_name': 'Physics', 'subject_code': 'P1',
            'date': iso_date, 'end_date': iso_date,
            'start_h': str(start_h), 'start_m': '0', 'start_p': 'AM',
            'end_h': str(end_h), 'end_m': '0', 'end_p': 'AM',
        }

    def test_overlapping_session_is_rejected(self, client, isolated_paths):
        login_as_admin(client)
        _seed_subject_and_session(isolated_paths['database_path'], time='10:00', end_time='11:00')
        resp = client.post('/admin/sessions', data=self._create_session_form(start_h=10, end_h=11), follow_redirects=True)
        assert b'overlaps' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute("SELECT COUNT(*) FROM sessions WHERE title='New Session'").fetchone()[0]
        conn.close()
        assert count == 0

    def test_non_overlapping_session_is_created(self, client, isolated_paths):
        login_as_admin(client)
        _seed_subject_and_session(isolated_paths['database_path'], time='10:00', end_time='11:00')
        resp = client.post('/admin/sessions', data=self._create_session_form(start_h=2, end_h=3), follow_redirects=True)
        assert b'scheduled successfully' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute("SELECT COUNT(*) FROM sessions WHERE title='New Session'").fetchone()[0]
        conn.close()
        assert count == 1

    def test_overlap_check_can_be_disabled_via_config(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ALLOW_OVERLAPPING_SESSIONS', True)
        login_as_admin(client)
        _seed_subject_and_session(isolated_paths['database_path'], time='10:00', end_time='11:00')
        resp = client.post('/admin/sessions', data=self._create_session_form(start_h=10, end_h=11), follow_redirects=True)
        assert b'scheduled successfully' in resp.data.lower()
