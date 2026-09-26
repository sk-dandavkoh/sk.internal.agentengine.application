"""Phase 00 baseline tests: package imports and the process starts."""

import akasha
from akasha.main import main


def test_version_is_defined() -> None:
    assert akasha.__version__


def test_application_starts() -> None:
    assert main() == 0
