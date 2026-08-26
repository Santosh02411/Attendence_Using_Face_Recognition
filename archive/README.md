# Archive

Code kept for history/reference. **Nothing here is imported or used by the
running Flask app (`app.py`)** — all of this logic was superseded by the
web app and its `config.py`/`app.py` implementation.

## `legacy_scripts/`

The original standalone, command-line version of this project, from before
it became a Flask web app. Kept for reference / to show project history —
not maintained, not tested against the current dependencies, and not
wired into anything.

- `Dataset_Creator.py` — CLI script that captured webcam frames and saved
  cropped face images to disk (OpenCV `VideoCapture` + Haar cascade).
  Superseded by `save_face_images()` in `app.py`, which does the same job
  through the browser instead of a local webcam window.
- `Trainer.py` — CLI script that trained an LBPH recognizer from the saved
  face images. Superseded by `train_recognizer()` in `app.py`.
- `Detector.py` — CLI script that ran live recognition against a webcam
  feed and printed/looked up matches. Superseded by `recognize_face()` in
  `app.py`.
- `postAttendence.py` / `post_date_time.py` — CLI scripts that wrote
  recognized attendance directly into a SQLite table. Superseded by the
  attendance routes in `app.py` (`/student/attend/mark`,
  `/admin/session/<id>/recognize`).
- `Basic Programs/` — small exploratory OpenCV scripts (plain face
  detection, face+eye detection) written while first learning the Haar
  cascade API. Not part of the project pipeline at all.

If you just want to run the project, ignore this folder entirely — see the
main [README.md](../README.md) for setup instructions.
