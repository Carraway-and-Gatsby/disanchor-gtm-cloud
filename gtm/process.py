"""Bounded, thread-free subprocess capture for headless POSIX workers."""
from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import time


MAX_OUTPUT_BYTES = 64 * 1024
_SAFE_COMMAND = "bounded subprocess"


def _kill_group(process):
    # start_new_session makes the child's PID its process-group ID. Keep that
    # ID even after the leader exits, so descendants holding pipes are killed.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        # Reap our direct child. Descendants are reaped by their own parent/init.
        process.wait()


def bounded_run(args, *, input=None, capture_output=True, text=True, timeout=90, check=False):
    """Run an argv command with at most 64 KiB of stdout and stderr combined.

    Supports the ``subprocess.run`` options used by the model adapter. Input is
    UTF-8 text, output is UTF-8 text, and no shell or thread is created. Deadline
    or output overflow kills the whole process group before returning. Error
    objects deliberately omit captured output and original command arguments.
    """
    if os.name != "posix":
        raise subprocess.SubprocessError("bounded_process_requires_posix")
    if text is not True or capture_output is not True:
        raise ValueError("bounded_run_requires_text_and_capture_output")
    if input is not None and not isinstance(input, str):
        raise TypeError("bounded_run_requires_text_input")
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("bounded_run_requires_positive_finite_timeout")
    payload = memoryview((input or "").encode("utf-8"))
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
    except OSError:
        raise subprocess.SubprocessError("bounded_process_start_failed") from None

    stdout, stderr = bytearray(), bytearray()
    total = 0
    position = 0
    selector = None

    def close_pipe(pipe):
        try:
            selector.unregister(pipe)
        except KeyError:
            pass
        pipe.close()

    try:
        selector = selectors.DefaultSelector()
        for pipe, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        if payload:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(_SAFE_COMMAND, timeout)
            for key, _ in selector.select(remaining):
                pipe, name = key.fileobj, key.data
                if name == "stdin":
                    try:
                        position += os.write(pipe.fileno(), payload[position:position + 16384])
                    except BrokenPipeError:
                        close_pipe(pipe)
                        continue
                    except BlockingIOError:
                        continue
                    if position == len(payload):
                        close_pipe(pipe)
                    continue
                try:
                    chunk = os.read(pipe.fileno(), 16384)
                except BlockingIOError:
                    continue
                if not chunk:
                    close_pipe(pipe)
                    continue
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    raise subprocess.SubprocessError("bounded_process_output_limit_exceeded")
                (stdout if name == "stdout" else stderr).extend(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0 and process.poll() is None:
            raise subprocess.TimeoutExpired(_SAFE_COMMAND, timeout)
        try:
            returncode = process.wait(timeout=max(0, remaining))
        except subprocess.TimeoutExpired:
            raise subprocess.TimeoutExpired(_SAFE_COMMAND, timeout) from None
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, _SAFE_COMMAND)
        try:
            result = subprocess.CompletedProcess(args, returncode, stdout.decode("utf-8"), stderr.decode("utf-8"))
        except UnicodeError:
            raise subprocess.SubprocessError("bounded_process_invalid_utf8") from None
        return result
    except BaseException:
        _kill_group(process)
        raise
    finally:
        if selector is not None:
            selector.close()
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
        payload.release()
