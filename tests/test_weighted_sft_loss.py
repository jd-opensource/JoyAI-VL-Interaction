from types import SimpleNamespace

import pytest

from training.llamafactory_weighted_sft_patch import make_weight_preserving_data_collator
from training.weighted_sft_loss import WeightedSFTTrainerMixin, pop_loss_weights, weighted_causal_cross_entropy


torch = pytest.importorskip("torch")


def test_weighted_causal_cross_entropy_matches_torch_weighted_mean_and_ignore_index():
    logits = torch.tensor(
        [
            [
                [3.0, 0.0],
                [0.0, 4.0],
                [2.0, 0.0],
                [0.0, 1.0],
            ]
        ],
        dtype=torch.float32,
    )
    labels = torch.tensor([[0, 1, -100, 0]])
    weights = torch.tensor([[99.0, 2.0, 5.0, 0.5]], dtype=torch.float64)

    loss = weighted_causal_cross_entropy(logits, labels, weights)

    per_token = torch.nn.functional.cross_entropy(
        logits[:, :-1, :].reshape(-1, 2),
        labels[:, 1:].reshape(-1),
        ignore_index=-100,
        reduction="none",
    )
    shifted_weights = weights[:, 1:].to(dtype=logits.dtype).reshape(-1)
    valid = labels[:, 1:].reshape(-1).ne(-100)
    expected = (per_token[valid] * shifted_weights[valid]).sum() / shifted_weights[valid].sum()

    assert loss.device == logits.device
    assert loss.dtype == logits.dtype
    assert torch.allclose(loss, expected)


def test_weighted_causal_cross_entropy_without_weights_matches_torch_ce():
    logits = torch.tensor(
        [
            [
                [1.0, 2.0, 0.0],
                [0.5, 0.0, 1.5],
                [2.0, 1.0, 0.0],
            ]
        ],
        dtype=torch.float32,
    )
    labels = torch.tensor([[0, -100, 2]])

    loss = weighted_causal_cross_entropy(logits, labels)
    expected = torch.nn.functional.cross_entropy(
        logits[:, :-1, :].reshape(-1, 3),
        labels[:, 1:].reshape(-1),
        ignore_index=-100,
    )

    assert torch.allclose(loss, expected)


def test_weighted_trainer_compute_loss_strips_labels_and_sets_returned_loss():
    class DummyModel:
        def __init__(self):
            self.seen_inputs = None

        def __call__(self, **inputs):
            self.seen_inputs = inputs
            assert "labels" not in inputs
            assert "loss_weights" not in inputs
            logits = torch.tensor([[[3.0, 0.0], [0.0, 3.0], [1.0, 0.0]]])
            return SimpleNamespace(logits=logits, loss=torch.tensor(123.0))

    class ParentTrainer:
        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            return torch.tensor(-1.0)

    class Trainer(WeightedSFTTrainerMixin, ParentTrainer):
        pass

    model = DummyModel()
    labels = torch.tensor([[0, 1, 0]])
    weights = torch.tensor([[1.0, 2.0, 3.0]])
    loss, outputs = Trainer().compute_loss(
        model,
        {"input_ids": torch.tensor([[5, 6, 7]]), "labels": labels, "loss_weights": weights},
        return_outputs=True,
    )

    expected = weighted_causal_cross_entropy(model.seen_inputs["input_ids"].new_tensor([[[3.0, 0.0], [0.0, 3.0], [1.0, 0.0]]], dtype=torch.float32), labels, weights)
    assert torch.allclose(loss, expected)
    assert torch.allclose(outputs.loss, loss)


def test_weight_preserving_collator_keeps_custom_key_and_removes_from_features():
    def base_collator(features):
        assert all("loss_weights" not in feature for feature in features)
        return {
            "labels": torch.tensor([feature["labels"] for feature in features]),
            "input_ids": torch.tensor([feature["input_ids"] for feature in features]),
        }

    collator = make_weight_preserving_data_collator(base_collator)
    batch = collator(
        [
            {"input_ids": [1, 2, 3], "labels": [1, 2, -100], "loss_weights": [1.0, 2.0, 3.0]},
            {"input_ids": [4, 5, 0], "labels": [4, 5, -100], "loss_weights": [0.5, 0.25, 0.0]},
        ]
    )

    assert "loss_weights" in batch
    assert batch["loss_weights"].device == batch["labels"].device
    assert torch.allclose(batch["loss_weights"], torch.tensor([[1.0, 2.0, 3.0], [0.5, 0.25, 0.0]]))


def test_pop_loss_weights_accepts_common_aliases_and_removes_key():
    expected = object()

    batch = {"loss_weights": expected, "labels": object()}
    assert pop_loss_weights(batch) is expected
    assert "loss_weights" not in batch

    assert pop_loss_weights({"token_weights": expected}) is expected
    assert pop_loss_weights({"weights": expected}) is expected
    assert pop_loss_weights({"labels": expected}) is None
