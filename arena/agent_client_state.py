"""Operator-private POSIX storage for a single reference client; standard library only."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
from uuid import uuid4


class StateError(Exception):
    pass


class State:
    def __init__(self, directory):
        if os.name != 'posix':
            raise StateError('Persistent credentials require verified operator-only POSIX storage')
        self.directory = Path(directory)
        parent = os.open(os.path.abspath(os.sep), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in self.directory.absolute().parent.parts[1:]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = following
                info = os.fstat(parent)
                if (info.st_uid not in (0, os.geteuid())
                        or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
                    raise StateError('Client parent directory is not trusted')
            try:
                os.mkdir(self.directory.name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            os.fsync(parent)  # Make the directory entry durable before any request.
        finally:
            os.close(parent)

    @staticmethod
    def _check(fd, *, directory=False):
        info = os.fstat(fd)
        if (info.st_uid != os.geteuid() or info.st_mode & 0o077
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
            raise StateError('Client storage must be private and owned by the current user')

    @contextmanager
    def locked(self):
        import fcntl
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        lock = None
        try:
            self._check(directory, directory=True)
            lock = os.open('client.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            self._check(lock)
            # Refuse a concurrent operator process instead of issuing parallel mutations.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                source = os.open('client.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                value = None
            else:
                with os.fdopen(source, 'rb') as stream:
                    self._check(stream.fileno())
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise StateError('Client state too large')
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get('version') != 1:
                    raise StateError('Invalid client state')
            yield value, lambda data: self.save(directory, data)
        finally:
            if lock is not None:
                os.close(lock)
            os.close(directory)

    @staticmethod
    def save(directory, value):
        name = 'client-' + uuid4().hex + '.tmp'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, 'client.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
