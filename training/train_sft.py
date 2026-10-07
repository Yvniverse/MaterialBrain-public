"""Train or resume a local-model WarehouseBench LoRA adapter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training.materialbrain_training.sft import main

if __name__ == "__main__":
    main()
