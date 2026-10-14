"""The composition root is wired by hand and has no logic worth a unit test, but a typo in it stops the
nightly run, so it must at least import and understand every command."""
import importlib
import pathlib

import pytest


def test_the_entry_point_imports_and_knows_every_command():
    main = importlib.import_module("interface.cli.main")
    parser = main.build_parser()
    for argv in (
        ["sync", "all"],
        ["sync", "sheets"],
        ["sync", "baseline"],
        ["status"],
        ["features", "--from", "2026-09-10", "--to", "2026-09-12"],
        ["evaluate", "--horizon", "7", "--test-from", "2026-09-20"],
        ["coverage"],
    ):
        assert parser.parse_args(argv).func is not None


def test_the_horizon_is_limited_to_the_supported_windows():
    main = importlib.import_module("interface.cli.main")
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["evaluate", "--horizon", "14"])


def test_every_module_of_the_project_compiles():
    root = pathlib.Path(__file__).resolve().parents[2]
    for folder in ("domain", "application", "infrastructure", "interface"):
        for path in (root / folder).rglob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
