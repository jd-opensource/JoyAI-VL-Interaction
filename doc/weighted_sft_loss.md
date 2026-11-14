# Weighted cross-entropy for LLaMA-Factory SFT

`training/weighted_sft_loss.py` provides a small LLaMA-Factory-compatible trainer
mixin for supervised fine-tuning (SFT) with per-token weighted cross-entropy.
This is intended for scenarios where some response tokens, time-aligned labels,
or domain-specific spans should contribute more or less to the SFT objective.

## How to use

Copy or import the mixin in the LLaMA-Factory training code and place it before
the concrete SFT trainer class in the inheritance order:

```python
from llamafactory.train.sft.trainer import CustomSeq2SeqTrainer
from training.weighted_sft_loss import WeightedSFTTrainerMixin


class WeightedSFTTrainer(WeightedSFTTrainerMixin, CustomSeq2SeqTrainer):
    pass
```

Then instantiate `WeightedSFTTrainer` wherever the normal SFT trainer is created.
If a batch does not include token weights, the mixin delegates to the original
trainer loss unchanged.

## Batch format

The data collator may add one of these keys:

- `loss_weights` (preferred)
- `token_weights`
- `weights`

The tensor shape should be `[batch_size, sequence_length]`, aligned with
`labels`. The helper applies the same causal-LM shift as Hugging Face models, so
weight `[:, t]` is applied to the loss for predicting label `[:, t]`. Labels equal
to `-100` are ignored.

Example:

```python
batch = {
    "input_ids": input_ids,
    "attention_mask": attention_mask,
    "labels": labels,
    "loss_weights": loss_weights,
}
```

The weighted loss is normalized by the sum of valid weights, making a weight of
`1.0` equivalent to the standard mean cross-entropy.
