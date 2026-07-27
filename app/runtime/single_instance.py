from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO


class AlreadyRunningError(RuntimeError):
    """Raised when another LoreLoop desktop process owns the instance lock."""


class SingleInstanceLock:
    """A process-scoped, non-blocking lock backed by a small persistent file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            _lock_file(handle)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise AlreadyRunningError(
                    "LoreLoop desktop is already running"
                ) from exc
            raise
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            _unlock_file(handle)
        finally:
            handle.close()

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


if os.name == "nt":
    import msvcrt

    def _lock_file(handle: BinaryIO) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(handle: BinaryIO) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
