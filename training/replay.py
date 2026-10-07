"""Replay a verified WarehouseBench split without model or robot execution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from training.materialbrain_training.replay import main

    main()
