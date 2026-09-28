"""Shared pytest options and fixtures."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """
    Register `--update-golden` for regenerating expected outputs.

    Parameters:
      parser: Pytest option parser.
    """
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="Rewrite golden and regression expectations from current output.",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    """
    Report whether golden files should be rewritten.

    Parameters:
      request: Pytest request.
    Returns:
      True when `--update-golden` was passed.
    """
    return bool(request.config.getoption("--update-golden"))
