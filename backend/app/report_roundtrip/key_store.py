"""Machine-local persistence for the R7 HMAC key.

Windows uses current-user DPAPI.  POSIX test/development hosts use a private
0600 file; key material is never stored below the public ``storage`` mount.
"""

from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import wintypes
from pathlib import Path

from ..config import settings
from .keys import RoundtripKeyError, RoundtripSigningKey, generate_signing_key


ENTROPY = b"Open-FuLuA.Roundtrip.KeyStore.v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def signing_key_path() -> Path:
    return settings.database_path.parent / "private" / "roundtrip" / "signing-key.v1"


def _blob(data: bytes) -> tuple[_Blob, object]:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        return data
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(ENTROPY)
    output = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "Open-FuLuA R7", ctypes.byref(entropy), None, None,
        0x1, ctypes.byref(output),
    ):
        raise RoundtripKeyError("ROUNDTRIP_KEY_DPAPI_PROTECT_FAILED")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer, entropy_buffer


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        return data
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(ENTROPY)
    output = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None,
        0x1, ctypes.byref(output),
    ):
        raise RoundtripKeyError("ROUNDTRIP_KEY_DPAPI_UNPROTECT_FAILED")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer, entropy_buffer


def load_or_create_signing_key() -> RoundtripSigningKey:
    path = signing_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return RoundtripSigningKey.from_bytes(_unprotect(path.read_bytes()))
    key = generate_signing_key()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    protected = _protect(key.material)
    try:
        with temporary.open("xb") as output:
            output.write(protected)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        try:
            # Linking a complete private file into the final name is an atomic
            # create-if-absent operation.  A concurrent winner is loaded back
            # instead of returning a key that was never persisted.
            os.link(temporary, path)
        except FileExistsError:
            return load_signing_key()
        except OSError as exc:
            if path.is_file():
                return load_signing_key()
            raise RoundtripKeyError("ROUNDTRIP_KEY_ATOMIC_CREATE_FAILED") from exc
        return key
    finally:
        temporary.unlink(missing_ok=True)


def load_signing_key() -> RoundtripSigningKey:
    path = signing_key_path()
    if not path.is_file():
        raise RoundtripKeyError("ROUNDTRIP_SIGNING_KEY_UNAVAILABLE")
    return RoundtripSigningKey.from_bytes(_unprotect(path.read_bytes()))
