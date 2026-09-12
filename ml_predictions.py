"""AI-powered attendance predictions — a genuinely trained supervised
learning model, not a rebrand of analytics.py's rule-based risk-trend
heuristic (that module says as much in its own docstring; this is the
"separate, unimplemented item" it points to).

What this is: logistic regression, trained from scratch (gradient
descent, implemented directly with numpy rather than adding a
scikit-learn dependency for one small model — consistent with this
project's general preference for a plain implementation over a heavy
dependency where one suffices) on THIS DEPLOYMENT'S OWN historical
attendance data. There is no pre-trained model shipped with the
project and no external data source — every deployment trains (or
doesn't) on whatever history it has actually accumulated.

What this is NOT, and why it matters:
  - It is not validated against any dataset beyond this deployment's
    own history. Whatever accuracy/precision/recall numbers training
    reports are the ONLY evidence of how well it works — there is no
    external benchmark to lean on, and those numbers should be read
    skeptically when the sample size is small (which it will be for
    any new or small deployment).
  - It has a cold-start problem by construction: with too little
    history, MIN_TRAINING_EXAMPLES below refuses to train at all
    rather than fit a model on data too thin to mean anything.
  - It is a supplementary signal for an admin to weigh, never an
    automated decision — nothing in this module writes to a student's
    attendance record, and nothing here should be treated as more
    authoritative than analytics.py's transparent, example-by-example
    risk-trend view for the same purpose.

See README's "AI-Powered Attendance Predictions" section for the full
picture, including the cold-start behavior and how to read the
reported metrics.
"""
import json
from datetime import datetime

import numpy as np

MIN_TRAINING_EXAMPLES = 20
MIN_HISTORY_SESSIONS = 4
DEFAULT_RECENT_WINDOW = 5
DEFAULT_LOOKAHEAD = 5

FEATURE_NAMES = [
    'total_so_far', 'overall_rate', 'recent_rate', 'earlier_rate',
    'trend', 'current_absence_streak', 'longest_absence_streak', 'volatility',
]


def _outcomes_for_student(student_id, ordered_sessions, status_by_pair, late_counts_as_present):
    outcomes = []
    for s in ordered_sessions:
        status = status_by_pair.get((s['id'], student_id))
        counts = status == 'Present' or (status == 'Late' and late_counts_as_present)
        outcomes.append(1 if counts else 0)
    return outcomes


def _streaks(binary_seq):
    """Returns (current_trailing_absence_streak, longest_absence_streak)
    for a 0/1 sequence (1 = present)."""
    longest = current = 0
    trailing = 0
    for v in binary_seq:
        if v == 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    # trailing streak = current streak of zeros ending at the sequence's end
    for v in reversed(binary_seq):
        if v == 0:
            trailing += 1
        else:
            break
    return trailing, longest


def _features_from_history(history, recent_window=DEFAULT_RECENT_WINDOW):
    """Builds the feature vector for one (student, as-of point) example
    using ONLY the outcomes up to that point — never anything from
    after it, which would leak the label into the features."""
    total = len(history)
    overall_rate = float(np.mean(history)) if total else 0.0
    window = min(recent_window, total // 2) or 1
    recent = history[-window:]
    earlier = history[:-window]
    recent_rate = float(np.mean(recent)) if recent else overall_rate
    earlier_rate = float(np.mean(earlier)) if earlier else recent_rate
    trend = recent_rate - earlier_rate
    trailing_streak, longest_streak = _streaks(history)
    volatility = float(np.std(history)) if total else 0.0
    return [total, overall_rate, recent_rate, earlier_rate, trend, trailing_streak, longest_streak, volatility]


def build_training_examples(students, ordered_sessions, status_by_pair, late_counts_as_present,
                             threshold_percent, min_history=MIN_HISTORY_SESSIONS, lookahead=DEFAULT_LOOKAHEAD):
    """Builds (X, y) from historical data via a rolling-origin split per
    student: for every point in a student's history with at least
    `min_history` sessions behind it AND at least `lookahead` sessions
    still ahead of it, one training example is generated — features
    from everything up to that point, label from what actually
    happened over the next `lookahead` sessions. A student contributes
    zero, one, or several examples depending on how much history they
    have; a brand-new deployment with short history contributes none.
    """
    threshold_rate = threshold_percent / 100
    X, y = [], []
    for student in students:
        outcomes = _outcomes_for_student(student['id'], ordered_sessions, status_by_pair, late_counts_as_present)
        total = len(outcomes)
        for i in range(min_history, total - lookahead + 1):
            history = outcomes[:i]
            future = outcomes[i:i + lookahead]
            if not future:
                continue
            future_rate = sum(future) / len(future)
            label = 1 if future_rate < threshold_rate else 0
            X.append(_features_from_history(history))
            y.append(label)
    return np.array(X, dtype=np.float64), np.array(y, dtype=np.float64)


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


def train_logistic_regression(X, y, learning_rate=0.1, l2=0.01, epochs=2000):
    """Plain batch gradient descent with L2 regularization. Returns
    (weights, bias, feature_means, feature_stds). Features are
    standardized before training; the same means/stds must be applied
    at prediction time (see predict_proba() below)."""
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    stds[stds == 0] = 1.0
    X_std = (X - means) / stds

    n_samples, n_features = X_std.shape
    weights = np.zeros(n_features)
    bias = 0.0

    for _ in range(epochs):
        z = X_std @ weights + bias
        preds = _sigmoid(z)
        error = preds - y
        grad_w = (X_std.T @ error) / n_samples + (l2 / n_samples) * weights
        grad_b = float(np.mean(error))
        weights -= learning_rate * grad_w
        bias -= learning_rate * grad_b

    return weights, bias, means, stds


def predict_proba(X, weights, bias, means, stds):
    X_std = (X - means) / stds
    return _sigmoid(X_std @ weights + bias)


def evaluate(y_true, y_pred_proba, decision_threshold=0.5):
    y_pred = (y_pred_proba >= decision_threshold).astype(int)
    y_true = y_true.astype(int)
    tp = int(np.sum((y_pred == 1) & (y_true == 1)))
    tn = int(np.sum((y_pred == 0) & (y_true == 0)))
    fp = int(np.sum((y_pred == 1) & (y_true == 0)))
    fn = int(np.sum((y_pred == 0) & (y_true == 1)))
    accuracy = (tp + tn) / len(y_true) if len(y_true) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        'accuracy': round(accuracy, 3), 'precision': round(precision, 3),
        'recall': round(recall, 3), 'f1': round(f1, 3),
        'n_examples': len(y_true), 'n_positive': int(np.sum(y_true)),
    }


def train_and_evaluate(X, y, test_fraction=0.2, seed=42):
    """Trains on a random split of the available examples and reports
    metrics on the held-out portion — the ONLY evidence offered that
    the model does anything useful, so callers should surface these
    numbers plainly rather than hiding them behind a single "trained!"
    message. Returns (model_dict, metrics_dict, validated: bool).

    validated=False (with metrics=None) when there are too few examples
    for a meaningful held-out set at all — the model is still trained
    on everything in that case (nothing else to do with 20-40 examples)
    but its accuracy is explicitly reported as unknown rather than
    computed from a test set of 2-3 points that would be statistical
    noise.
    """
    n = len(y)
    rng = np.random.RandomState(seed)
    indices = rng.permutation(n)
    n_test = int(n * test_fraction)

    if n_test < 5:
        weights, bias, means, stds = train_logistic_regression(X, y)
        model = {'weights': weights.tolist(), 'bias': bias, 'means': means.tolist(), 'stds': stds.tolist()}
        return model, None, False

    test_idx, train_idx = indices[:n_test], indices[n_test:]
    X_train, y_train = X[train_idx], y[train_idx]
    X_test, y_test = X[test_idx], y[test_idx]

    weights, bias, means, stds = train_logistic_regression(X_train, y_train)
    test_proba = predict_proba(X_test, weights, bias, means, stds)
    metrics = evaluate(y_test, test_proba)

    # Refit on all available data for the model that actually gets
    # saved/used — the held-out split above exists purely to produce an
    # honest accuracy estimate, not to withhold data from the final model.
    weights_final, bias_final, means_final, stds_final = train_logistic_regression(X, y)
    model = {
        'weights': weights_final.tolist(), 'bias': bias_final,
        'means': means_final.tolist(), 'stds': stds_final.tolist(),
    }
    return model, metrics, True


def serialize_model(model, metrics, validated, n_examples, trained_at=None):
    return json.dumps({
        'model': model, 'metrics': metrics, 'validated': validated,
        'n_examples': n_examples, 'trained_at': trained_at or datetime.now().isoformat(),
        'feature_names': FEATURE_NAMES,
    })


def deserialize_model(blob):
    return json.loads(blob)


def predict_for_student(student, ordered_sessions, status_by_pair, late_counts_as_present, model_state):
    """Applies a stored, trained model to one student's CURRENT full
    history (there's no future data to hold back at prediction time —
    that's only relevant during training). Returns None if the student
    doesn't have enough history for a feature vector to mean anything,
    consistent with build_training_examples()'s min_history."""
    outcomes = _outcomes_for_student(student['id'], ordered_sessions, status_by_pair, late_counts_as_present)
    if len(outcomes) < MIN_HISTORY_SESSIONS:
        return None
    features = np.array([_features_from_history(outcomes)], dtype=np.float64)
    model = model_state['model']
    proba = predict_proba(
        features, np.array(model['weights']), model['bias'],
        np.array(model['means']), np.array(model['stds'])
    )
    return float(proba[0])
