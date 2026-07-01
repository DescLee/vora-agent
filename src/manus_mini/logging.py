from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manus_mini.redaction import redact_sensitive_value


class EventLogger:
    def __init__(self, root: Path, enabled: bool | None = None) -> None:
        self.root = root
        if enabled is None:
            enabled = os.environ.get("MANUS_DISABLE_LOGGING") != "1" and not os.environ.get("PYTEST_CURRENT_TEST")
        self.enabled = enabled

    def record(self, run_id: str, event: dict[str, Any]) -> Path:
        run_dir = self.root / run_id
        path = run_dir / "events.jsonl"
        if not self.enabled:
            return path
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = redact_sensitive_value({"ts": datetime.now(UTC).isoformat(), "run_id": run_id, **event})
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path
