"""Watchdog for long-running processes."""
from __future__ import annotations

import datetime
import logging
import multiprocessing as mp

from viseron.watchdog import WatchDog

LOGGER = logging.getLogger(__name__)


class RestartableProcess:
    """A restartable process.

    Like multiprocessing.Process, but registers itself in a watchdog which monitors the
    process.
    """

    def __init__(
        self, *args, name=None, grace_period=20, register=True, **kwargs
    ) -> None:
        self._args = args
        self._name = name
        self._grace_period = grace_period
        self._kwargs = kwargs
        self._process: mp.Process | None = None
        self._started = False
        self._start_time: float | None = None
        self._register = register

    def __getattr__(self, attr):
        """Forward all undefined attribute calls to mp.Process."""
        if attr in self.__class__.__dict__:
            return getattr(self, attr)
        return getattr(self._process, attr)

    @property
    def name(self):
        """Return process name."""
        return self._name

    @property
    def grace_period(self) -> int:
        """Return process grace period."""
        return self._grace_period

    @property
    def process(self) -> mp.Process | None:
        """Return process."""
        return self._process

    @property
    def started(self) -> bool:
        """Return if process has started."""
        return self._started

    @property
    def start_time(self) -> float | None:
        """Return process start time."""
        return self._start_time

    @property
    def exitcode(self) -> int | None:
        """Return process exit code."""
        return self._process.exitcode if self._process else 0

    def start(self) -> None:
        """Start the process."""
        self._process = mp.Process(
            *self._args,
            **self._kwargs,
        )
        self._start_time = datetime.datetime.now().timestamp()
        self._started = True
        self._process.start()
        if self._register:
            ProcessWatchDog.register(self)

    def restart(self, timeout: float | None = None) -> None:
        """Restart the process."""
        self._started = False
        if self._process:
            self._process.terminate()
            self._process.join(timeout=timeout)
            self._process.kill()
        self.start()

    def is_alive(self) -> bool:
        """Return if the process is alive."""
        return self._process.is_alive() if self._process else False

    def join(self, timeout: float | None = None) -> None:
        """Join the process."""
        if self._process:
            self._process.join(timeout=timeout)

    def terminate(self) -> None:
        """Terminate the process."""
        self._started = False
        ProcessWatchDog.unregister(self)
        if self._process:
            self._process.terminate()

    def kill(self) -> None:
        """Kill the process."""
        self._started = False
        ProcessWatchDog.unregister(self)
        if self._process:
            self._process.kill()


class ProcessWatchDog(WatchDog):
    """A watchdog for long running processes."""

    registered_items: list[RestartableProcess] = []

    def __init__(self) -> None:
        super().__init__()
        self._scheduler.add_job(self.watchdog, "interval", seconds=15)

    def watchdog(self) -> None:
        """Check for stopped processes and restart them."""
        for registered_process in self.registered_items:
            if not registered_process.started:
                continue
            if registered_process.is_alive():
                continue

            now = datetime.datetime.now().timestamp()
            if (
                registered_process.start_time
                and now - registered_process.start_time
                < registered_process.grace_period
            ):
                continue

            LOGGER.error(f"Process {registered_process.name} has exited, restarting")
            registered_process.restart()
