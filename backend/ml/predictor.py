"""
ML Predictor — loads the trained classifier and returns predictions.

Returns:
    {"label": "malicious", "confidence": 0.95}

Handles missing model files gracefully (raises informative error).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "models"
VECTORIZER_PATH = MODEL_DIR / "tfidf_vectorizer.joblib"
MODEL_PATH = MODEL_DIR / "logistic_classifier.joblib"
LABEL_ENCODER_PATH = MODEL_DIR / "label_encoder.joblib"


class PredictorLoadError(Exception):
    """Raised when model files cannot be loaded."""
    pass


class PromptPredictor:
    """
    Loads the trained TF-IDF + Logistic Regression model and predicts
    prompt safety labels.

    This predictor provides risk signals ONLY.
    It must NEVER independently authorize actions or override rule-based blocks.
    """

    def __init__(self):
        self._vectorizer = None
        self._model = None
        self._label_encoder = None
        self._load()

    def _load(self):
        """Load model artifacts from disk."""
        try:
            import joblib
        except ImportError:
            raise PredictorLoadError("joblib is not installed. Run: pip install joblib")

        for path, name in [
            (VECTORIZER_PATH, "TF-IDF vectorizer"),
            (MODEL_PATH, "Logistic Regression classifier"),
            (LABEL_ENCODER_PATH, "Label encoder"),
        ]:
            if not path.exists():
                raise PredictorLoadError(
                    f"{name} not found at {path}. "
                    "Run: python ml/train_classifier.py first."
                )

        self._vectorizer = joblib.load(VECTORIZER_PATH)
        self._model = joblib.load(MODEL_PATH)
        self._label_encoder = joblib.load(LABEL_ENCODER_PATH)
        logger.info("ML prompt classifier loaded from %s", MODEL_DIR)

    def predict(self, text: str) -> dict[str, Any]:
        """
        Predict the safety label and confidence for a text prompt.

        Returns:
            {"label": "legitimate"|"malicious"|"uncertain", "confidence": float}
        """
        if not text or not text.strip():
            return {"label": "uncertain", "confidence": 0.0}

        X = self._vectorizer.transform([text.strip()])
        probas = self._model.predict_proba(X)[0]
        pred_idx = probas.argmax()
        label = self._label_encoder.inverse_transform([pred_idx])[0]
        confidence = float(probas[pred_idx])

        return {
            "label": label,
            "confidence": round(confidence, 4),
        }

    def predict_detail(self, text: str) -> dict[str, Any]:
        """Return per-class probabilities for detailed analysis."""
        if not text or not text.strip():
            return {"label": "uncertain", "confidence": 0.0, "probabilities": {}}

        X = self._vectorizer.transform([text.strip()])
        probas = self._model.predict_proba(X)[0]
        pred_idx = probas.argmax()
        label = self._label_encoder.inverse_transform([pred_idx])[0]

        prob_dict = {
            self._label_encoder.inverse_transform([i])[0]: round(float(p), 4)
            for i, p in enumerate(probas)
        }

        return {
            "label": label,
            "confidence": round(float(probas[pred_idx]), 4),
            "probabilities": prob_dict,
        }

