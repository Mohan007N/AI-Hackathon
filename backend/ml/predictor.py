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

    Supported classes: 'legitimate', 'malicious', 'uncertain'.
    This predictor provides risk signals ONLY.
    It must NEVER independently authorize actions or override rule-based blocks.
    """

    def __init__(self):
        self._vectorizer = None
        self._model = None
        self._label_encoder = None
        self._is_ready = False
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

        try:
            self._vectorizer = joblib.load(VECTORIZER_PATH)
            self._model = joblib.load(MODEL_PATH)
            self._label_encoder = joblib.load(LABEL_ENCODER_PATH)
            self._is_ready = True
            logger.info("ML prompt classifier loaded successfully from %s", MODEL_DIR)
        except Exception as exc:
            self._is_ready = False
            raise PredictorLoadError(f"Error deserializing model artifacts: {exc}") from exc

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    def predict(self, text: str) -> dict[str, Any]:
        """
        Predict the safety label and confidence for input text.

        Returns:
            {
                "label": "legitimate" | "malicious" | "uncertain",
                "confidence": float,
                "model": "logistic_regression",
                "probabilities": { ... }
            }
        """
        if not text or not text.strip():
            return {
                "label": "uncertain",
                "confidence": 0.0,
                "model": "logistic_regression",
                "probabilities": {},
            }

        if not self._is_ready:
            raise PredictorLoadError("Classifier is not loaded or ready.")

        try:
            cleaned = text.strip()
            X = self._vectorizer.transform([cleaned])
            probas = self._model.predict_proba(X)[0]
            pred_idx = probas.argmax()
            label = str(self._label_encoder.inverse_transform([pred_idx])[0])
            confidence = float(probas[pred_idx])

            prob_dict = {
                str(self._label_encoder.inverse_transform([i])[0]): round(float(p), 4)
                for i, p in enumerate(probas)
            }

            return {
                "label": label,
                "confidence": round(confidence, 4),
                "model": "logistic_regression",
                "probabilities": prob_dict,
            }
        except Exception as exc:
            logger.error("Prediction failed for input '%s': %s", text[:100], exc)
            return {
                "label": "uncertain",
                "confidence": 0.0,
                "model": "logistic_regression",
                "error": str(exc),
                "probabilities": {},
            }

    def predict_detail(self, text: str) -> dict[str, Any]:
        """Alias for detailed prediction output."""
        return self.predict(text)

