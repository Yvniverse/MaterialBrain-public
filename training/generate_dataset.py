"""Generate verified, group-disjoint synthetic warehouse decisions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training.materialbrain_training.dataset import main

if __name__ == "__main__":
    main()
