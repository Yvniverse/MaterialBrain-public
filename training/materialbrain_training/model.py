"""Local Qwen3.5 text loading and hashed LoRA checkpoint artifacts."""

import json
from pathlib import Path

from .common import file_hash, read_json, write_json


def load_policy(
    model_path, *, adapter=None, trainable=False, rank=32, alpha=64, dropout=0.05
):
    import torch
    from transformers import AutoTokenizer, Qwen3_5ForCausalLM, Qwen3_5TextConfig

    model_path = Path(model_path)
    if not model_path.is_dir():
        raise RuntimeError("Model must be an existing local directory")
    if not torch.cuda.is_available():
        raise RuntimeError("SFT requires a CUDA device; dataset/replay work on CPU")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("SFT requires a CUDA device supporting bfloat16")
    tokenizer = AutoTokenizer.from_pretrained(
        str(model_path), local_files_only=True, padding_side="left"
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    # Transformers' qwen3_5_text conversion removes the language_model prefix
    # from the full released archive. Loading receipts reject absent text weights.
    # The vision tower is outside this text/tool policy and is never optimized.
    config = Qwen3_5TextConfig.from_pretrained(str(model_path), local_files_only=True)
    model, loading = Qwen3_5ForCausalLM.from_pretrained(
        str(model_path),
        config=config,
        local_files_only=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        device_map={"": "cuda"},
        output_loading_info=True,
    )
    # Transformers 5 returns sets in the loading diagnostics. Keep strict checks
    # and serialize their actual contents in every training/evaluation receipt.
    loading = json.loads(json.dumps(loading, default=lambda value: sorted(value)))
    loading["model_identity"] = {
        "model_config_sha256": file_hash(model_path / "config.json"),
        "tokenizer_sha256": file_hash(model_path / "tokenizer.json"),
        "model_index_sha256": (
            file_hash(model_path / "model.safetensors.index.json")
            if (model_path / "model.safetensors.index.json").exists()
            else None
        ),
        "text_hidden_size": config.hidden_size,
        "text_layers": config.num_hidden_layers,
        "model_path": str(model_path),
    }
    missing = [
        key
        for key in loading.get("missing_keys", [])
        if not (config.tie_word_embeddings and key.endswith("lm_head.weight"))
    ]
    unexpected = [
        key
        for key in loading.get("unexpected_keys", [])
        if not key.startswith(("model.visual.", "visual.", "mtp.", "model.mtp."))
    ]
    if (
        missing
        or unexpected
        or loading.get("mismatched_keys")
        or loading.get("error_msgs")
    ):
        raise RuntimeError("Checkpoint loading is incomplete: " + json.dumps(loading))
    model.config.use_cache = not trainable
    if adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(
            model, str(adapter), is_trainable=trainable, local_files_only=True
        )
    elif trainable:
        from peft import LoraConfig, get_peft_model

        targets = (
            r"model\.layers\..*\."
            r"(q_proj|k_proj|v_proj|o_proj|in_proj_qkv|in_proj_z|in_proj_a|in_proj_b|out_proj|"
            r"gate_proj|up_proj|down_proj)$"
        )
        config = LoraConfig(
            task_type="CAUSAL_LM",
            r=rank,
            lora_alpha=alpha,
            lora_dropout=dropout,
            target_modules=targets,
        )
        model = get_peft_model(model, config)
    if trainable:
        model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False}
        )
        model.enable_input_require_grads()
        model.train()
    else:
        model.eval()
    return model, tokenizer, loading


def verify_adapter(directory, *, required_files=()):
    path = Path(directory).resolve()
    manifest = read_json(path / "ADAPTER_MANIFEST.json")
    if not manifest.get("files"):
        raise RuntimeError("Adapter has no hashed artifacts")
    required = {"adapter_model.safetensors", "adapter_config.json", *required_files}
    if not required.issubset(manifest["files"]):
        raise RuntimeError(
            "Adapter receipt omits required model/config/optimizer artifacts"
        )
    for relative, expected in manifest["files"].items():
        target = (path / relative).resolve()
        if not target.is_relative_to(path) or file_hash(target) != expected["sha256"]:
            raise RuntimeError(f"Adapter artifact hash mismatch: {relative}")
    return manifest


def adapter_manifest(directory, *, model_path, metadata):
    path = Path(directory)
    artifacts = {
        str(file.relative_to(path)): {
            "sha256": file_hash(file),
            "bytes": file.stat().st_size,
        }
        for file in path.rglob("*")
        if file.is_file() and file.name != "ADAPTER_MANIFEST.json"
    }
    write_json(
        path / "ADAPTER_MANIFEST.json",
        {
            "model_path": str(model_path),
            "files": artifacts,
            **metadata,
        },
    )
