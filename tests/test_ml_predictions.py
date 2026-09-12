"""Tests for ml_predictions.py (a genuinely trained logistic-regression
model — see that module's docstring for what it is and isn't) and the
/admin/ml-predictions routes built on it.
"""
import sqlite3

import numpy as np

import ml_predictions as mlp
from tests.conftest import login_as_admin


def _make_history(rng, n_sessions, prob_schedule):
    """Builds a {(session_id, student_id): status} slice for one
    student given a per-session probability-of-present schedule."""
    outcomes = {}
    for sid, p in enumerate(prob_schedule[:n_sessions]):
        if rng.random() < p:
            outcomes[sid] = 'Present'
    return outcomes


class TestFeatureEngineering:
    def test_features_reflect_recent_decline(self):
        history = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
        features = mlp._features_from_history(history)
        names = mlp.FEATURE_NAMES
        as_dict = dict(zip(names, features))
        assert as_dict['trend'] < 0  # recent worse than earlier
        assert as_dict['current_absence_streak'] == 5

    def test_streaks_helper(self):
        trailing, longest = mlp._streaks([1, 0, 0, 1, 0, 0, 0])
        assert trailing == 3
        assert longest == 3

    def test_streaks_all_present(self):
        trailing, longest = mlp._streaks([1, 1, 1])
        assert trailing == 0
        assert longest == 0


class TestBuildTrainingExamples:
    def test_no_examples_when_history_too_short(self):
        students = [{'id': 1}]
        sessions = [{'id': i} for i in range(3)]
        X, y = mlp.build_training_examples(students, sessions, {}, False, 75.0)
        assert X.shape == (0,)
        assert y.shape == (0,)

    def test_generates_examples_when_enough_history(self):
        students = [{'id': 1}]
        sessions = [{'id': i} for i in range(20)]
        status = {(i, 1): 'Present' for i in range(20) if i % 2 == 0}
        X, y = mlp.build_training_examples(students, sessions, status, False, 75.0,
                                            min_history=4, lookahead=5)
        assert X.shape[0] > 0
        assert X.shape[1] == len(mlp.FEATURE_NAMES)

    def test_label_reflects_future_not_past(self):
        """A student who was struggling early but is 100% present for
        the whole lookahead window must be labeled 0 (not at risk),
        proving the label comes from the future window, not from
        overall/earlier history."""
        students = [{'id': 1}]
        n = 15
        sessions = [{'id': i} for i in range(n)]
        # First 8 sessions: absent (bad history). Last 7: all present.
        status = {(i, 1): 'Present' for i in range(8, n)}
        X, y = mlp.build_training_examples(students, sessions, status, False, 75.0,
                                            min_history=4, lookahead=5)
        # The example generated at i=8 (features from a bad history,
        # label from an all-present future) must be labeled 0.
        assert 0 in y
        assert 1 in y or len(y) == 1  # sanity: some variety expected in general


class TestTrainAndEvaluate:
    def test_model_learns_a_real_pattern(self):
        """Sanity check the whole pipeline against clearly separable
        synthetic data: stable-good vs chronically-low students should
        be distinguishable well above chance."""
        rng = np.random.RandomState(0)
        sessions = [{'id': i} for i in range(40)]
        students = []
        status = {}
        sid = 1
        for _ in range(10):
            students.append({'id': sid})
            for i in range(40):
                if rng.random() < 0.92:
                    status[(i, sid)] = 'Present'
            sid += 1
        for _ in range(10):
            students.append({'id': sid})
            for i in range(40):
                if rng.random() < 0.15:
                    status[(i, sid)] = 'Present'
            sid += 1

        X, y = mlp.build_training_examples(students, sessions, status, False, 75.0)
        assert len(y) >= mlp.MIN_TRAINING_EXAMPLES
        model, metrics, validated = mlp.train_and_evaluate(X, y)
        assert validated is True
        assert metrics['accuracy'] > 0.75  # well above chance on a clearly separable pattern

    def test_small_sample_is_not_falsely_validated(self):
        rng = np.random.RandomState(1)
        X_small = rng.rand(15, len(mlp.FEATURE_NAMES))
        y_small = (X_small[:, 1] > 0.5).astype(float)
        model, metrics, validated = mlp.train_and_evaluate(X_small, y_small)
        assert validated is False
        assert metrics is None
        assert 'weights' in model  # still trained on everything, just not scored


class TestPredictForStudent:
    def test_returns_none_for_insufficient_history(self):
        student = {'id': 1}
        sessions = [{'id': i} for i in range(2)]
        model_state = {'model': {
            'weights': [0.0] * len(mlp.FEATURE_NAMES), 'bias': 0.0,
            'means': [0.0] * len(mlp.FEATURE_NAMES), 'stds': [1.0] * len(mlp.FEATURE_NAMES),
        }}
        result = mlp.predict_for_student(student, sessions, {}, False, model_state)
        assert result is None

    def test_returns_probability_for_sufficient_history(self):
        student = {'id': 1}
        sessions = [{'id': i} for i in range(10)]
        status = {(i, 1): 'Present' for i in range(10)}
        model_state = {'model': {
            'weights': [0.0] * len(mlp.FEATURE_NAMES), 'bias': 0.0,
            'means': [0.0] * len(mlp.FEATURE_NAMES), 'stds': [1.0] * len(mlp.FEATURE_NAMES),
        }}
        result = mlp.predict_for_student(student, sessions, status, False, model_state)
        assert result is not None
        assert 0.0 <= result <= 1.0


class TestSerializeDeserialize:
    def test_roundtrip(self):
        model = {'weights': [0.1, 0.2], 'bias': 0.5, 'means': [1.0, 2.0], 'stds': [1.0, 1.0]}
        blob = mlp.serialize_model(model, {'accuracy': 0.9}, True, 50, trained_at='2026-01-01T00:00:00')
        restored = mlp.deserialize_model(blob)
        assert restored['model'] == model
        assert restored['validated'] is True
        assert restored['n_examples'] == 50


class TestMlPredictionsRoutes:
    def _seed_history(self, db_path, n_sessions=40):
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO subjects(id, name, code) VALUES (1, 'Sub', 'S1')")
        rng = np.random.RandomState(2)
        for i in range(1, n_sessions + 1):
            conn.execute(
                "INSERT INTO sessions(id, subject_id, title, date, time, active) VALUES (?, 1, ?, '01-01-2026', '09:00', 0)",
                (i, f'Sess{i}')
            )
        for sid in range(1, 21):
            branch = 'CSE'
            conn.execute(
                "INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?, ?, ?, ?, '1', 'x')",
                (sid, f'Student{sid}', f'R{sid}', branch)
            )
            prob = 0.9 if sid <= 10 else 0.15
            for i in range(1, n_sessions + 1):
                if rng.random() < prob:
                    conn.execute(
                        "INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (?, ?, 'Present', 'x')",
                        (sid, i)
                    )
        conn.commit()
        conn.close()

    def test_requires_admin_login(self, client, isolated_paths):
        resp = client.get('/admin/ml-predictions', follow_redirects=False)
        assert resp.status_code == 302

    def test_shows_untrained_state(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.get('/admin/ml-predictions')
        assert resp.status_code == 200
        assert b'No model has been trained yet' in resp.data

    def test_train_with_insufficient_data_shows_error(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.post('/admin/ml-predictions/train', follow_redirects=True)
        assert b'Not enough historical data' in resp.data

    def test_train_and_view_predictions(self, client, isolated_paths):
        self._seed_history(isolated_paths['database_path'])
        login_as_admin(client)
        resp = client.post('/admin/ml-predictions/train', follow_redirects=True)
        assert resp.status_code == 200

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT * FROM ml_model_state WHERE id=1').fetchone()
        conn.close()
        assert row is not None

        resp2 = client.get('/admin/ml-predictions')
        assert b'Predicted Risk' in resp2.data
        assert b'R1' in resp2.data  # a student with enough history appears

    def test_retrain_replaces_existing_model(self, client, isolated_paths):
        self._seed_history(isolated_paths['database_path'])
        login_as_admin(client)
        client.post('/admin/ml-predictions/train')
        client.post('/admin/ml-predictions/train')

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM ml_model_state').fetchone()[0]
        conn.close()
        assert count == 1  # replaced, not accumulated
