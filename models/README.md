# Face embedding model

**`openface_nn4.small2.v1.t7`** — a pretrained deep CNN from the
[OpenFace project](https://cmusatyalab.github.io/openface/) (CMU), loaded via
OpenCV's built-in `cv2.dnn.readNetFromTorch()`. Given a 96×96 face crop, it
outputs a 128-dimensional, L2-normalized embedding vector. Two embeddings
from the same person land close together in that space (high cosine
similarity); embeddings from different people land far apart.

- **Source**: mirrored from the official OpenFace release, originally
  hosted at `https://storage.cmusatyalab.org/openface-models/nn4.small2.v1.t7`.
- **Integrity**: MD5 `c95bfd8cc1adf05210e979ff623013b6`, matching the
  checksum published in OpenFace's own [`get-models.sh`](https://github.com/cmusatyalab/openface/blob/master/models/get-models.sh).
- **License**: OpenFace's pretrained models are released for
  **non-commercial research and educational use only** — see the
  [OpenFace project page](https://cmusatyalab.github.io/openface/) for the
  full terms. That fits a student/academic attendance project; if this
  system is ever adapted for commercial deployment, this model would need
  to be swapped for one with a commercial-compatible license.
- **Why this model**: it's small (~31MB), CPU-friendly, and needs no new
  Python dependency beyond `opencv-contrib-python` (already required) —
  no PyTorch/TensorFlow/dlib installation needed. See the "Face
  Recognition" section of the main README for how it's used in this app.

## Detection: still Haar cascade, now with alignment

Face *detection* (finding a face's bounding box, before embedding) still
uses `haarcascade_frontalface_default.xml`, not a modern learned detector
like YuNet. We looked into pairing this with YuNet specifically because
embeddings are known to work better on properly-aligned crops — but
YuNet's pretrained weights (like the OpenCV Zoo's SFace embedding model,
before we found this OpenFace alternative) are Git-LFS-gated in a way
this project's build environment can't reach.

As a practical alternative, `app.py`'s `align_face_crop()` uses the
bundled Haar eye cascade (`haarcascade_eye.xml`, ships with
`opencv-contrib-python`, no download needed) to detect both eyes within
a face box, then rotates the crop so the eye line is level before it's
handed to the embedder — falling back to the plain unaligned crop if
eyes aren't reliably found. This captures the main accuracy benefit
alignment provides, without needing a new model. A learned detector with
proper 5-point landmarks would still be more reliable than Haar eye
detection (which is sensitive to glasses, angle, and lighting) — worth
revisiting if YuNet's weights ever become reachable from wherever this
project is actually deployed.
