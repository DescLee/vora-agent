from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manus_mini.redaction import redact_sensitive_value


class EventLogger:
    def __init__(self, root: Path) -> None:
        self.root = root

    def record(self, run_id: str, event: dict[str, Any]) -> Path:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "events.jsonl"
        payload = redact_sensitive_value({"ts": datetime.now(UTC).isoformat(), "run_id": run_id, **event})
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path
