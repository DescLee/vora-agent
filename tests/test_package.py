def test_package_version_and_import():
    from importlib import import_module

    package = import_module("manus_mini")

    assert package.__version__ == "0.1.0"

    app = import_module("manus_mini.app")
    assert hasattr(app, "main")
