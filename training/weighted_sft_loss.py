"""Weighted cross-entropy helpers for LLaMA-Factory SFT.

This module is intentionally small and dependency-light so it can be copied into a
LLaMA-Factory checkout or imported by a custom trainer.  It implements the same
causal-language-model label shift used by Hugging Face models while allowing a
batch to provide per-token loss weights.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def _require_torch():
    """Import torch lazily so documentation tooling can import this file."""

    try:
        import torch
        import torch.nn.functional as functional
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise ImportError(
            "training.weighted_sft_loss requires PyTorch. Install torch before "
            "using the weighted SFT trainer."
        ) from exc
    return torch, functional


def weighted_causal_cross_entropy(
    logits: Any,
    labels: Any,
    weights: Optional[Any] = None,
    ignore_index: int = -100,
) -> Any:
    """Compute causal LM cross entropy with optional per-token weights.

    Args:
        logits: Model logits with shape ``[batch, seq_len, vocab_size]``.
        labels: Target token ids with shape ``[batch, seq_len]``. Positions equal
            to ``ignore_index`` do not contribute to the loss.
        weights: Optional tensor with shape ``[batch, seq_len]``. Weights are
            aligned with labels, so the first column is dropped together with the
            causal shift. When ``None``, this function is equivalent to ordinary
            mean cross entropy over non-ignored shifted labels.
        ignore_index: Label value ignored by the loss.

    Returns:
        A scalar PyTorch tensor.
    """

    torch, functional = _require_torch()

    shifted_logits = logits[..., :-1, :].contiguous()
    shifted_labels = labels[..., 1:].contiguous()

    flat_logits = shifted_logits.view(-1, shifted_logits.size(-1))
    flat_labels = shifted_labels.view(-1)

    if weights is None:
        return functional.cross_entropy(flat_logits, flat_labels, ignore_index=ignore_index)

    shifted_weights = weights[..., 1:].contiguous().to(device=shifted_logits.device, dtype=shifted_logits.dtype)
    flat_weights = shifted_weights.view(-1)
    valid_mask = flat_labels.ne(ignore_index)

    if not bool(valid_mask.any()):
        return flat_logits.sum() * 0.0

    token_losses = functional.cross_entropy(
        flat_logits,
        flat_labels,
        ignore_index=ignore_index,
        reduction="none",
    )
    weighted_losses = token_losses[valid_mask] * flat_weights[valid_mask]
    denominator = flat_weights[valid_mask].sum().clamp_min(torch.finfo(flat_weights.dtype).eps)
    return weighted_losses.sum() / denominator


def pop_loss_weights(inputs: Mapping[str, Any]) -> Optional[Any]:
    """Return the first supported per-token weight tensor from a trainer batch.

    The aliases make the helper easy to use with different dataset/data-collator
    conventions without requiring a LLaMA-Factory fork.
    """

    for key in ("loss_weights", "token_weights", "weights"):
        if key in inputs:
            return inputs[key]
    return None


class WeightedSFTTrainerMixin:
    """Mixin that adds weighted CE loss to a LLaMA-Factory/HF Trainer.

    Use this mixin before the concrete LLaMA-Factory SFT trainer in the method
    resolution order, for example::

        class WeightedSFTTrainer(WeightedSFTTrainerMixin, CustomSeq2SeqTrainer):
            pass

    Batches may include ``loss_weights`` (preferred), ``token_weights``, or
    ``weights`` with shape ``[batch, seq_len]``. If no weights are provided, this
    mixin delegates to the parent trainer unchanged.
    """

    def compute_loss(self, model: Any, inputs: dict[str, Any], return_outputs: bool = False, **kwargs: Any):
        weights = pop_loss_weights(inputs)
        if weights is None:
            return super().compute_loss(model, inputs, return_outputs=return_outputs, **kwargs)

        labels = inputs.get("labels")
        if labels is None:
            raise ValueError("Weighted SFT loss requires `labels` in the trainer inputs.")

        outputs = model(**inputs)
        loss = weighted_causal_cross_entropy(outputs.logits, labels, weights)
        return (loss, outputs) if return_outputs else loss
