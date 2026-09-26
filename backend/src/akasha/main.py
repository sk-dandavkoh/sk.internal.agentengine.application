"""Application entry point.

Phase 00 scope: proves the process starts. Bootstrap, configuration and the
HTTP server are introduced in Phase 01.
"""

import sys

from akasha import __version__


def main() -> int:
    """Start the backend process."""
    sys.stdout.write(f"akasha-engine {__version__}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
