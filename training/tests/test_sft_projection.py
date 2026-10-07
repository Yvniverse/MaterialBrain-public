"""Completion vocabulary projection preserves masked causal CE and gradients."""

import unittest
from types import SimpleNamespace

from training.materialbrain_training.sft import completion_cross_entropy


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        try:
            import torch
        except ImportError:
            self.skipTest("Install optional training requirements for SFT loss checks")

        self.torch = torch
        torch.manual_seed(41)
        self.labels = torch.tensor(
            [[-100, -100, -100, -100, 2, 3, 4, -100], [-100, -100, 5, 6, -100, -100, -100, -100]]
        )
        self.inputs = {
            "input_ids": torch.zeros_like(self.labels),
            "attention_mask": torch.ones_like(self.labels),
            "labels": self.labels,
        }
        self.values = torch.randn(2, 8, 9)

    def policy(self, values):
        torch = self.torch

        class Policy(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.values = torch.nn.Parameter(values.clone())

            def forward(self, input_ids, attention_mask, use_cache, logits_to_keep):
                self.positions = logits_to_keep
                assert use_cache is False
                return SimpleNamespace(logits=self.values[:, logits_to_keep, :])

        return Policy()

    def reference(self, values, labels, denominator=None):
        torch = self.torch
        total = torch.nn.functional.cross_entropy(
            values[:, :-1].reshape(-1, values.shape[-1]),
            labels[:, 1:].reshape(-1),
            ignore_index=-100,
            reduction="sum",
        )
        return total / ((labels[:, 1:] != -100).sum() if denominator is None else denominator)

    def test_loss_matches_full_projection_with_different_prompt_lengths(self):
        model = self.policy(self.values)
        loss, output = completion_cross_entropy(model, self.inputs)
        self.torch.testing.assert_close(loss, self.reference(self.values, self.labels))
        self.assertLess(output.logits.shape[1], self.values.shape[1])
        self.assertIn("labels", self.inputs)

    def test_gradient_matches_full_masked_causal_loss(self):
        model = self.policy(self.values)
        expected = self.values.clone().requires_grad_()
        completion_cross_entropy(model, self.inputs)[0].backward()
        self.reference(expected, self.labels).backward()
        self.torch.testing.assert_close(model.values.grad, expected.grad)

    def test_accumulation_uses_total_tokens_for_unequal_microbatches(self):
        denominator = (self.labels[:, 1:] != -100).sum()
        accumulated = 0
        for index in range(2):
            inputs = {key: value[index : index + 1] for key, value in self.inputs.items()}
            model = self.policy(self.values[index : index + 1])
            loss, _ = completion_cross_entropy(model, inputs, denominator)
            accumulated = accumulated + loss
        self.torch.testing.assert_close(accumulated, self.reference(self.values, self.labels))

    def test_empty_supervision_is_rejected(self):
        inputs = {**self.inputs, "labels": self.torch.full_like(self.labels, -100)}
        with self.assertRaisesRegex(RuntimeError, "no supervised"):
            completion_cross_entropy(self.policy(self.values), inputs)

    def test_zero_denominator_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "denominator"):
            completion_cross_entropy(self.policy(self.values), self.inputs, 0)


if __name__ == "__main__":
    unittest.main()
