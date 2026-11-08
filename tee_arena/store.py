"""Atomic JSON documents for agent state: one writer at a time, no lost updates.

Ported from ``memory_store.py`` in TEE-internal (see ``reference/``). The original
cannot be reused as is: its read and write paths call into about 50 other TEE
modules (privacy projection, reconsideration, X, operations). This port keeps only
the transaction mechanism:

- a per-file lock (thread lock plus ``flock`` on a separate, permanent lock file);
- read, change and validate under that lock;
- optimistic concurrency: a caller can pass the raw bytes it read, and the write is
  refused if the file changed in between;
- the new content is fsynced to a temporary file, then atomically renamed over the
  old one, so a crash never leaves a half-written document.

POSIX only, like the arena server. Documents are JSON objects.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading
import time

_thread_locks = {}
_thread_locks_guard = threading.Lock()


class Conflict(ValueError):
    """The document changed since it was read; nothing was written."""


class Busy(TimeoutError):
    """Another writer held the lock for longer than the timeout."""


def _encode(value):
    if not isinstance(value, dict):
        raise ValueError("A stored document must be a JSON object.")
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode("utf-8")


@contextmanager
def lock(path, timeout=10.0):
    """Hold the exclusive lock for ``path`` across threads and processes."""
    path = Path(path).resolve()
    with _thread_locks_guard:
        thread_lock = _thread_locks.setdefault(str(path), threading.Lock())
    deadline = time.monotonic() + timeout
    if not thread_lock.acquire(timeout=max(0.0, timeout)):
        raise Busy(f"{path.name} is busy.")
    try:
        # The lock lives in its own file, so it survives atomic replacement of the
        # document itself.
        with path.with_name(path.name + ".lock").open("a+b", buffering=0) as file:
            while True:
                try:
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise Busy(f"{path.name} is busy.") from None
                    time.sleep(0.025)
            try:
                yield
            finally:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
    finally:
        thread_lock.release()


def _write_atomic(path, payload, *, exclusive):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        if exclusive:
            os.link(temporary, path)  # Fails if the document already exists.
        else:
            os.replace(temporary, path)
            temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create(path, value, *, validate=None):
    """Create a new document; never overwrites an existing one."""
    path = Path(path).resolve()
    if validate is not None:
        validate(value)
    payload = _encode(value)
    with lock(path):
        if path.exists():
            raise FileExistsError(f"{path.name} already exists.")
        _write_atomic(path, payload, exclusive=True)


def read(path):
    """Return ``(raw_bytes, document)`` read under the lock."""
    path = Path(path).resolve()
    with lock(path):
        raw = path.read_bytes()
    return raw, json.loads(raw)


def update(path, change, *, expected_raw=None, validate=None):
    """Apply ``change(document)`` atomically and return the new document.

    ``change`` edits the document in place. It must only change data: no waiting
    for input and no model calls while the lock is held. If ``expected_raw`` is
    given and the file no longer matches it, ``Conflict`` is raised and nothing is
    written. If the change leaves the document identical, nothing is written.
    """
    path = Path(path).resolve()
    with lock(path):
        raw = path.read_bytes()
        if expected_raw is not None and raw != expected_raw:
            raise Conflict(f"{path.name} changed since it was read; nothing saved.")
        document = json.loads(raw)
        before = deepcopy(document)
        change(document)
        if validate is not None:
            validate(document)
        if document == before:
            return document
        _write_atomic(path, _encode(document), exclusive=False)
        return document
