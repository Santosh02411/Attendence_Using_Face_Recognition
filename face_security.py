"""Anti-proxy / anti-spoof face-verification helpers.

Two independent defenses live here, both aimed at the same goal — making
sure the person marking attendance is an actual live human physically in
front of the camera, not a photo, a screen playing a video, or someone
else entirely:

1. Active liveness challenges (generate_liveness_challenge /
   verify_challenge_response): the server picks ONE random action per
   attempt and the captured frame burst must show that specific action
   happening. See config.py's ACTIVE_LIVENESS_* comments for why this is
   meaningfully stronger than the older passive motion+blink check alone
   against a prepared video replay.

2. Presentation-attack ("spoof") detection (compute_screen_replay_score /
   is_likely_screen_replay): a heuristic frequency-domain check for the
   pixel-grid moire pattern a phone/tablet/monitor screen produces when
   photographed by another camera, which real skin doesn't have.

Both are heuristics built on Haar-cascade face boxes and basic
image-processing signals — not a trained anti-spoofing model (none was
reachable from this project's build environment; same constraint noted in
app.py's align_face_crop() docstring). They raise the cost of a proxy
attempt; they don't guarantee it's impossible.

Kept as a separate module (rather than inline in app.py) so the pure
logic here — everything except actually generating a random token — can
be unit-tested against synthetic frame/box data without needing a real
camera, a running Flask app, or an OpenCV cascade file on disk.
"""
import secrets

import cv2
import numpy as np

CHALLENGE_PROMPTS = {
    'blink': 'Please blink naturally.',
    'head_turn': 'Slowly turn your head to one side and back.',
    'head_nod': 'Slowly nod your head (look down, then back up).',
}


def generate_liveness_challenge(enabled_types):
    """Picks one random challenge type from enabled_types and returns
    {'type', 'token', 'prompt'} — the caller (app.py) is responsible for
    storing this server-side (e.g. in the Flask session) keyed by the
    token, with a timestamp, so verify_challenge_response() can later be
    given the same type it issued rather than trusting the client to
    report it. enabled_types must be non-empty."""
    if not enabled_types:
        raise ValueError('enabled_types must not be empty')
    challenge_type = secrets.choice(list(enabled_types))
    return {
        'type': challenge_type,
        'token': secrets.token_urlsafe(24),
        'prompt': CHALLENGE_PROMPTS.get(challenge_type, 'Please follow the on-screen instruction.'),
    }


def _face_box_centers(face_boxes):
    """[(cx, cy, w, h), ...] for a list of (x, y, w, h) boxes — None
    entries (a frame where no face was detected) pass through as None."""
    centers: list = []
    for box in face_boxes:
        if box is None:
            centers.append(None)
            continue
        (x, y, w, h) = box
        centers.append((x + w / 2.0, y + h / 2.0, w, h))
    return centers


def _max_axis_shift_ratio(face_boxes, axis):
    """Largest pairwise shift along `axis` ('x' or 'y') between any two
    frames' face-box centers, expressed as a fraction of the average face
    width across those two frames — so the same physical head movement
    reads as roughly the same ratio regardless of how close the person is
    to the camera. Returns 0.0 if fewer than two valid boxes are present."""
    centers = [c for c in _face_box_centers(face_boxes) if c is not None]
    if len(centers) < 2:
        return 0.0
    axis_index = 0 if axis == 'x' else 1
    best = 0.0
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            width_avg = (centers[i][2] + centers[j][2]) / 2.0
            if width_avg <= 0:
                continue
            shift = abs(centers[i][axis_index] - centers[j][axis_index]) / width_avg
            best = max(best, shift)
    return best


def verify_head_turn(face_boxes, min_shift_ratio):
    """True if the horizontal face-box-center shift between any two
    frames in the burst is at least min_shift_ratio of the face width —
    direction-agnostic by design (either left or right satisfies it); see
    config.ACTIVE_LIVENESS_HEAD_TURN_MIN_SHIFT_RATIO for why."""
    return _max_axis_shift_ratio(face_boxes, 'x') >= min_shift_ratio


def verify_head_nod(face_boxes, min_shift_ratio):
    """Same idea as verify_head_turn, vertical axis."""
    return _max_axis_shift_ratio(face_boxes, 'y') >= min_shift_ratio


def verify_challenge_response(challenge_type, face_boxes, blink_detected, min_head_turn_ratio, min_head_nod_ratio):
    """Dispatches to the right check for challenge_type. blink_detected is
    passed in (computed by app.py's existing check_blink_transition, which
    needs the grayscale frames and eye cascade this module doesn't have)
    rather than recomputed here, so this module stays free of any direct
    OpenCV cascade dependency beyond what compute_screen_replay_score
    needs. Returns False for an unrecognized challenge_type rather than
    raising — an unrecognized type should never pass verification."""
    if challenge_type == 'blink':
        return bool(blink_detected)
    if challenge_type == 'head_turn':
        return verify_head_turn(face_boxes, min_head_turn_ratio)
    if challenge_type == 'head_nod':
        return verify_head_nod(face_boxes, min_head_nod_ratio)
    return False


def compute_screen_replay_score(gray_crop):
    """Heuristic 0+ score for how likely gray_crop shows a screen replay
    (phone/tablet/monitor photographed by another camera) rather than a
    real face — higher means more suspicious. Works by looking at the 2D
    FFT magnitude spectrum for a periodic pattern: a screen's pixel/
    subpixel grid, especially when a bit out of focus or at an angle
    (typical when someone holds a phone up to a webcam), shows up as a
    small number of unusually strong, spatially periodic peaks in the
    high-frequency band. A real face's spectrum is comparatively smooth —
    it decays gradually with frequency rather than spiking at specific
    frequencies. The score is the strongest such peak's energy divided by
    the surrounding high-frequency band's average energy: a smooth
    spectrum keeps this ratio low; a strong periodic grid pushes it high.

    Deliberately excludes the pure horizontal and vertical frequency axes
    from that band (see the on-axis mask below): ordinary JPEG
    compression's 8x8 DCT blocking is itself a periodic pattern, and its
    energy concentrates almost entirely on-axis (thin horizontal/vertical
    streaks through the spectrum's center) — without excluding it, any
    JPEG-compressed photo, including a perfectly genuine live one, can
    trip this check. A screen/monitor's pixel grid, in contrast, is
    photographed through a second lens at some angle and distance, so its
    moire pattern typically carries real off-axis energy too — excluding
    the axes filters out the JPEG confound while leaving genuine
    screen-replay evidence largely intact.

    Returns 0.0 for a crop too small to analyze meaningfully, rather than
    raising — callers should treat that as "couldn't assess, don't block
    on it" (see is_likely_screen_replay).
    """
    if gray_crop is None or gray_crop.size == 0 or min(gray_crop.shape[:2]) < 32:
        return 0.0

    # A fixed, moderate size keeps the frequency-domain scale (and
    # therefore the "high-frequency band" boundary below) comparable
    # regardless of the original crop's resolution.
    resized = cv2.resize(gray_crop, (128, 128)).astype(np.float32)

    spectrum = np.fft.fftshift(np.fft.fft2(resized))
    magnitude = np.abs(spectrum)

    center = 64
    y_idx, x_idx = np.ogrid[:128, :128]
    radius = np.sqrt((y_idx - center) ** 2 + (x_idx - center) ** 2)

    # Excludes the DC component and low frequencies (overall brightness
    # and broad shading, which carry no useful spoof signal and would
    # otherwise dominate the average), AND a thin band along the pure
    # horizontal/vertical axes (see the JPEG-blocking explanation above)
    # — only the remaining high-frequency, off-axis band is considered.
    on_axis_mask = (np.abs(y_idx - center) <= 3) | (np.abs(x_idx - center) <= 3)
    high_freq_mask = (radius > 24) & ~on_axis_mask
    band = magnitude[high_freq_mask]
    if band.size == 0:
        return 0.0

    band_mean = float(np.mean(band))
    if band_mean <= 1e-6:
        return 0.0
    band_max = float(np.max(band))
    return band_max / band_mean


def is_likely_screen_replay(gray_crop, max_ratio):
    """True if compute_screen_replay_score(gray_crop) exceeds max_ratio
    (see config.ANTI_SPOOF_MAX_PERIODICITY_RATIO)."""
    return compute_screen_replay_score(gray_crop) > max_ratio
