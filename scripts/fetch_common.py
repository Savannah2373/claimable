"""Shared bits for the fetch_* scripts: stamped JSONL output, one writer.

load_opportunities.py consumes these files; keeping a single writer means a
serialization fix (encoding, a non-JSON-safe field) lands for every source
at once instead of drifting per script.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from claimable.ingestion.grants_gov import Opportunity


def write_jsonl(opportunities: Iterable[Opportunity], prefix: str,
                out_dir: str = "data/raw") -> Path:
    """Write opportunities as JSONL to a UTC-stamped file; returns the path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{prefix}_{stamp}.jsonl"
    with path.open("w") as f:
        for opp in opportunities:
            f.write(json.dumps(dataclasses.asdict(opp)) + "\n")
    return path
