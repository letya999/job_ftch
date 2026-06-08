from __future__ import annotations

import asyncio
from pathlib import Path

from job_ftch.application.contracts import ClassificationResult

# Optional dep guard at module top
try:
    from setfit import SetFitModel

    _SETFIT_AVAILABLE = True
except ImportError:
    _SETFIT_AVAILABLE = False

LABELS = ["job_posting", "candidate_seeking", "announcement", "spam", "unknown"]
DEFAULT_MODEL = "sentence-transformers/multilingual-e5-small"
TRAINING_DATA_PATH = Path("fixtures/classifier_training")


class SetFitClassifierProvider:
    model_id: str

    def __init__(self, model_path: str | Path | None = None) -> None:
        if not _SETFIT_AVAILABLE:
            raise ImportError("SetFit is not installed. Run: pip install 'job_ftch[classifiers]'")
        path = str(model_path or DEFAULT_MODEL)
        self._model = SetFitModel.from_pretrained(path)
        self.model_id = f"setfit:{path}"

    async def classify(self, text: str) -> ClassificationResult:
        loop = asyncio.get_running_loop()
        preds = await loop.run_in_executor(None, self._model.predict, [text])
        label = str(preds[0])
        proba = self._model.predict_proba([text])[0]
        confidence = float(max(proba))
        return ClassificationResult(label, confidence, self.model_id)

    async def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        if not texts:
            return []
        loop = asyncio.get_running_loop()
        preds = await loop.run_in_executor(None, self._model.predict, texts)
        probas = await loop.run_in_executor(None, self._model.predict_proba, texts)
        return [
            ClassificationResult(str(label), float(max(proba)), self.model_id)
            for label, proba in zip(preds, probas, strict=False)
        ]

    @classmethod
    def train(cls, output_path: str | Path) -> None:
        """Fine-tune the model using JSONL files in TRAINING_DATA_PATH."""
        if not _SETFIT_AVAILABLE:
            raise ImportError("SetFit is required for training.")

        from datasets import load_dataset
        from setfit import SetFitTrainer

        dataset = load_dataset(  # nosec B615
            "json", data_files=str(TRAINING_DATA_PATH / "*.jsonl"), split="train"
        )
        model = SetFitModel.from_pretrained(DEFAULT_MODEL)

        trainer = SetFitTrainer(
            model=model,
            train_dataset=dataset,
            column_mapping={"text": "text", "label": "label"},
        )
        trainer.train()
        model.save_pretrained(str(output_path))
