from __future__ import annotations

from pathlib import Path


class Reporter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def write_markdown(self, filename: str, content: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / filename
        path.write_text(content, encoding="utf-8")
        return path
