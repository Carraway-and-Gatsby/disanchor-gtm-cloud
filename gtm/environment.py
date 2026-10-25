"""Explicit private-file credentials loading without executing shell syntax."""
from __future__ import annotations

import os
from pathlib import Path
import stat

ALLOWED = frozenset({"UPLOAD_POST_API_KEY", "GA4_ACCESS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
                     "CLONE_APP_KEY", "CLONE_USER_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_USER_ID"})


def read_private_environment(path):
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid != os.getuid()):
                raise ValueError("Private environment file must be owner-only and regular")
            raw = handle.read(32769)
        if len(raw) > 32768:
            raise ValueError("Private environment file too large")
        values = {}
        for line in raw.decode("utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if (not separator or key not in ALLOWED or key in values
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise ValueError("Private environment file has invalid fields")
            values[key] = value
        return values
    except (OSError, UnicodeError):
        raise ValueError("Private environment file unavailable") from None


def load_private_environment(path):
    # Apply atomically after validating the complete file; never log values.
    os.environ.update(read_private_environment(path))
