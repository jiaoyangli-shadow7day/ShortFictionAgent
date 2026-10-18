"""评测/回归 harness（V 层）。题材无关: fixture 自带 genre。"""

from .harness import (
    EvalHarness, load_fixtures, save_snapshot, compare_snapshots, FIXTURES_DIR,
)

__all__ = ["EvalHarness", "load_fixtures", "save_snapshot", "compare_snapshots", "FIXTURES_DIR"]

