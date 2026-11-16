"""Runtime state lives under the Windows-provided user temporary directory."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from .i18n import t

# Deliberately distinct from the sibling DesktopBounce runtime directory.
RUNTIME_DIRECTORY_NAME = 'DesktopIconBilliards'


def _temporary_directory() -> Path:
    """Return the Windows per-user temporary directory."""
    get_temp_path = ctypes.windll.kernel32.GetTempPathW
    get_temp_path.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    get_temp_path.restype = ctypes.c_uint32
    size = 260
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        length = get_temp_path(size, buffer)
        if not length:
            raise ctypes.WinError()
        if length < size:
            break
        # The buffer was too small; retry with the required length.
        size = length + 1
    return Path(buffer.value)


def _candidate_bases():
    """Where runtime files may live, best first: the temp directory, then the user's profile.

    The temp directory can be missing, redirected to a read-only place or locked down by a
    security policy; the profile folders are the fallback so the game still starts there.
    """
    try:
        yield _temporary_directory()
    except OSError:
        pass
    for variable in ('LOCALAPPDATA', 'APPDATA'):
        base = os.environ.get(variable)
        if base:
            yield Path(base)


def runtime_directory() -> Path:
    """Return a writable per-user directory for journals and logs, creating it if needed.

    Raises the last ``OSError`` only when no candidate location can be created and written to.
    """
    last_error = OSError(t('paths.error.no_directory'))
    for base in _candidate_bases():
        directory = base / RUNTIME_DIRECTORY_NAME
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / '.write-test'
            probe.write_text('', encoding='utf-8')
            probe.unlink()
            return directory
        except OSError as error:
            last_error = error
    raise last_error
