from pathlib import Path


def test_package_version_and_import():
    from importlib import import_module

    package = import_module("manus_mini")

    assert package.__version__ == "v20260702.1644"

    prompt_tui = import_module("manus_mini.prompt_tui")
    assert not hasattr(prompt_tui, "main")


def test_readme_positions_project_as_agent_runtime_not_interview_project() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "本地 Agent Runtime" in readme
    assert "面试项目" not in readme
    assert "面向面试展示" not in readme
