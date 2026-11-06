"""Installed-package smoke tests; run against a wheel as well as editable installs."""

from importlib import import_module
from importlib.metadata import version
from importlib.resources import files


def test_distribution_metadata_and_typing_marker() -> None:
    assert version("klados-world") == "0.1.0.dev0"
    assert files("klados_world").joinpath("py.typed").is_file()


def test_all_package_boundaries_are_installed() -> None:
    for module in ("society", "application", "api", "workers", "persistence", "integrations"):
        assert import_module(f"klados_world.{module}") is not None
