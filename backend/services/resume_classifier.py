from __future__ import annotations

import logging
import os
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)


DEFAULT_MODEL_PATH = Path(os.environ.get("RESUME_CLASSIFIER_MODEL_PATH", "backend/model/resume_model.pkl"))
DEFAULT_ENCODER_PATH = Path(os.environ.get("RESUME_CLASSIFIER_ENCODER_PATH", "backend/model/label_encoder.pkl"))


@dataclass(frozen=True)
class ResumeClassifier:
    model: Any
    label_encoder: Any
    model_path: Path
    encoder_path: Path


_CLASSIFIER: Optional[ResumeClassifier] = None
_LOAD_ERROR: Optional[str] = None


def clean_resume_text(text: str) -> str:
    """
    Mirrors cleaning used in the standalone ML project.
    Kept intentionally simple to avoid NLTK runtime downloads in the API server.
    """
    value = str(text or "")
    value = re.sub(r"http\S+\s*", " ", value)
    value = re.sub(r"RT|cc", " ", value)
    value = re.sub(r"#\S+", " ", value)
    value = re.sub(r"@\S+", " ", value)
    value = re.sub(r"[^A-Za-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _resolve_paths(
    model_path: Path | str | None = None,
    encoder_path: Path | str | None = None,
) -> Tuple[Path, Path]:
    mp = Path(model_path) if model_path else DEFAULT_MODEL_PATH
    ep = Path(encoder_path) if encoder_path else DEFAULT_ENCODER_PATH
    # Allow relative paths from repo root when running uvicorn from root
    mp = mp if mp.is_absolute() else (Path.cwd() / mp).resolve()
    ep = ep if ep.is_absolute() else (Path.cwd() / ep).resolve()
    return mp, ep


def load_resume_classifier(
    model_path: Path | str | None = None,
    encoder_path: Path | str | None = None,
    *,
    force_reload: bool = False,
) -> Optional[ResumeClassifier]:
    """
    Loads the classifier once and caches it.
    Returns None if artifacts are missing/corrupt (pipeline continues without this signal).
    """
    global _CLASSIFIER, _LOAD_ERROR

    if _CLASSIFIER is not None and not force_reload:
        return _CLASSIFIER

    mp, ep = _resolve_paths(model_path, encoder_path)
    try:
        if not mp.exists() or not ep.exists():
            _LOAD_ERROR = f"Resume classifier artifacts not found (model={mp}, encoder={ep})"
            logger.warning(_LOAD_ERROR)
            _CLASSIFIER = None
            return None

        with mp.open("rb") as f:
            model = pickle.load(f)
        with ep.open("rb") as f:
            label_encoder = pickle.load(f)

        _CLASSIFIER = ResumeClassifier(model=model, label_encoder=label_encoder, model_path=mp, encoder_path=ep)
        _LOAD_ERROR = None
        logger.info("Loaded resume classifier model=%s encoder=%s", str(mp), str(ep))
        return _CLASSIFIER
    except Exception as exc:
        _LOAD_ERROR = f"Resume classifier load failed: {exc}"
        logger.exception("Resume classifier load failed (model=%s encoder=%s)", str(mp), str(ep))
        _CLASSIFIER = None
        return None


def _confidence_from_model(model: Any, cleaned_text: str) -> Optional[float]:
    """
    Returns confidence in [0,1] when possible.
    Works for sklearn estimators/pipelines that expose predict_proba or decision_function.
    """
    try:
        if hasattr(model, "predict_proba"):
            probs = model.predict_proba([cleaned_text])[0]
            return float(max(probs))
        if hasattr(model, "decision_function"):
            decision = model.decision_function([cleaned_text])[0]
            # Softmax over decision scores (multi-class) or sigmoid-ish fallback (binary)
            if hasattr(decision, "__iter__"):
                scores = list(decision)
                if not scores:
                    return None
                m = max(scores)
                exp = [pow(2.718281828, s - m) for s in scores]
                total = sum(exp) or 1.0
                return float(max(v / total for v in exp))
            return None
        return None
    except Exception:
        return None


def predict_resume_category(text: str) -> Optional[dict]:
    """
    Returns:
      {"predicted_category": str, "confidence": float}
    or None if classifier unavailable or prediction fails.
    """
    clf = load_resume_classifier()
    if clf is None:
        return None

    cleaned = clean_resume_text(text)
    if not cleaned:
        return None

    try:
        pred_id = clf.model.predict([cleaned])[0]
        predicted_category = clf.label_encoder.inverse_transform([pred_id])[0]
        confidence = _confidence_from_model(clf.model, cleaned)
        if confidence is None:
            confidence = 0.0
        confidence = float(max(0.0, min(1.0, confidence)))
        logger.info("Resume category predicted=%s confidence=%.3f", predicted_category, confidence)
        return {"predicted_category": str(predicted_category), "confidence": confidence}
    except Exception as exc:
        logger.warning("Resume category prediction failed: %s", exc)
        return None


def get_resume_classifier_status() -> dict:
    """
    Small diagnostic payload for logs/debug UIs.
    """
    clf = _CLASSIFIER
    return {
        "loaded": bool(clf),
        "model_path": str(clf.model_path) if clf else str(_resolve_paths()[0]),
        "encoder_path": str(clf.encoder_path) if clf else str(_resolve_paths()[1]),
        "error": _LOAD_ERROR,
    }


def compute_category_alignment(
    predicted_category: str,
    jd_target_role: str,
    confidence: float,
) -> dict:
    """
    Returns:
      {
        "predicted_category": str,
        "confidence": float,
        "jd_target_role": str,
        "alignment_score": int,   # 0..10
        "alignment_label": str,
      }

    Heuristics:
    - If confidence is low, keep it as a weak signal.
    - Exact/close matches with high confidence yield strong scores.
    - Mismatch with high confidence penalizes alignment.
    """

    pred = (predicted_category or "").strip()
    jd = (jd_target_role or "").strip()
    conf = float(confidence or 0.0)
    conf = max(0.0, min(1.0, conf))

    def _norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9\\s]", " ", s)
        s = re.sub(r"\\s+", " ", s).strip()
        return s

    pred_n = _norm(pred)
    jd_n = _norm(jd)

    if not pred_n or not jd_n:
        return {
            "predicted_category": pred,
            "confidence": conf,
            "jd_target_role": jd,
            "alignment_score": 0,
            "alignment_label": "Unknown",
        }

    # "Close match" via substring overlap or shared core tokens
    pred_tokens = set(pred_n.split())
    jd_tokens = set(jd_n.split())
    token_overlap = len(pred_tokens & jd_tokens)
    close_match = (pred_n in jd_n) or (jd_n in pred_n) or token_overlap >= 2
    exact_match = pred_n == jd_n

    # Confidence buckets
    high = conf >= 0.75
    mid = 0.45 <= conf < 0.75
    low = conf < 0.45

    score: int
    label: str

    if exact_match and high:
        score, label = 9, "Strong match"
    elif close_match and high:
        score, label = 8, "Strong match"
    elif (exact_match or close_match) and mid:
        score, label = 6, "Likely match"
    elif (exact_match or close_match) and low:
        score, label = 4, "Weak signal"
    else:
        # Mismatch
        if high:
            score, label = 2, "Mismatch"
        elif mid:
            score, label = 3, "Weak signal"
        else:
            score, label = 4, "Weak signal"

    return {
        "predicted_category": pred,
        "confidence": conf,
        "jd_target_role": jd,
        "alignment_score": int(max(0, min(10, score))),
        "alignment_label": label,
    }

