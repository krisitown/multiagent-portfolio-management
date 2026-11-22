"""The documented package and installed command resolve without the old namespace."""

from importlib import import_module
from importlib.metadata import distribution


def test_installed_public_package_and_cli_identity():
    package = import_module("portfolio_management")
    cli = import_module("portfolio_management.cli")
    assert package.__file__ is not None
    assert callable(cli.main)
    entry_points = distribution("portfolio-management").entry_points
    assert any(
        item.group == "console_scripts"
        and item.name == "portfolio-management"
        and item.value == "portfolio_management.cli:main"
        for item in entry_points
    )
