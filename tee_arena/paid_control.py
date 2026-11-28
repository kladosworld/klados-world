"""Read-only host paid-authorization contract. Settlement never consults these gates."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import stat

CONTRACT = 1
DIRECTORY = Path('/etc/klados-control')
REASONS = frozenset(('lease_missing', 'lease_expired', 'lease_invalid', 'lease_future',
                     'lease_incompatible', 'social_paused', 'paid_stop', 'killed', 'control_unavailable'))


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None
    lease_id: str | None = None


def killed(directory=None):
    """A broken symlink still counts as an emergency stop; unreadable is stopped."""
    directory = DIRECTORY if directory is None else directory
    try:
        os.stat(Path(directory) / 'KILLED', follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def authorize(*, directory=None, now=None, mock=False, expected_uid=0, min_remaining=120):
    """No credentials, writes, ledger copies or cached authorization.

    Synthetic tests may supply a private directory/UID. The live builder always
    uses the fixed root-owned control mount and default expected_uid=0.
    """
    directory = DIRECTORY if directory is None else directory
    if killed(directory):
        return Decision(False, 'killed')
    if mock:
        return Decision(True)
    parent = source = None
    try:
        parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(parent)
        if info.st_uid != expected_uid or info.st_mode & 0o022:
            return Decision(False, 'control_unavailable')
        try:
            os.stat('PAID_STOP', dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return Decision(False, 'paid_stop')
        try:
            source = os.open('FINANCIAL_LEASE', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return Decision(False, 'lease_missing')
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid or info.st_mode & 0o022:
            return Decision(False, 'lease_invalid')
        raw = os.read(source, 513)
        return parse_lease(raw, now=now, min_remaining=min_remaining)
    except FileNotFoundError:
        return Decision(False, 'lease_missing')
    except (OSError, ValueError, UnicodeError, AttributeError):
        return Decision(False, 'control_unavailable')
    finally:
        if source is not None:
            os.close(source)
        if parent is not None:
            os.close(parent)


def parse_lease(raw, *, now=None, min_remaining=120):
    try:
        if not raw or len(raw) > 512:
            return Decision(False, 'lease_invalid')
        fields = {}
        for line in raw.decode('ascii').splitlines():
            name, equals, value = line.partition('=')
            if not equals or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                return Decision(False, 'lease_invalid')
            if name not in ('contract', 'lease_id', 'verified_at', 'valid_until', 'history_n'):
                continue
            if name in fields:
                return Decision(False, 'lease_invalid')
            fields[name] = value
        if set(fields) != {'contract', 'lease_id', 'verified_at', 'valid_until', 'history_n'}:
            return Decision(False, 'lease_invalid')
        if fields['contract'] != '1':
            return Decision(False, 'lease_incompatible')
        if not re.fullmatch(r'[0-9a-f]{32}', fields['lease_id']):
            return Decision(False, 'lease_invalid')
        if not re.fullmatch(r'0|[1-9][0-9]{0,19}', fields['history_n']):
            return Decision(False, 'lease_invalid')
        times = []
        for key in ('verified_at', 'valid_until'):
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', fields[key]):
                return Decision(False, 'lease_invalid')
            times.append(datetime.fromisoformat(fields[key].replace('Z', '+00:00')))
        verified, until = times
        current = now or datetime.now(timezone.utc)
        if (current.utcoffset() != timedelta(0) or not timedelta(0) < until-verified <= timedelta(minutes=20)
                or type(min_remaining) is not int or not 61 <= min_remaining <= 360):
            return Decision(False, 'lease_invalid')
        if verified > current:
            return Decision(False, 'lease_future')
        return (Decision(True, lease_id=fields['lease_id'])
                if until-current >= timedelta(seconds=min_remaining) else Decision(False, 'lease_expired'))
    except (ValueError, UnicodeError, TypeError, AttributeError):
        return Decision(False, 'lease_invalid')
