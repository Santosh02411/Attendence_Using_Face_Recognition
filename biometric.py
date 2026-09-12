"""Iris-authentication SCAFFOLDING — an abstract interface and storage
layer for a future biometric integration, not a working biometric
security feature today.

Read this before enabling anything here:

  - There is no certified iris-capture hardware or vendor SDK wired up
    in this project. A standard webcam (the kind this project already
    uses for face recognition) CANNOT do iris recognition — that needs
    a near-infrared camera and matching optics. Nothing here tries to
    fake that with a regular webcam, because doing so would mislead
    whoever is looking at the screen into thinking a real biometric
    check happened when it didn't.
  - What's here is the shape a real integration would plug into: an
    abstract IrisProvider interface (capture_template/compare), a
    storage table for the resulting templates, and an admin-only
    diagnostic page to exercise that plumbing with synthetic data. The
    one working provider (MockIrisProvider) is explicitly NOT a
    biometric matcher — it derives a deterministic vector from whatever
    bytes it's given, purely so the enroll/compare code paths, the
    database schema, and the admin UI have something real to run
    against in tests and demos.
  - CRITICAL: nothing in this module is wired into any actual
    attendance-marking or login security decision anywhere else in the
    app, and it must stay that way until a real provider (backed by
    real hardware and a real matching algorithm with a known false-
    accept rate) is implemented and independently reviewed. Treating
    MockIrisProvider's output as a security signal would be actively
    dangerous — it always "matches" its own deterministic derivation of
    the same input, which tells you nothing about whether two samples
    came from the same eye.

See README's "Biometric Integration (Scaffolding)" section for the
intended path from here to a real integration.
"""
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

TEMPLATE_DIM = 64


@dataclass
class IrisTemplate:
    student_id: int
    vector: np.ndarray
    provider_name: str
    enrolled_at: str


class IrisProvider(ABC):
    """What a real integration would implement. `raw_sample` is
    whatever a real capture device/SDK hands back (e.g. a proprietary
    iris-code buffer) — deliberately untyped here since that shape is
    entirely vendor-specific and unknown until a real device is chosen.
    """

    name: str

    @abstractmethod
    def capture_template(self, raw_sample: bytes) -> np.ndarray:
        """Turns a raw sample into a fixed-length template vector
        suitable for storage and later comparison."""
        raise NotImplementedError

    @abstractmethod
    def compare(self, template_a: np.ndarray, template_b: np.ndarray) -> float:
        """Returns a similarity score in [0, 1] between two templates
        from the same provider. Higher means more similar."""
        raise NotImplementedError


class MockIrisProvider(IrisProvider):
    """NOT a biometric matcher — see this module's docstring. Derives a
    deterministic, fixed-length vector from the SHA-256 hash of
    whatever bytes it's given, purely so enroll/compare has a concrete,
    testable implementation to exercise the surrounding plumbing.
    Comparing a template against itself always scores 1.0; comparing
    against a different input scores measurably lower (but NOT
    necessarily close to 0 — these are non-negative byte-derived
    vectors, not real iris codes, so their cosine similarity carries no
    biometric meaning at all) — this is expected and is exactly why it
    must never be treated as a real match/no-match decision.
    """

    name = 'mock'

    def capture_template(self, raw_sample: bytes) -> np.ndarray:
        digest = hashlib.sha256(raw_sample).digest()
        # Repeat/trim the 32-byte digest out to TEMPLATE_DIM floats in
        # [0, 1] — simple, deterministic, and enough to exercise
        # storage/serialization, nothing more.
        repeated = (digest * ((TEMPLATE_DIM // len(digest)) + 1))[:TEMPLATE_DIM]
        return np.frombuffer(repeated, dtype=np.uint8).astype(np.float32) / 255.0

    def compare(self, template_a: np.ndarray, template_b: np.ndarray) -> float:
        if template_a.shape != template_b.shape:
            return 0.0
        # Cosine similarity, clipped to [0, 1] -- a plain vector-math
        # comparison, not a biometric matching algorithm.
        denom = (np.linalg.norm(template_a) * np.linalg.norm(template_b))
        if denom == 0:
            return 0.0
        return float(max(0.0, min(1.0, np.dot(template_a, template_b) / denom)))


_PROVIDERS = {'mock': MockIrisProvider}


def get_iris_provider(provider_name, enabled):
    """Factory mirroring this project's other opt-in integrations
    (see error_reporting.init_sentry(), configure_oauth()): returns
    None — a safe no-op — unless a provider is both enabled AND a
    known, valid name. 'mock' is the only value that currently means
    anything; anything else (including a real vendor name someone
    might optimistically set before a real driver exists) resolves to
    None rather than pretending to work.
    """
    if not enabled:
        return None
    provider_cls = _PROVIDERS.get(provider_name)
    if not provider_cls:
        return None
    return provider_cls()


def serialize_template(vector):
    return vector.astype(np.float32).tobytes()


def deserialize_template(blob):
    return np.frombuffer(blob, dtype=np.float32)
