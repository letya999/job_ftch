"""Tenant-local TF-IDF/LogReg prefilter dataset and artifact workflow.

Training is explicit and gated. Example/profile writes only mark the model dirty.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MIN_ROWS = 2000
MIN_POSITIVES = 150
MIN_POSITIVE_FRACTION = 0.02
MAX_POSITIVE_FRACTION = 0.50
MIN_HOLDOUT_RETENTION = 0.90
DEFAULT_THRESHOLD = 0.30
DEFAULT_HOLDOUT_FRACTION = 0.20
DEFAULT_FIXTURE = Path("fixtures/prefilter/tfidf_logreg_v1.json")
DEFAULT_EVAL_DATASET = Path("fixtures/dataset/eval_dataset.jsonl")
_TOKEN_RE = re.compile(r"(?u)\b\w\w+\b")
_PREPARE_SOURCES = frozenset({"examples", "feedback", "eval_dataset", "mixed"})


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def sklearn_status() -> dict[str, Any]:
    try:
        import sklearn  # noqa: F401
    except ImportError:
        return {
            "present": False,
            "install_hint": "pip install scikit-learn",
        }
    return {"present": True, "install_hint": None}


def prefilter_dir(settings: Any, *, create: bool = True) -> Path:
    store_path = getattr(settings, "store_path", None)
    if store_path is None:
        msg = "settings.store_path is required for prefilter artifacts"
        raise ValueError(msg)
    root = Path(store_path).parent / "prefilter"
    if create:
        root.mkdir(parents=True, exist_ok=True)
        (root / "datasets").mkdir(exist_ok=True)
        (root / "artifacts").mkdir(exist_ok=True)
    return root


def promoted_model_path(settings: Any) -> Path | None:
    """Return tenant current.json when a promoted artifact exists."""
    if settings is None or getattr(settings, "store_path", None) is None:
        return None
    current = prefilter_dir(settings, create=False) / "current.json"
    return current if current.is_file() else None


def resolve_prefilter_model_path(settings: Any, configured: str | None = None) -> str:
    """Prefer a promoted tenant artifact over the configured/default fixture."""
    promoted = promoted_model_path(settings)
    if promoted is not None:
        return str(promoted)
    if configured:
        return configured
    return str(DEFAULT_FIXTURE)


def apply_promoted_prefilter_to_graph(settings: Any, graph: Any) -> str | None:
    """Point tfidf_logreg_prefilter graph params at current.json when present."""
    promoted = promoted_model_path(settings)
    if promoted is None:
        return None
    spec = getattr(graph, "spec", None)
    nodes = getattr(spec, "nodes", ())
    for node in nodes:
        if getattr(node, "node", None) != "tfidf_logreg_prefilter":
            continue
        params = getattr(node, "params", None)
        if isinstance(params, dict):
            params["model_path"] = str(promoted)
    return str(promoted)


def _state_path(root: Path) -> Path:
    return root / "state.json"


def load_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.is_file():
        return {
            "dirty": True,
            "current_artifact_id": None,
            "previous_artifact_id": None,
            "last_dataset_id": None,
            "last_eval": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "dirty": True,
            "current_artifact_id": None,
            "previous_artifact_id": None,
            "last_dataset_id": None,
            "last_eval": None,
        }
    if not isinstance(payload, dict):
        return {
            "dirty": True,
            "current_artifact_id": None,
            "previous_artifact_id": None,
            "last_dataset_id": None,
            "last_eval": None,
        }
    payload.setdefault("dirty", True)
    payload.setdefault("current_artifact_id", None)
    payload.setdefault("previous_artifact_id", None)
    payload.setdefault("last_dataset_id", None)
    payload.setdefault("last_eval", None)
    return payload


def save_state(root: Path, state: dict[str, Any]) -> None:
    _state_path(root).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_prefilter_dirty(settings: Any) -> None:
    if settings is None or getattr(settings, "store_path", None) is None:
        return
    root = prefilter_dir(settings)
    state = load_state(root)
    state["dirty"] = True
    save_state(root, state)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _relevant_from_record(record: dict[str, Any]) -> int | None:
    relevant = record.get("relevant")
    if type(relevant) is int and relevant in (0, 1):
        return relevant
    label = record.get("label")
    if label in {1, "1", "positive"}:
        return 1
    if label in {0, "0", "negative"}:
        return 0
    return None


def load_dataset_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            relevant = _relevant_from_record(record)
            text = record.get("text")
            if relevant is None or not isinstance(text, str) or not text.strip():
                continue
            stable_id = record.get("stable_id") or f"row-{len(rows)}"
            rows.append(
                {
                    "stable_id": str(stable_id),
                    "text": text,
                    "relevant": relevant,
                }
            )
    return rows


def dataset_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n_rows = len(rows)
    n_positive = sum(1 for row in rows if row["relevant"] == 1)
    fraction = (n_positive / n_rows) if n_rows else 0.0
    errors: list[str] = []
    if n_rows < MIN_ROWS:
        errors.append(f"Dataset has {n_rows} rows, minimum is {MIN_ROWS}.")
    if n_positive < MIN_POSITIVES:
        errors.append(f"Dataset has {n_positive} positives, minimum is {MIN_POSITIVES}.")
    if n_rows > 0 and not (MIN_POSITIVE_FRACTION <= fraction <= MAX_POSITIVE_FRACTION):
        errors.append(
            f"Positive fraction {fraction:.3f} is outside allowed range "
            f"[{MIN_POSITIVE_FRACTION}, {MAX_POSITIVE_FRACTION}]."
        )
    return {
        "n_rows": n_rows,
        "n_positive": n_positive,
        "n_negative": n_rows - n_positive,
        "positive_fraction": round(fraction, 4),
        "ok": not errors,
        "errors": errors,
        "production_ready": not errors,
    }


def validate_prefilter_dataset(
    dataset_id_or_path: str, settings: Any | None = None
) -> dict[str, Any]:
    path = resolve_dataset_path(dataset_id_or_path, settings)
    if path is None:
        return {
            "error": "not_found",
            "message": f"dataset not found: {dataset_id_or_path}",
            "ok": False,
        }
    stats = dataset_stats(load_dataset_rows(path))
    stats["path"] = str(path)
    stats["dataset_sha256"] = file_sha256(path)
    return stats


def resolve_dataset_path(dataset_id_or_path: str, settings: Any | None) -> Path | None:
    raw = Path(dataset_id_or_path)
    if raw.is_file():
        return raw
    if settings is None:
        return None
    root = prefilter_dir(settings)
    candidate = root / "datasets" / dataset_id_or_path
    if candidate.is_file():
        return candidate
    if not dataset_id_or_path.endswith(".jsonl"):
        with_suffix = root / "datasets" / f"{dataset_id_or_path}.jsonl"
        if with_suffix.is_file():
            return with_suffix
    return None


def resolve_artifact_path(artifact_id: str, settings: Any) -> Path | None:
    raw = Path(artifact_id)
    if raw.is_file():
        return raw
    root = prefilter_dir(settings)
    for candidate in (
        root / "artifacts" / artifact_id,
        root / "artifacts" / f"{artifact_id}.json",
        root / artifact_id,
    ):
        if candidate.is_file():
            return candidate
    return None


def get_prefilter_status(
    settings: Any,
    *,
    tenant_id: str,
    profile_id: str | None = None,
) -> dict[str, Any]:
    root = prefilter_dir(settings, create=False)
    state = load_state(root)
    current_id = state.get("current_artifact_id")
    current_path = resolve_artifact_path(str(current_id), settings) if current_id else None
    active_path = resolve_prefilter_model_path(settings)
    return {
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "dirty": bool(state.get("dirty", True)),
        "current_artifact_id": current_id,
        "previous_artifact_id": state.get("previous_artifact_id"),
        "last_dataset_id": state.get("last_dataset_id"),
        "last_eval": state.get("last_eval"),
        "current_artifact_present": current_path is not None,
        "using_promoted": promoted_model_path(settings) is not None,
        "active_model_path": active_path,
        "production_fixture_present": DEFAULT_FIXTURE.is_file(),
        "production_fixture": str(DEFAULT_FIXTURE),
        "sklearn": sklearn_status(),
        "size_requirements": {
            "min_rows": MIN_ROWS,
            "min_positives": MIN_POSITIVES,
            "positive_fraction_min": MIN_POSITIVE_FRACTION,
            "positive_fraction_max": MAX_POSITIVE_FRACTION,
            "min_holdout_retention": MIN_HOLDOUT_RETENTION,
        },
        "promotion": {
            "automatic_after_example_change": False,
            "require_eval_gate": True,
        },
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _example_rows(examples: dict[str, list[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for text in examples.get("positive_job", []) + examples.get("positive_vacancy", []):
        rows.append(
            {
                "stable_id": f"example-vacancy-pos-{len(rows)}",
                "text": text,
                "relevant": 1,
            }
        )
    for text in examples.get("negative_job", []) + examples.get("negative_vacancy", []):
        rows.append(
            {
                "stable_id": f"example-vacancy-neg-{len(rows)}",
                "text": text,
                "relevant": 0,
            }
        )
    return rows


async def prepare_prefilter_dataset(
    runner: Any,
    *,
    tenant_id: str,
    profile_id: str | None = None,
    source: str = "examples",
    output: str | None = None,
    user_id: str = "mcp",
) -> dict[str, Any]:
    if source not in _PREPARE_SOURCES:
        return {
            "error": "invalid_arguments",
            "message": "source must be one of examples|feedback|eval_dataset|mixed",
            "source": source,
        }
    settings = runner.get_runtime(tenant_id).settings
    root = prefilter_dir(settings)
    dataset_id = _new_id(f"ds-{source}")
    out_path = Path(output) if output else root / "datasets" / f"{dataset_id}.jsonl"

    rows: list[dict[str, Any]] = []
    used: list[str] = []
    if source in {"examples", "mixed"}:
        from job_ftch.application.profile_inputs import list_examples

        managed = None
        if profile_id:
            managed = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
        if managed is None:
            profiles = await runner.list_candidate_profiles(tenant_id, user_id)
            active = next((item for item in profiles if item.get("active")), None)
            chosen = None
            if active is not None:
                chosen = str(active["profile_id"])
            elif profiles:
                chosen = str(profiles[0]["profile_id"])
            if chosen:
                managed = await runner.get_candidate_profile(tenant_id, user_id, chosen)
        example_rows = _example_rows(list_examples(managed) if managed is not None else {})
        rows.extend(example_rows)
        used.append(f"examples:{len(example_rows)}")
    if source in {"feedback", "mixed"}:
        from job_ftch.application.vacancy_feedback import load_feedback

        store = runner.get_runtime(tenant_id).store
        records = await load_feedback(store, tenant_id)
        added = 0
        for record in records:
            text = (record.excerpt or record.title or "").strip()
            if not text:
                continue
            rows.append(
                {
                    "stable_id": f"feedback-{record.job_id}-{record.user_id}",
                    "text": text,
                    "relevant": 0,
                }
            )
            added += 1
        used.append(f"feedback:{added}")
    if source in {"eval_dataset", "mixed"}:
        if DEFAULT_EVAL_DATASET.is_file():
            eval_rows = load_dataset_rows(DEFAULT_EVAL_DATASET)
            rows.extend(eval_rows)
            used.append(f"eval_dataset:{len(eval_rows)}")
        elif source == "eval_dataset":
            return {
                "error": "not_found",
                "message": f"default eval dataset missing: {DEFAULT_EVAL_DATASET}",
                "ok": False,
            }

    _write_jsonl(out_path, rows)
    stats = dataset_stats(rows)
    state = load_state(root)
    state["last_dataset_id"] = out_path.stem
    save_state(root, state)
    return {
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "source": source,
        "sources_used": used,
        "dataset_id": out_path.stem,
        "path": str(out_path),
        **stats,
    }


def train_tfidf_logreg(
    *,
    texts: list[str],
    labels: list[int],
    dataset_path: Path,
    threshold: float = DEFAULT_THRESHOLD,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
) -> dict[str, Any]:
    import numpy as np
    import sklearn
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedShuffleSplit

    sss = StratifiedShuffleSplit(n_splits=1, test_size=holdout_fraction, random_state=42)
    labels_arr = np.array(labels)
    train_idx, holdout_idx = next(sss.split(texts, labels_arr))
    train_texts = [texts[i] for i in train_idx]
    train_labels = [labels[i] for i in train_idx]
    holdout_texts = [texts[i] for i in holdout_idx]
    holdout_labels_arr = labels_arr[holdout_idx]

    vectorizer = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        min_df=2,
        max_features=200000,
        sublinear_tf=True,
    )
    x_train = vectorizer.fit_transform(train_texts)
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0)
    clf.fit(x_train, train_labels)

    holdout_probs = clf.predict_proba(vectorizer.transform(holdout_texts))[:, 1]
    sweep = []
    n_holdout_pos = int(holdout_labels_arr.sum())
    for threshold_value in (0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        kept = int((holdout_probs >= threshold_value).sum())
        kept_positives = int(((holdout_probs >= threshold_value) & (holdout_labels_arr == 1)).sum())
        pos_retention = float(kept_positives / n_holdout_pos) if n_holdout_pos else 0.0
        sweep.append(
            {
                "threshold": float(threshold_value),
                "kept": kept,
                "positive_retention": round(pos_retention, 4),
            }
        )
    requested_kept_pos = int(((holdout_probs >= threshold) & (holdout_labels_arr == 1)).sum())
    target_retention = float(requested_kept_pos / n_holdout_pos) if n_holdout_pos else 0.0

    if target_retention < MIN_HOLDOUT_RETENTION:
        return {
            "ok": False,
            "error": "gate_failed",
            "message": (
                f"Positive retention at threshold {threshold} is "
                f"{target_retention:.4f} < {MIN_HOLDOUT_RETENTION} on held-out data."
            ),
            "metrics": {
                "threshold_sweep_holdout": sweep,
                "target_threshold": threshold,
                "holdout_positive_retention": round(target_retention, 4),
            },
        }

    vectorizer_full = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        min_df=2,
        max_features=200000,
        sublinear_tf=True,
    )
    clf_full = LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0)
    clf_full.fit(vectorizer_full.fit_transform(texts), labels)
    return {
        "ok": True,
        "artifact": {
            "schema_version": 1,
            "model_version": "tfidf-logreg-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "analyzer": "word",
            "ngram_range": [1, 2],
            "min_df": 2,
            "sublinear_tf": True,
            "vocabulary": {key: int(value) for key, value in vectorizer_full.vocabulary_.items()},
            "idf": vectorizer_full.idf_.tolist(),
            "coef": clf_full.coef_[0].tolist(),
            "intercept": float(clf_full.intercept_[0]),
            "training": {
                "dataset": str(dataset_path),
                "dataset_sha256": file_sha256(dataset_path),
                "n_rows": len(texts),
                "n_positive": sum(labels),
                "excluded_ids": 0,
                "sklearn_version": sklearn.__version__,
                "holdout_fraction": holdout_fraction,
                "holdout_size": len(holdout_texts),
                "holdout_positives": int(holdout_labels_arr.sum()),
            },
            "metrics": {
                "threshold_sweep_holdout": sweep,
                "target_threshold": threshold,
                "holdout_positive_retention": round(target_retention, 4),
            },
        },
    }


def train_prefilter(
    settings: Any,
    *,
    tenant_id: str,
    profile_id: str | None = None,
    dataset_id_or_path: str | None = None,
    dry_run: bool = True,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    if not dataset_id_or_path:
        return {
            "error": "invalid_arguments",
            "message": "dataset_id_or_path is required",
        }
    path = resolve_dataset_path(dataset_id_or_path, settings)
    if path is None:
        return {"error": "not_found", "message": f"dataset not found: {dataset_id_or_path}"}
    rows = load_dataset_rows(path)
    stats = dataset_stats(rows)
    skl = sklearn_status()
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "dataset_path": str(path),
        "dry_run": dry_run,
        "threshold": threshold,
        "dataset": stats,
        "sklearn": skl,
    }
    if dry_run:
        payload["would_write"] = bool(stats["ok"] and skl["present"])
        payload["ok"] = True
        return payload
    if not stats["ok"]:
        payload["error"] = "dataset_not_ready"
        payload["ok"] = False
        return payload
    if not skl["present"]:
        payload["error"] = "sklearn_missing"
        payload["ok"] = False
        return payload
    trained = train_tfidf_logreg(
        texts=[row["text"] for row in rows],
        labels=[int(row["relevant"]) for row in rows],
        dataset_path=path,
        threshold=threshold,
    )
    if not trained.get("ok"):
        payload.update(trained)
        return payload
    root = prefilter_dir(settings)
    artifact_id = _new_id("art")
    out_path = root / "artifacts" / f"{artifact_id}.json"
    out_path.write_text(json.dumps(trained["artifact"], ensure_ascii=False), encoding="utf-8")
    state = load_state(root)
    state["last_dataset_id"] = path.stem
    save_state(root, state)
    payload.update(
        {
            "ok": True,
            "artifact_id": artifact_id,
            "path": str(out_path),
            "metrics": trained["artifact"]["metrics"],
            "promoted": False,
        }
    )
    return payload


def _score_text(text: str, artifact: dict[str, Any]) -> float:
    tokens = _TOKEN_RE.findall(text.lower())
    ngram_range = artifact.get("ngram_range", [1, 2])
    min_n, max_n = int(ngram_range[0]), int(ngram_range[1])
    vocabulary: dict[str, int] = artifact["vocabulary"]
    idf: list[float] = artifact["idf"]
    coef: list[float] = artifact["coef"]
    intercept = float(artifact["intercept"])
    sublinear = bool(artifact.get("sublinear_tf", True))
    term_counts: dict[int, int] = {}
    n_tokens = len(tokens)
    for n in range(min_n, min(max_n + 1, n_tokens + 1)):
        for index in range(n_tokens - n + 1):
            term = " ".join(tokens[index : index + n])
            if term in vocabulary:
                vocab_index = vocabulary[term]
                term_counts[vocab_index] = term_counts.get(vocab_index, 0) + 1
    norm_sq = 0.0
    vec: list[tuple[int, float]] = []
    for vocab_index, count in term_counts.items():
        tf = 1.0 + math.log(count) if sublinear else float(count)
        value = tf * idf[vocab_index]
        vec.append((vocab_index, value))
        norm_sq += value * value
    norm = math.sqrt(norm_sq) if norm_sq > 0 else 1.0
    dot = intercept
    for vocab_index, value in vec:
        dot += (value / norm) * coef[vocab_index]
    if dot >= 0:
        return 1.0 / (1.0 + math.exp(-dot))
    return math.exp(dot) / (1.0 + math.exp(dot))


def evaluate_prefilter(
    settings: Any,
    *,
    tenant_id: str,
    artifact_id: str,
    dataset_id_or_path: str | None = None,
    threshold: float | None = None,
) -> dict[str, Any]:
    path = resolve_artifact_path(artifact_id, settings)
    if path is None:
        return {"error": "not_found", "message": f"artifact not found: {artifact_id}"}
    artifact = json.loads(path.read_text(encoding="utf-8"))
    stored = artifact.get("metrics", {})
    target = float(
        threshold if threshold is not None else stored.get("target_threshold", DEFAULT_THRESHOLD)
    )
    stored_retention = stored.get("holdout_positive_retention")
    gate_from_train = (
        isinstance(stored_retention, (int, float))
        and float(stored_retention) >= MIN_HOLDOUT_RETENTION
    )
    dataset_metrics: dict[str, Any] | None = None
    if dataset_id_or_path:
        dataset_path = resolve_dataset_path(dataset_id_or_path, settings)
        if dataset_path is None:
            return {"error": "not_found", "message": f"dataset not found: {dataset_id_or_path}"}
        rows = load_dataset_rows(dataset_path)
        positives = [row for row in rows if row["relevant"] == 1]
        kept_pos = 0
        kept = 0
        for row in rows:
            score = _score_text(row["text"], artifact)
            if score >= target:
                kept += 1
                if row["relevant"] == 1:
                    kept_pos += 1
        retention = (kept_pos / len(positives)) if positives else 0.0
        dataset_metrics = {
            "n_rows": len(rows),
            "n_positive": len(positives),
            "kept": kept,
            "kept_positives": kept_pos,
            "positive_retention": round(retention, 4),
            "threshold": target,
            "path": str(dataset_path),
        }
        gate_from_dataset = retention >= MIN_HOLDOUT_RETENTION
    else:
        gate_from_dataset = True

    gate_pass = bool(gate_from_train and gate_from_dataset)
    root = prefilter_dir(settings)
    state = load_state(root)
    eval_payload = {
        "artifact_id": path.stem,
        "gate_pass": gate_pass,
        "stored_metrics": stored,
        "dataset_metrics": dataset_metrics,
        "min_holdout_retention": MIN_HOLDOUT_RETENTION,
    }
    state["last_eval"] = eval_payload
    save_state(root, state)
    return {
        "tenant_id": tenant_id,
        "ok": True,
        **eval_payload,
        "path": str(path),
    }


def list_prefilter_artifacts(
    settings: Any,
    *,
    tenant_id: str,
    profile_id: str | None = None,
) -> dict[str, Any]:
    root = prefilter_dir(settings)
    state = load_state(root)
    artifacts: list[dict[str, Any]] = []
    for path in sorted((root / "artifacts").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        artifacts.append(
            {
                "artifact_id": path.stem,
                "path": str(path),
                "created_at": payload.get("created_at"),
                "n_rows": (payload.get("training") or {}).get("n_rows"),
                "n_positive": (payload.get("training") or {}).get("n_positive"),
                "holdout_positive_retention": (payload.get("metrics") or {}).get(
                    "holdout_positive_retention"
                ),
                "is_current": path.stem == state.get("current_artifact_id"),
            }
        )
    return {
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "current_artifact_id": state.get("current_artifact_id"),
        "artifacts": artifacts,
        "count": len(artifacts),
    }


def promote_prefilter(
    settings: Any,
    *,
    tenant_id: str,
    artifact_id: str,
    threshold: float | None = None,
    require_gate_pass: bool = True,
) -> dict[str, Any]:
    evaluation = evaluate_prefilter(
        settings,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        threshold=threshold,
    )
    if evaluation.get("error"):
        return evaluation
    if require_gate_pass and not evaluation.get("gate_pass"):
        return {
            "error": "gate_failed",
            "message": "evaluate gate did not pass; pass require_gate_pass=false to override",
            "evaluation": evaluation,
        }
    path = resolve_artifact_path(artifact_id, settings)
    if path is None:
        return {"error": "not_found", "message": f"artifact not found: {artifact_id}"}
    root = prefilter_dir(settings)
    current = root / "current.json"
    previous = root / "previous.json"
    state = load_state(root)
    if current.is_file():
        previous.write_bytes(current.read_bytes())
        state["previous_artifact_id"] = state.get("current_artifact_id")
    current.write_bytes(path.read_bytes())
    state["current_artifact_id"] = path.stem
    state["dirty"] = False
    save_state(root, state)
    return {
        "tenant_id": tenant_id,
        "ok": True,
        "promoted_artifact_id": path.stem,
        "current_path": str(current),
        "active_model_path": str(current),
        "previous_artifact_id": state.get("previous_artifact_id"),
        "gate_pass": evaluation.get("gate_pass"),
        "threshold": threshold,
    }


def rollback_prefilter(
    settings: Any,
    *,
    tenant_id: str,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    root = prefilter_dir(settings)
    state = load_state(root)
    target_id = artifact_id or state.get("previous_artifact_id")
    if not target_id:
        previous = root / "previous.json"
        if not previous.is_file():
            return {"error": "not_found", "message": "no previous artifact to roll back to"}
        current = root / "current.json"
        if current.is_file():
            current.replace(root / "current.rollback.json")
        previous.replace(current)
        state["current_artifact_id"], state["previous_artifact_id"] = (
            state.get("previous_artifact_id"),
            state.get("current_artifact_id"),
        )
        state["dirty"] = False
        save_state(root, state)
        return {
            "tenant_id": tenant_id,
            "ok": True,
            "current_artifact_id": state.get("current_artifact_id"),
            "rolled_back_to": "previous.json",
        }
    return promote_prefilter(
        settings,
        tenant_id=tenant_id,
        artifact_id=str(target_id),
        require_gate_pass=False,
    )
