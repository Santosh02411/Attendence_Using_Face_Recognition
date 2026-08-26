"""
One-time migration: backfill face_embeddings for students who were
registered under the old LBPH system.

The old system's per-registration face crops are still sitting in
Datasets/User.<student_id>.<n>.jpg — this script re-detects a face in
each of those images and computes+stores an embedding for it using the
new OpenFace model, so existing students don't have to re-register.

Safe to re-run: it skips any image whose embedding has already been
migrated (tracked in database/app.db under a small marker table), so
running it twice won't create duplicate embedding rows.

Usage:
    python migrate_embeddings.py
"""
import os
import sqlite3
import sys

import cv2
import numpy as np
from PIL import Image

import config as cfg
from app import compute_embedding, store_embedding, init_databases


def _ensure_migration_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS migrated_embedding_sources(
        filename TEXT PRIMARY KEY
    )''')
    conn.commit()


def _already_migrated(conn, filename):
    row = conn.execute(
        'SELECT 1 FROM migrated_embedding_sources WHERE filename=?', (filename,)
    ).fetchone()
    return row is not None


def _mark_migrated(conn, filename):
    conn.execute(
        'INSERT OR IGNORE INTO migrated_embedding_sources(filename) VALUES (?)', (filename,)
    )
    conn.commit()


def main():
    init_databases()

    if not os.path.isdir(cfg.DATA_DIR):
        print(f'No Datasets directory found at {cfg.DATA_DIR} — nothing to migrate.')
        return

    image_files = [f for f in os.listdir(cfg.DATA_DIR) if f.lower().endswith('.jpg')]
    if not image_files:
        print('No images found in Datasets/ — nothing to migrate.')
        return

    conn = sqlite3.connect(cfg.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_migration_table(conn)

    face_cascade = cv2.CascadeClassifier(cfg.CASCADE_PATH)

    migrated, skipped, failed = 0, 0, 0
    for filename in sorted(image_files):
        if _already_migrated(conn, filename):
            skipped += 1
            continue

        # Expected format: User.<student_id>.<n>.jpg
        parts = filename.split('.')
        if len(parts) < 3:
            failed += 1
            continue
        try:
            student_id = int(parts[1])
        except ValueError:
            failed += 1
            continue

        path = os.path.join(cfg.DATA_DIR, filename)
        try:
            img = Image.open(path).convert('RGB')
        except Exception as e:
            print(f'  [skip] {filename}: could not open ({e})')
            failed += 1
            continue

        frame = np.array(img)

        # Old-format images were saved as grayscale-only face crops (3
        # identical channels after convert('RGB')), which the embedder can
        # still process, just with somewhat lower quality than a genuine
        # color crop. New registrations save real color crops instead.
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY) if frame.ndim == 3 else frame
        faces = face_cascade.detectMultiScale(gray, **cfg.REGISTRATION_DETECT_PARAMS)

        if len(faces) > 0:
            faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
            (x, y, w, h) = faces[0]
            face_crop = frame[y:y+h, x:x+w]
        else:
            # Already a tight crop from the old pipeline — use as-is.
            face_crop = frame

        try:
            embedding = compute_embedding(face_crop)
            store_embedding(student_id, embedding)
            _mark_migrated(conn, filename)
            migrated += 1
            print(f'  [ok] {filename} -> student {student_id}')
        except Exception as e:
            print(f'  [fail] {filename}: {e}')
            failed += 1

    conn.close()
    print(f'\nDone. Migrated: {migrated}, already done: {skipped}, failed: {failed}.')
    if failed:
        print('Failed images were left alone — check them manually if needed.')


if __name__ == '__main__':
    sys.exit(main())
