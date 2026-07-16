"""Roundtrip HMAC key primitives without persistence or logging side effects."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass


KEY_BYTES = 32
KEY_ID_DOMAIN = b"Open-FuLuA.Roundtrip.KeyId.v1\x00"


class RoundtripKeyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _key_id(material: bytes) -> str:
    return hashlib.sha256(KEY_ID_DOMAIN + material).hexdigest()


@dataclass(frozen=True, repr=False)
class RoundtripSigningKey:
    """A fixed-size HMAC key whose representation never exposes key material."""

    key_id: str
    material: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.material, bytes) or len(self.material) != KEY_BYTES:
            raise RoundtripKeyError("ROUNDTRIP_KEY_LENGTH_INVALID")
        if self.key_id != _key_id(self.material):
            raise RoundtripKeyError("ROUNDTRIP_KEY_ID_MISMATCH")

    @classmethod
    def from_bytes(cls, material: bytes) -> "RoundtripSigningKey":
        raw = bytes(material)
        return cls(key_id=_key_id(raw), material=raw)

    def __repr__(self) -> str:
        return f"RoundtripSigningKey(key_id={self.key_id!r}, material=<redacted>)"


def generate_signing_key() -> RoundtripSigningKey:
    return RoundtripSigningKey.from_bytes(secrets.token_bytes(KEY_BYTES))
