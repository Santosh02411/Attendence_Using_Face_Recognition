"""Tests for the embedding-based face recognition core: computing
embeddings, comparing them, and the gallery storage layer."""
import numpy as np
import pytest

import app as app_module


def test_compute_embedding_shape_and_normalization():
    """The embedder must always return a 128-d, L2-normalized vector."""
    face = np.random.randint(0, 255, (150, 150, 3), dtype=np.uint8)
    embedding = app_module.compute_embedding(face)
    assert embedding.shape == (128,)
    assert embedding.dtype == np.float32
    assert np.isclose(np.linalg.norm(embedding), 1.0, atol=1e-4)


def test_compute_embedding_is_deterministic():
    """The same input image must always produce the same embedding."""
    face = np.random.randint(0, 255, (120, 120, 3), dtype=np.uint8)
    e1 = app_module.compute_embedding(face)
    e2 = app_module.compute_embedding(face)
    assert np.allclose(e1, e2)


def test_cosine_similarity_self_match_is_one():
    a = np.random.randn(128).astype(np.float32)
    assert app_module.cosine_similarity(a, a) == pytest.approx(1.0, abs=1e-5)


def test_cosine_similarity_opposite_vectors_is_minus_one():
    a = np.zeros(128, dtype=np.float32)
    a[0] = 1.0
    b = -a
    assert app_module.cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-5)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    a = np.zeros(128, dtype=np.float32)
    a[0] = 1.0
    b = np.zeros(128, dtype=np.float32)
    b[1] = 1.0
    assert app_module.cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-5)


def test_cosine_similarity_handles_zero_vector_without_crashing():
    a = np.zeros(128, dtype=np.float32)
    b = np.random.randn(128).astype(np.float32)
    assert app_module.cosine_similarity(a, b) == 0.0


class TestEmbeddingGallery:
    """Tests for storing, retrieving, and matching embeddings — the
    incremental-gallery design that replaced LBPH's full-retrain model."""

    def _unit_vector(self, index):
        v = np.zeros(128, dtype=np.float32)
        v[index] = 1.0
        return v

    def test_store_and_retrieve_single_student(self, isolated_paths):
        emb = self._unit_vector(0)
        app_module.store_embedding(1, emb)
        stored = app_module.get_student_embeddings(1)
        assert len(stored) == 1
        assert np.allclose(stored[0], emb)

    def test_multiple_embeddings_per_student(self, isolated_paths):
        app_module.store_embedding(1, self._unit_vector(0))
        app_module.store_embedding(1, self._unit_vector(1))
        assert len(app_module.get_student_embeddings(1)) == 2

    def test_get_all_embeddings_spans_every_student(self, isolated_paths):
        app_module.store_embedding(1, self._unit_vector(0))
        app_module.store_embedding(2, self._unit_vector(1))
        all_embeddings = app_module.get_all_embeddings()
        student_ids = {sid for sid, _ in all_embeddings}
        assert student_ids == {1, 2}

    def test_delete_student_embeddings_removes_only_that_student(self, isolated_paths):
        app_module.store_embedding(1, self._unit_vector(0))
        app_module.store_embedding(2, self._unit_vector(1))
        app_module.delete_student_embeddings(1)
        remaining = app_module.get_all_embeddings()
        assert [sid for sid, _ in remaining] == [2]

    def test_delete_is_instant_no_retrain_step(self, isolated_paths, monkeypatch):
        """Regression guard: the old LBPH design required retraining the
        whole model on every add/delete. Embeddings should never call any
        such function — this fails loudly if that pattern ever creeps
        back in."""
        assert not hasattr(app_module, 'train_recognizer'), (
            'train_recognizer() should not exist anymore — deleting a '
            'student must not require reprocessing the whole dataset.'
        )

    def test_find_best_match_picks_closest_embedding(self, isolated_paths):
        target = self._unit_vector(0)
        decoy = self._unit_vector(1)
        app_module.store_embedding(101, target)
        app_module.store_embedding(202, decoy)

        query = target + np.random.randn(128).astype(np.float32) * 0.01
        query /= np.linalg.norm(query)

        best_id, similarity = app_module.find_best_match(query)
        assert best_id == 101
        assert similarity > 0.9

    def test_find_best_match_empty_gallery_returns_none(self, isolated_paths):
        best_id, similarity = app_module.find_best_match(np.random.randn(128).astype(np.float32))
        assert best_id is None
        assert similarity == 0.0
