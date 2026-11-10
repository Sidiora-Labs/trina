"""Keys every run's configuration declares (clause 11.1).

These are the keys the run record reads. Sections owned by other packages are added
with ``BASE_SCHEMA.extend``.
"""

from __future__ import annotations

from trina.config.schema import Schema

# Order of the seeds in a run record (training.md section 3.1).
SEED_NAMES = ("global", "data_order", "init", "eval_bootstrap")

BASE_SCHEMA = Schema(
    {
        "run.stage": "str",
        "run.dataset_version": "str",
        **{f"seeds.{name}": "int" for name in SEED_NAMES},
    }
)
