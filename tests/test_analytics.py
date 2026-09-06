"""Tests for analytics.py (cohort comparison, risk-trend predictions)
and the /admin/analytics route built on top of it.

compute_risk_predictions() is tested as a pure function with hand-built
outcome sequences (see the module docstring in analytics.py for the
exact formulas) rather than through seeded attendance rows, so each
test case's expected numbers can be verified by hand.
"""
import sqlite3

import analytics
from tests.conftest import login_as_admin


def _session(session_id):
    return {'id': session_id}


def _status_map_from_outcomes(student_id, outcomes, late_as_present_marker=False):
    """Builds a {(session_id, student_id): status} map from a list of
    0/1 outcomes. When late_as_present_marker is True, a 1 is encoded
    as 'Late' instead of 'Present', to test the late_counts_as_present
    plumbing specifically."""
    present_label = 'Late' if late_as_present_marker else 'Present'
    status_by_pair = {}
    for i, outcome in enumerate(outcomes):
        if outcome:
            status_by_pair[(i, student_id)] = present_label
        # else: no row at all -> absent, matching how the app itself
        # never writes an explicit "Absent" attendance row.
    return status_by_pair


def _ordered_sessions(n):
    return [_session(i) for i in range(n)]


class TestAggregateCohort:
    def test_groups_and_averages_by_field(self):
        rows = [
            {'branch': 'CSE', 'percentage': 80.0, 'below_threshold': False},
            {'branch': 'CSE', 'percentage': 60.0, 'below_threshold': True},
            {'branch': 'ECE', 'percentage': 90.0, 'below_threshold': False},
        ]
        cohorts = analytics.aggregate_cohort(rows, 'branch')
        by_label = {c['label']: c for c in cohorts}
        assert by_label['CSE']['average_percentage'] == 70.0
        assert by_label['CSE']['student_count'] == 2
        assert by_label['CSE']['below_threshold_count'] == 1
        assert by_label['ECE']['average_percentage'] == 90.0

    def test_sorted_worst_first(self):
        rows = [
            {'branch': 'A', 'percentage': 95.0, 'below_threshold': False},
            {'branch': 'B', 'percentage': 40.0, 'below_threshold': True},
        ]
        cohorts = analytics.aggregate_cohort(rows, 'branch')
        assert [c['label'] for c in cohorts] == ['B', 'A']

    def test_missing_field_grouped_as_unspecified(self):
        rows = [{'branch': None, 'percentage': 50.0, 'below_threshold': True}]
        cohorts = analytics.aggregate_cohort(rows, 'branch')
        assert cohorts[0]['label'] == 'Unspecified'


class TestBuildSubjectCohorts:
    def test_skips_subjects_with_no_sessions_in_range(self):
        def fake_compute_report(subject_id=None, start_date=None, end_date=None):
            if subject_id == 1:
                return [{'percentage': 80.0, 'below_threshold': False}], []
            return [], []  # subject 2 has no sessions in range

        subjects = [{'id': 1, 'name': 'Data Structures', 'code': 'CS201'},
                    {'id': 2, 'name': 'Algorithms', 'code': 'CS202'}]
        cohorts = analytics.build_subject_cohorts(subjects, fake_compute_report)
        assert len(cohorts) == 1
        assert 'Data Structures' in cohorts[0]['label']

    def test_averages_across_students_in_subject(self):
        def fake_compute_report(subject_id=None, start_date=None, end_date=None):
            return [
                {'percentage': 100.0, 'below_threshold': False},
                {'percentage': 50.0, 'below_threshold': True},
            ], []

        subjects = [{'id': 1, 'name': 'X', 'code': 'X1'}]
        cohorts = analytics.build_subject_cohorts(subjects, fake_compute_report)
        assert cohorts[0]['average_percentage'] == 75.0
        assert cohorts[0]['below_threshold_count'] == 1


class TestComputeRiskPredictions:
    def test_already_below_threshold_is_critical(self):
        student = {'id': 1, 'name': 'Alice', 'roll_no': 'R1', 'branch': 'CSE', 'semester': '3'}
        outcomes = [1, 1, 0, 0, 0, 0]  # 2/6 = 33.3%
        status_by_pair = _status_map_from_outcomes(1, outcomes)
        preds = analytics.compute_risk_predictions(
            [student], _ordered_sessions(6), status_by_pair,
            late_counts_as_present=False, threshold_percent=75.0,
        )
        assert len(preds) == 1
        assert preds[0]['risk_level'] == 'Critical'
        assert preds[0]['current_percentage'] == round(2 / 6 * 100, 1)

    def test_declining_trend_projected_below_threshold_is_high(self):
        student = {'id': 2, 'name': 'Bob', 'roll_no': 'R2', 'branch': 'ECE', 'semester': '5'}
        outcomes = [1] * 11 + [1, 1, 0, 0, 0]  # earlier 11/11, recent 2/5
        status_by_pair = _status_map_from_outcomes(2, outcomes)
        preds = analytics.compute_risk_predictions(
            [student], _ordered_sessions(16), status_by_pair,
            late_counts_as_present=False, threshold_percent=75.0,
            recent_window=5, lookahead_sessions=5,
        )
        assert len(preds) == 1
        p = preds[0]
        assert p['current_percentage'] == round(13 / 16 * 100, 1)
        assert p['recent_percentage'] == 40.0
        assert p['earlier_percentage'] == 100.0
        assert p['risk_level'] == 'High'
        assert p['projected_percentage'] < 75.0
        assert p['sessions_until_threshold'] == 3

    def test_declining_but_not_projected_to_cross_is_medium(self):
        student = {'id': 3, 'name': 'Cara', 'roll_no': 'R3', 'branch': 'CSE', 'semester': '1'}
        outcomes = [1, 1, 1, 1, 1, 1, 1, 0]  # earlier 4/4, recent 3/4
        status_by_pair = _status_map_from_outcomes(3, outcomes)
        preds = analytics.compute_risk_predictions(
            [student], _ordered_sessions(8), status_by_pair,
            late_counts_as_present=False, threshold_percent=75.0,
            recent_window=5, lookahead_sessions=5,
        )
        assert len(preds) == 1
        p = preds[0]
        assert p['current_percentage'] == 87.5
        assert p['recent_percentage'] == 75.0
        assert p['earlier_percentage'] == 100.0
        assert p['risk_level'] == 'Medium'
        assert p['sessions_until_threshold'] is None

    def test_stable_attendance_is_not_flagged(self):
        student = {'id': 4, 'name': 'Dev', 'roll_no': 'R4', 'branch': 'CSE', 'semester': '1'}
        outcomes = [1] * 8  # perfect, no decline
        status_by_pair = _status_map_from_outcomes(4, outcomes)
        preds = analytics.compute_risk_predictions(
            [student], _ordered_sessions(8), status_by_pair,
            late_counts_as_present=False, threshold_percent=75.0,
        )
        assert preds == []

    def test_below_threshold_but_improving_is_not_flagged(self):
        # Below threshold overall, but the recent window is a clear
        # recovery (earlier much worse than recent) -- this is the
        # existing dashboard's Low Attendance Alerts territory, not a
        # "declining trend" this feature should also raise.
        student = {'id': 9, 'name': 'Ivy', 'roll_no': 'R9', 'branch': 'ECE', 'semester': '5'}
        outcomes = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1]  # earlier 0/7, recent 5/5
        status_by_pair = _status_map_from_outcomes(9, outcomes)
        preds = analytics.compute_risk_predictions(
            [student], _ordered_sessions(12), status_by_pair,
            late_counts_as_present=False, threshold_percent=75.0,
        )
        assert preds == []

    def test_too_few_sessions_excluded(self):
        student = {'id': 5, 'name': 'Eve', 'roll_no': 'R5', 'branch': 'CSE', 'semester': '1'}
        outcomes = [0, 0]  # below min_sessions default of 4
        status_by_pair = _status_map_from_outcomes(5, outcomes)
        preds = analytics.compute_risk_predictions(
            [student], _ordered_sessions(2), status_by_pair,
            late_counts_as_present=False, threshold_percent=75.0,
        )
        assert preds == []

    def test_late_counts_as_present_flag_is_honored(self):
        student = {'id': 6, 'name': 'Fay', 'roll_no': 'R6', 'branch': 'CSE', 'semester': '1'}
        outcomes = [1, 1, 1, 1]  # encoded as 'Late' below
        status_by_pair = _status_map_from_outcomes(6, outcomes, late_as_present_marker=True)

        preds_counted = analytics.compute_risk_predictions(
            [student], _ordered_sessions(4), status_by_pair,
            late_counts_as_present=True, threshold_percent=75.0,
        )
        assert preds_counted == []  # 100% attendance, nothing to flag

        preds_not_counted = analytics.compute_risk_predictions(
            [student], _ordered_sessions(4), status_by_pair,
            late_counts_as_present=False, threshold_percent=75.0,
        )
        assert len(preds_not_counted) == 1
        assert preds_not_counted[0]['current_percentage'] == 0.0
        assert preds_not_counted[0]['risk_level'] == 'Critical'

    def test_results_sorted_most_urgent_first(self):
        critical = {'id': 7, 'name': 'Gus', 'roll_no': 'R7', 'branch': 'CSE', 'semester': '1'}
        medium = {'id': 8, 'name': 'Hana', 'roll_no': 'R8', 'branch': 'CSE', 'semester': '1'}
        status_by_pair = {}
        status_by_pair.update(_status_map_from_outcomes(7, [0, 0, 0, 0]))  # 0% -> Critical
        status_by_pair.update(_status_map_from_outcomes(8, [1, 1, 1, 1, 1, 1, 1, 0]))  # -> Medium
        preds = analytics.compute_risk_predictions(
            [critical, medium], _ordered_sessions(8), status_by_pair,
            late_counts_as_present=False, threshold_percent=75.0,
        )
        assert [p['risk_level'] for p in preds] == ['Critical', 'Medium']


class TestAdminAnalyticsRoute:
    def _seed(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO subjects(id, name, code) VALUES (1, 'Data Structures', 'CS201')")
        conn.execute(
            "INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES "
            "(1, 'Declining Dana', 'RD1', 'CSE', '3', 'x')"
        )
        conn.execute(
            "INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES "
            "(2, 'Steady Sam', 'RS1', 'ECE', '5', 'x')"
        )
        # 8 sessions; Dana's attendance declines over time, Sam's is perfect throughout.
        for i in range(1, 9):
            conn.execute(
                "INSERT INTO sessions(id, subject_id, title, date, time, active) VALUES (?, 1, ?, ?, '10:00', 0)",
                (i, f'Sess{i}', f'0{i}-01-2026' if i < 10 else f'{i}-01-2026')
            )
            conn.execute(
                "INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (2, ?, 'Present', 'x')",
                (i,)
            )
            if i <= 4:
                conn.execute(
                    "INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, ?, 'Present', 'x')",
                    (i,)
                )
            # Dana has no rows for sessions 5-8 -> counted absent -> declining trend
        conn.commit()
        conn.close()

    def test_requires_admin_login(self, client):
        resp = client.get('/admin/analytics', follow_redirects=False)
        assert resp.status_code == 302

    def test_renders_with_cohort_and_risk_data(self, client, isolated_paths):
        self._seed(isolated_paths['database_path'])
        login_as_admin(client)
        resp = client.get('/admin/analytics')
        assert resp.status_code == 200
        assert b'Declining Dana' in resp.data
        assert b'Steady Sam' not in resp.data  # stable/perfect attendance isn't flagged
        assert b'CSE' in resp.data
        assert b'Data Structures' in resp.data

    def test_date_range_filter_is_applied(self, client, isolated_paths):
        self._seed(isolated_paths['database_path'])
        login_as_admin(client)
        resp = client.get('/admin/analytics?start_date=2026-01-01&end_date=2026-01-04')
        assert resp.status_code == 200
        # Only Dana's fully-present window is in range -> not enough
        # decline signal (and Sam is never flagged either way).
        assert b'Steady Sam' not in resp.data

    def test_reports_page_links_to_analytics(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.get('/admin/reports')
        assert b'/admin/analytics' in resp.data
