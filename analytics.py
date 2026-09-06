"""Deeper attendance analytics: cohort comparison and risk-trend
predictions, built on top of the same session/attendance data as the
existing /admin/reports page (see _compute_attendance_report() in
app.py, which this module calls rather than re-querying the database
directly, to stay consistent with its established filtering rules and
avoid a second, possibly-diverging implementation).

Both pieces here are transparent, rule-based calculations — NOT a
machine-learning model. That distinction matters: the README's "Future
Enhancements" list separately calls out "AI-powered attendance
predictions" as a still-unimplemented, larger undertaking. What's here
is a linear trend/extrapolation heuristic, deliberately kept simple and
explainable (an admin can see exactly why a student is flagged), in the
same spirit as this project's existing risk-score security feature.
"""
from math import ceil


def aggregate_cohort(report_rows, field):
    """Groups per-student report rows (as returned by
    _compute_attendance_report()) by a field — 'branch' or 'semester' —
    and averages their attendance percentages. Rows with no value for
    the field are grouped under 'Unspecified' rather than dropped, so a
    student record with a blank branch/semester still shows up
    somewhere instead of silently vanishing from the comparison."""
    groups = {}
    for row in report_rows:
        key = row.get(field) or 'Unspecified'
        g = groups.setdefault(key, {'student_count': 0, 'pct_sum': 0.0, 'below_threshold_count': 0})
        g['student_count'] += 1
        g['pct_sum'] += row['percentage']
        if row['below_threshold']:
            g['below_threshold_count'] += 1

    cohorts = []
    for key, g in groups.items():
        cohorts.append({
            'label': key,
            'student_count': g['student_count'],
            'average_percentage': round(g['pct_sum'] / g['student_count'], 1) if g['student_count'] else 0.0,
            'below_threshold_count': g['below_threshold_count'],
        })
    cohorts.sort(key=lambda c: c['average_percentage'])
    return cohorts


def build_subject_cohorts(subjects, compute_report_fn, start_date=None, end_date=None):
    """Builds the per-subject cohort comparison by calling
    compute_report_fn (i.e. _compute_attendance_report) once per
    subject — subjects with no sessions in the filtered range are
    skipped rather than shown as a misleading 0%."""
    cohorts = []
    for subject in subjects:
        rows, _trend = compute_report_fn(subject_id=subject['id'], start_date=start_date, end_date=end_date)
        if not rows:
            continue
        avg = round(sum(r['percentage'] for r in rows) / len(rows), 1)
        below = sum(1 for r in rows if r['below_threshold'])
        cohorts.append({
            'label': f"{subject['name']} ({subject['code']})",
            'student_count': len(rows),
            'average_percentage': avg,
            'below_threshold_count': below,
        })
    cohorts.sort(key=lambda c: c['average_percentage'])
    return cohorts


def _session_outcomes_for_student(student_id, ordered_sessions, status_by_pair, late_counts_as_present):
    """Returns a chronological list of 1s and 0s (present/late-counts-
    vs-absent) for one student across ordered_sessions — mirrors
    _attendance_counts_as_present()'s rule (status == 'Present', or
    'Late' only when LATE_COUNTS_AS_PRESENT is on) rather than
    hardcoding it, so this stays consistent with the rest of the app's
    reports if that setting is ever changed."""
    outcomes = []
    for s in ordered_sessions:
        status = status_by_pair.get((s['id'], student_id))
        counts = status == 'Present' or (status == 'Late' and late_counts_as_present)
        outcomes.append(1 if counts else 0)
    return outcomes


def compute_risk_predictions(students, ordered_sessions, status_by_pair, late_counts_as_present,
                              threshold_percent, min_sessions=4, recent_window=5, lookahead_sessions=5,
                              decline_flag_points=10.0):
    """For each student with enough session history, compares their
    attendance rate over their most recent `recent_window` sessions
    against their rate over everything before that, and — if the
    recent rate is declining — projects forward `lookahead_sessions`
    more sessions at that same recent rate to see whether their
    overall percentage would cross below threshold_percent.

    This is intentionally NOT a machine-learning forecast: it is a
    plain "if recent behavior continues" extrapolation, chosen so the
    reasoning behind every flagged student is directly inspectable
    (their own recent-vs-earlier numbers), rather than a model output
    an admin has to take on faith.

    Returns a list of dicts, most urgent first (Critical > High >
    Medium), each with the student's identity, current/recent/earlier
    percentages, a risk_level, and — when applicable — an estimated
    number of sessions until they'd cross the threshold if nothing
    changes. A student already below threshold but clearly recovering
    (a positive trend) is left off this list — that current-state fact
    is already covered by the dashboard's Low Attendance Alerts, and
    showing them here as "Critical" next to an upward trend arrow would
    read as contradictory.
    """
    predictions = []
    for student in students:
        outcomes = _session_outcomes_for_student(student['id'], ordered_sessions, status_by_pair, late_counts_as_present)
        total = len(outcomes)
        if total < min_sessions:
            continue

        present_count = sum(outcomes)
        current_pct = round(present_count / total * 100, 1)

        window = min(recent_window, total // 2) or 1
        recent_outcomes = outcomes[-window:]
        earlier_outcomes = outcomes[:-window]
        recent_pct = round(sum(recent_outcomes) / len(recent_outcomes) * 100, 1)
        earlier_pct = (
            round(sum(earlier_outcomes) / len(earlier_outcomes) * 100, 1)
            if earlier_outcomes else recent_pct
        )
        trend_delta = round(recent_pct - earlier_pct, 1)

        # "If the next `lookahead_sessions` sessions go the same way as
        # the last `window` did" — a simple forward projection, not a
        # statistical forecast.
        recent_rate = recent_pct / 100
        projected_present = present_count + recent_rate * lookahead_sessions
        projected_total = total + lookahead_sessions
        projected_pct = round(projected_present / projected_total * 100, 1)

        sessions_until_threshold = None
        threshold_rate = threshold_percent / 100
        if current_pct >= threshold_percent and recent_rate < threshold_rate:
            denominator = recent_rate - threshold_rate  # negative
            numerator = threshold_rate * total - present_count  # <= 0
            if denominator != 0:
                sessions_until_threshold = max(0, ceil(numerator / denominator))

        if current_pct < threshold_percent:
            # Below threshold is already surfaced by the dashboard's
            # existing Low Attendance Alerts regardless of trend — this
            # feature's job is the trend signal specifically, so a
            # student who is below threshold but clearly recovering
            # (a positive trend_delta) is deliberately left off this
            # list rather than being shown as "Critical" next to a
            # trend arrow pointing the right way, which would read as
            # contradictory and undermine trust in the feature.
            risk_level = None if trend_delta > 0 else 'Critical'
        elif projected_pct < threshold_percent:
            risk_level = 'High'
        elif trend_delta <= -decline_flag_points:
            risk_level = 'Medium'
        else:
            risk_level = None

        if risk_level is None:
            continue  # stable, improving, or already recovering — not worth an admin's attention here

        predictions.append({
            'student_id': student['id'],
            'name': student['name'],
            'roll_no': student['roll_no'],
            'branch': student['branch'],
            'semester': student['semester'],
            'total_sessions': total,
            'current_percentage': current_pct,
            'recent_percentage': recent_pct,
            'earlier_percentage': earlier_pct,
            'trend_delta': trend_delta,
            'projected_percentage': projected_pct,
            'sessions_until_threshold': sessions_until_threshold,
            'risk_level': risk_level,
        })

    risk_order = {'Critical': 0, 'High': 1, 'Medium': 2}
    predictions.sort(key=lambda p: (risk_order.get(p['risk_level'], 9), p['current_percentage']))
    return predictions
