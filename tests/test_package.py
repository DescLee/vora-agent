def test_package_version_and_import():
    from importlib import import_module

    package = import_module("manus_mini")

    assert package.__version__ == "v20260702.1644"

    prompt_tui = import_module("manus_mini.prompt_tui")
    assert not hasattr(prompt_tui, "main")
