"""Patchable LLaMA-Factory integration for weighted SFT loss.

This file is designed to be copied into a LLaMA-Factory checkout or imported from
this repository when launching LLaMA-Factory. It wires the weighted loss helper
into the trainer class, preserves custom batch keys in the collator, and exposes a
single function that can monkey-patch LLaMA-Factory's SFT trainer before trainer
creation.

Example launcher patch::

    from training.llamafactory_weighted_sft_patch import apply_weighted_sft_patch

    apply_weighted_sft_patch(enabled=True, remove_unused_columns=False)
    from llamafactory.cli import main
    main()

For YAML/CLI configs, also set ``remove_unused_columns: false`` so Hugging Face
Trainer does not drop ``loss_weights``/``token_weights``/``weights`` before they
reach ``compute_loss``.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from training.weighted_sft_loss import WeightedSFTTrainerMixin

LOSS_WEIGHT_KEYS = ("loss_weights", "token_weights", "weights")


def _require_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise ImportError("Weighted LLaMA-Factory SFT integration requires PyTorch.") from exc
    return torch


def pad_loss_weight_sequences(
    features: Iterable[Mapping[str, Any]],
    key: str = "loss_weights",
    padding_value: float = 0.0,
):
    """Pad per-token loss weights from collator features into a tensor.

    This helper mirrors the sequence padding that a tokenizer/data collator does
    for labels. Missing feature weights are filled with ``1.0`` for the feature's
    label length so unannotated examples retain ordinary CE behavior. Padding
    positions are filled with ``0.0`` and therefore do not contribute to the
    normalized weighted loss.
    """

    torch = _require_torch()
    features = list(features)
    if not features:
        return torch.empty(0, dtype=torch.float32)

    lengths = [len(feature.get(key, feature.get("labels", []))) for feature in features]
    max_length = max(lengths, default=0)
    rows = []
    for feature, length in zip(features, lengths):
        values = feature.get(key)
        if values is None:
            values = [1.0] * length
        if hasattr(values, "detach"):
            values = values.detach().cpu().tolist()
        row = [float(value) for value in values]
        rows.append(row + [padding_value] * (max_length - len(row)))
    return torch.tensor(rows, dtype=torch.float32)


def make_weight_preserving_data_collator(base_collator: Callable[[list[dict[str, Any]]], dict[str, Any]]):
    """Wrap a LLaMA-Factory/HF collator so custom weight keys survive batching."""

    def collate(features: list[dict[str, Any]]) -> dict[str, Any]:
        weight_key = next((key for key in LOSS_WEIGHT_KEYS if any(key in item for item in features)), None)
        weight_tensor = pad_loss_weight_sequences(features, weight_key) if weight_key else None
        clean_features = [
            {key: value for key, value in feature.items() if key not in LOSS_WEIGHT_KEYS}
            for feature in features
        ]
        batch = base_collator(clean_features)
        if weight_key is not None:
            batch[weight_key] = weight_tensor.to(device=batch["labels"].device)
        return batch

    return collate


def build_weighted_sft_trainer_class(base_trainer_class: type) -> type:
    """Create a concrete weighted trainer from LLaMA-Factory's SFT trainer."""

    if issubclass(base_trainer_class, WeightedSFTTrainerMixin):
        return base_trainer_class
    return type("Weighted" + base_trainer_class.__name__, (WeightedSFTTrainerMixin, base_trainer_class), {})


def apply_weighted_sft_patch(
    enabled: bool = True,
    remove_unused_columns: bool = False,
) -> type | None:
    """Patch LLaMA-Factory's SFT trainer class before trainer construction.

    Args:
        enabled: When ``False``, no patch is applied.
        remove_unused_columns: Must be ``False`` for weighted SFT. The argument is
            explicit so launcher code can fail fast if a config would drop custom
            batch keys.

    Returns:
        The patched weighted trainer class, or ``None`` when disabled.
    """

    if not enabled:
        return None
    if remove_unused_columns:
        raise ValueError(
            "Weighted SFT requires remove_unused_columns=False so loss weight "
            "columns reach the data collator and trainer."
        )

    try:
        import llamafactory.train.sft.trainer as sft_trainer_module
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise ImportError(
            "Could not import LLaMA-Factory. Run this patch inside a "
            "LLaMA-Factory environment."
        ) from exc

    base_class = getattr(sft_trainer_module, "CustomSeq2SeqTrainer", None)
    if base_class is None:
        raise AttributeError("LLaMA-Factory SFT trainer module has no CustomSeq2SeqTrainer.")

    weighted_class = build_weighted_sft_trainer_class(base_class)
    sft_trainer_module.CustomSeq2SeqTrainer = weighted_class
    return weighted_class
