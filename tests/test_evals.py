import json
from pathlib import Path

import pytest

from evals.run_evals import load_cases, main


def test_declared_eval_cases_have_unique_runners() -> None:
    cases = load_cases()

    assert len(cases) == 12
    assert len({case.case_id for case in cases}) == len(cases)


def test_eval_runner_writes_machine_and_human_reports(tmp_path: Path) -> None:
    json_report = tmp_path / "eval.json"
    markdown_report = tmp_path / "eval.md"

    exit_code = main(
        [
            "--json-report",
            str(json_report),
            "--markdown-report",
            str(markdown_report),
        ]
    )

    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["failed"] == 0
    assert "Manus Mini Eval 报告" in markdown_report.read_text(encoding="utf-8")
    assert "分类统计" in markdown_report.read_text(encoding="utf-8")


def test_eval_case_file_rejects_unknown_runner(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.zh.json"
    cases_path.write_text(
        '[{"id":"unknown","category":"security","target":"未知用例"}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="has no runner"):
        load_cases(cases_path)
