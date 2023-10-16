"""Start Viseron."""
from __future__ import annotations

import signal
import sys

from viseron import Viseron, setup_viseron


def main():
    """Start Viseron."""
    viseron: Viseron | None = None

    def signal_term(*_) -> None:
        if viseron:
            viseron.shutdown()

    # Listen to signals
    signal.signal(signal.SIGTERM, signal_term)
    signal.signal(signal.SIGINT, signal_term)

    viseron = setup_viseron()

    signal.pause()
    return viseron.exit_code if viseron else 0


def init():
    """Initialize."""
    return main() if __name__ == "__main__" else 1


sys.exit(init())
