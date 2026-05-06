import pickle
from pathlib import Path

import pytest

from services.resume_classifier import (
    clean_resume_text,
    compute_category_alignment,
    load_resume_classifier,
    predict_resume_category,
)


class _DummyLabelEncoder:
    def __init__(self, labels):
        self._labels = list(labels)

    def inverse_transform(self, ids):
        return [self._labels[int(ids[0])]]


class _DummyModel:
    def __init__(self, pred_id=0, proba=None, raise_on_predict=False):
        self._pred_id = pred_id
        self._proba = proba
        self._raise = raise_on_predict

    def predict(self, X):
        if self._raise:
            raise RuntimeError("predict failed")
        return [self._pred_id]

    def predict_proba(self, X):
        if self._proba is None:
            raise AttributeError("no proba")
        return [self._proba]


def test_clean_resume_text_matches_expected_rules():
    raw = "RT cc Hello!!! Visit http://example.com #tag @user"
    cleaned = clean_resume_text(raw)
    assert "http" not in cleaned
    assert "#" not in cleaned
    assert "@" not in cleaned
    assert cleaned == cleaned.lower()


def test_predict_resume_category_empty_text_returns_none(tmp_path, monkeypatch):
    # Ensure no cached classifier leaks in
    load_resume_classifier(force_reload=True, model_path=tmp_path / "missing.pkl", encoder_path=tmp_path / "missing2.pkl")
    assert predict_resume_category("") is None


def test_predict_resume_category_happy_path_with_pickles(tmp_path):
    model_path = tmp_path / "resume_model.pkl"
    enc_path = tmp_path / "label_encoder.pkl"

    model = _DummyModel(pred_id=1, proba=[0.05, 0.91, 0.04])
    encoder = _DummyLabelEncoder(["Frontend Developer", "Backend Developer", "Data Scientist"])

    model_path.write_bytes(pickle.dumps(model))
    enc_path.write_bytes(pickle.dumps(encoder))

    load_resume_classifier(force_reload=True, model_path=model_path, encoder_path=enc_path)
    out = predict_resume_category("Python FastAPI developer")
    assert out is not None
    assert out["predicted_category"] == "Backend Developer"
    assert out["confidence"] == pytest.approx(0.91, rel=1e-6)


def test_predict_resume_category_handles_corrupt_model_files(tmp_path):
    model_path = tmp_path / "resume_model.pkl"
    enc_path = tmp_path / "label_encoder.pkl"
    model_path.write_text("not a pickle", encoding="utf-8")
    enc_path.write_text("not a pickle", encoding="utf-8")

    load_resume_classifier(force_reload=True, model_path=model_path, encoder_path=enc_path)
    assert predict_resume_category("Some resume text") is None


def test_category_alignment_strong_match():
    out = compute_category_alignment("Backend Developer", "Backend Developer", 0.91)
    assert out["alignment_score"] >= 8
    assert out["alignment_label"] in {"Strong match", "Likely match"}


def test_category_alignment_mismatch_high_confidence():
    out = compute_category_alignment("Data Scientist", "Backend Developer", 0.91)
    assert out["alignment_score"] <= 3
    assert out["alignment_label"] in {"Mismatch", "Weak signal"}


def test_category_alignment_low_confidence_is_weak_signal():
    out = compute_category_alignment("Backend Developer", "Backend Developer", 0.2)
    assert out["alignment_label"] == "Weak signal"

