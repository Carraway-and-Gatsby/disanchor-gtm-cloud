"""Process-local staging lock; not a substitute for credential/OS isolation."""
from contextlib import contextmanager
from contextvars import ContextVar
import os

_POSTING_LOCK = ContextVar("gtm_posting_lock", default=False)


def posting_locked():
    # Any value, including an empty or misspelled value, keeps the lock on.
    return _POSTING_LOCK.get() or "GTM_POSTING_LOCK" in os.environ


@contextmanager
def publication_lock(enabled=False):
    token = _POSTING_LOCK.set(_POSTING_LOCK.get() or enabled)
    try:
        yield
    finally:
        _POSTING_LOCK.reset(token)
