from __future__ import annotations

import threading
from concurrent.futures import CancelledError
from contextvars import ContextVar, Token
from typing import Callable, TypeVar


T = TypeVar("T")


class CancellationToken:
    """Cooperative cancellation with an atomic final-commit boundary."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requested = threading.Event()
        self._committed = False

    @property
    def requested(self) -> bool:
        return self._requested.is_set()

    def request(self) -> bool:
        with self._lock:
            if self._committed:
                return False
            self._requested.set()
            return True

    def raise_if_requested(self) -> None:
        if self._requested.is_set():
            raise CancelledError("operation cancelled by user")

    def commit(self, callback: Callable[[], T]) -> T:
        with self._lock:
            self.raise_if_requested()
            result = callback()
            self._committed = True
            return result


_CURRENT_TOKEN: ContextVar[CancellationToken | None] = ContextVar(
    "operation_cancellation_token",
    default=None,
)


def bind_cancellation_token(token: CancellationToken) -> Token:
    return _CURRENT_TOKEN.set(token)


def reset_cancellation_token(context_token: Token) -> None:
    _CURRENT_TOKEN.reset(context_token)


def raise_if_cancelled() -> None:
    token = _CURRENT_TOKEN.get()
    if token is not None:
        token.raise_if_requested()


def commit_if_not_cancelled(callback: Callable[[], T]) -> T:
    token = _CURRENT_TOKEN.get()
    return callback() if token is None else token.commit(callback)
