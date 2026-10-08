"""Conventional LoRA SFT over verified static warehouse decisions."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .common import canonical, digest, read_json, read_jsonl, verify_dataset, write_json
from .model import adapter_manifest, load_policy, verify_adapter


def encode_record(tokenizer, row, cutoff):
    import torch

    messages = row["messages"]
    prompt = tokenizer.apply_chat_template(
        messages[:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
    target = tokenizer.encode(
        messages[-1]["content"] + tokenizer.eos_token, add_special_tokens=False
    )
    if len(prompt_ids) + len(target) > cutoff:
        return None
    return {
        "input_ids": torch.tensor(prompt_ids + target, dtype=torch.long),
        "attention_mask": torch.ones(len(prompt_ids) + len(target), dtype=torch.long),
        "labels": torch.tensor([-100] * len(prompt_ids) + target, dtype=torch.long),
    }


class StaticDecisions:
    def __init__(self, rows, tokenizer, cutoff):
        self.examples = []
        self.dropped = []
        self.lengths = []
        for row in rows:
            encoded = encode_record(tokenizer, row, cutoff)
            if encoded is None:
                self.dropped.append(row["record_id"])
            else:
                self.examples.append(encoded)
                self.lengths.append(len(encoded["input_ids"]))
        if not self.examples:
            raise RuntimeError("No complete assistant labels fit the selected cutoff")

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, index):
        return self.examples[index]


def completion_cross_entropy(model, inputs, num_items_in_batch=None):
    """Exact masked causal CE, projecting only columns containing a supervised label."""
    import torch
    from torch.nn import functional as F

    labels = inputs["labels"]
    shifted = labels[:, 1:]
    positions = torch.nonzero((shifted != -100).any(dim=0), as_tuple=False).flatten()
    if not positions.numel():
        raise RuntimeError("SFT batch has no supervised assistant tokens")
    values = {key: value for key, value in inputs.items() if key != "labels"}
    outputs = model(**values, use_cache=False, logits_to_keep=positions)
    targets = shifted[:, positions]
    loss = F.cross_entropy(
        outputs.logits.float().reshape(-1, outputs.logits.shape[-1]),
        targets.reshape(-1),
        ignore_index=-100,
        reduction="sum",
    )
    denominator = num_items_in_batch if num_items_in_batch is not None else (targets != -100).sum()
    if denominator <= 0:
        raise RuntimeError("SFT supervised-token denominator must be positive")
    return loss / denominator, outputs


def train(args):
    from transformers import (
        DataCollatorForSeq2Seq,
        Trainer,
        TrainerCallback,
        TrainingArguments,
        set_seed,
    )

    manifest = verify_dataset(args.data, snapshot=args.snapshot, frozen_receipt=args.receipt)
    snapshot = read_json(args.snapshot)
    if snapshot["map"]["revision"] != manifest["map_revision"]:
        raise RuntimeError("Snapshot/dataset identity mismatch")
    rows = read_jsonl(args.data / "sft-train.jsonl")
    if any(row["split"] != "train" for row in rows):
        raise RuntimeError("Training split violation")
    if args.smoke:
        rows = rows[:64]
    config = {
        "model_path": str(args.model),
        "source_sha": args.source_sha,
        "dataset_manifest_sha256": digest(manifest),
        "smoke": args.smoke,
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "cutoff": args.cutoff,
        "batch_size": args.batch_size,
        "accumulation": args.accumulation,
        "seed": args.seed,
        "loss_implementation": "exact_masked_completion_projection_ce_v1",
    }
    resume_step = 0
    if args.resume:
        if args.resume.parent.resolve() != args.output.resolve():
            raise RuntimeError("SFT resume checkpoint must belong to this run")
        if read_json(args.output / "SFT_CONFIG.json") != config:
            raise RuntimeError("SFT resume immutable input/hyperparameter identity mismatch")
        receipt = verify_adapter(
            args.resume,
            required_files={
                "optimizer.pt",
                "scheduler.pt",
                "rng_state.pth",
                "trainer_state.json",
            },
        )
        resume_step = read_json(args.resume / "trainer_state.json")["global_step"]
        if receipt.get("global_step") != resume_step or any(
            receipt.get(key) != config[key]
            for key in ("model_path", "source_sha", "dataset_manifest_sha256")
        ):
            raise RuntimeError("SFT checkpoint lineage mismatch")
    elif (args.output / "SFT_CONFIG.json").exists():
        raise RuntimeError("Existing SFT run requires explicit verified resume")
    set_seed(args.seed)
    model, tokenizer, loading = load_policy(args.model, trainable=True)
    tokenizer.padding_side = "right"
    dataset = StaticDecisions(rows, tokenizer, args.cutoff)
    if len(dataset.dropped) > max(1, int(len(rows) * 0.01)):
        raise RuntimeError(
            "Cutoff would lose over 1% complete decisions; "
            "choose it from the frozen length distribution"
        )
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    if not args.resume:
        write_json(output / "SFT_CONFIG.json", config)
    write_json(
        output / "SFT_INPUT_AUDIT.json",
        {
            "records": len(rows),
            "accepted": len(dataset),
            "dropped": dataset.dropped,
            "cutoff": args.cutoff,
            "length_min": min(dataset.lengths),
            "length_max": max(dataset.lengths),
            "length_mean": sum(dataset.lengths) / len(dataset.lengths),
            "dataset_manifest_sha256": digest(manifest),
            "train_only": True,
            "resume": str(args.resume) if args.resume else None,
            "loading_info": loading,
        },
    )

    class Heartbeat(TrainerCallback):
        def on_save(self, callback_args, state, control, logs=None, **kwargs):
            adapter_manifest(
                output / f"checkpoint-{state.global_step}",
                model_path=args.model,
                metadata={
                    "stage": "sft_smoke" if args.smoke else "sft",
                    "source_sha": args.source_sha,
                    "global_step": state.global_step,
                    "dataset_manifest_sha256": digest(manifest),
                },
            )

        def on_log(self, callback_args, state, control, logs=None, **kwargs):
            write_json(
                output / "heartbeat.json",
                {
                                        "step": state.global_step,
                    "epoch": state.epoch,
                    "logs": logs or {},
                    "timestamp": time.time(),
                },
            )
            print(
                canonical(
                    {
                        "sft_step": state.global_step,
                        "epoch": state.epoch,
                        **(logs or {}),
                    }
                ),
                flush=True,
            )

    options = TrainingArguments(
        output_dir=str(output),
        learning_rate=args.learning_rate,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.accumulation,
        bf16=True,
        fp16=False,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=5,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=4,
        report_to=[],
        dataloader_num_workers=0,
        optim="adamw_torch",
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        max_steps=8 if args.smoke else -1,
        remove_unused_columns=False,
        seed=args.seed,
        data_seed=args.seed,
        ddp_find_unused_parameters=False,
    )

    class CompletionTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            loss, outputs = completion_cross_entropy(model, inputs, num_items_in_batch)
            return (loss, outputs) if return_outputs else loss

    trainer = CompletionTrainer(
        model=model,
        args=options,
        train_dataset=dataset,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer, padding=True, label_pad_token_id=-100, pad_to_multiple_of=8
        ),
        callbacks=[Heartbeat()],
    )
    # Normalize over the accumulation group's supervised tokens, with no second
    # gradient-accumulation division inside Trainer.training_step.
    trainer.model_accepts_loss_kwargs = True
    started = time.perf_counter()
    result = trainer.train(resume_from_checkpoint=str(args.resume) if args.resume else None)
    final = output / "adapter-final"
    model.save_pretrained(final, safe_serialization=True)
    tokenizer.save_pretrained(final)
    metadata = {
        "stage": "sft_smoke" if args.smoke else "sft",
        "model_path": str(args.model),
        "loading_info": loading,
        "resumed_from_step": resume_step,
        "updates_this_invocation": trainer.state.global_step - resume_step,
        "seed": args.seed,
        "learning_rate": args.learning_rate,
        "requested_epochs": args.epochs,
        "completed_epoch": trainer.state.epoch,
        "global_steps": trainer.state.global_step,
        "elapsed_s": time.perf_counter() - started,
        "metrics": result.metrics,
        "lora_rank": 32,
        "lora_alpha": 64,
        "lora_dropout": 0.05,
        "bf16": True,
        "gradient_checkpointing": True,
        "loss_implementation": config["loss_implementation"],
        "train_only": True,
        "dataset_manifest_sha256": digest(manifest),
        "source_sha": args.source_sha,
        "adapter_path": str(final),
            }
    if not args.smoke and (trainer.state.epoch is None or trainer.state.epoch < args.epochs - 0.01):
        raise RuntimeError("Requested SFT epochs did not finish")
    if trainer.state.global_step <= resume_step:
        raise RuntimeError("SFT invocation performed no optimizer updates")
    adapter_manifest(final, model_path=args.model, metadata=metadata)
    write_json(output / "SFT_TRAINING.json", metadata)
    write_json(output / "training_curve.json", trainer.state.log_history)
    # Smoke proves save/load separately from finishing optimization. A later
    # scheduled invocation resumes optimizer state from a real checkpoint.
    print(canonical(metadata), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, help="Optional dataset input hash receipt")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--cutoff", type=int, default=4096)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--accumulation", type=int, default=8)
    parser.add_argument("--save-steps", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20261005)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--smoke", action="store_true")
    train(parser.parse_args())


if __name__ == "__main__":
    main()
