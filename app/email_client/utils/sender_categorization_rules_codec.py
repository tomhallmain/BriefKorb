"""Obfuscation helpers for bundled sender-categorization rules (not security-grade)."""

from __future__ import annotations

import base64
import zlib

# Distinct from other projects; rotate if rules leak becomes a concern.
_HEADER = b"BK_sc2_\xfe\xda\xc0" + b"v01_"
_FOOTER = b"_\xfd\xafBK_END"
_XOR_KEY = b"9pR#xL02@vKw!nQ"


def preprocess_data_for_encryption(data: str) -> bytes:
    """Wrap, compress, XOR, base64-encode, and reverse payload bytes."""
    data_bytes = data.encode("utf-8")
    wrapped = _HEADER + data_bytes + _FOOTER
    compressed = zlib.compress(wrapped, level=zlib.Z_BEST_COMPRESSION)
    key_stream = (_XOR_KEY * (len(compressed) // len(_XOR_KEY) + 1))[: len(compressed)]
    xored = bytes(a ^ b for a, b in zip(compressed, key_stream))
    b64 = base64.b64encode(xored)
    return b64[::-1]


def postprocess_data_from_decryption(encoded_data: bytes) -> str:
    """Inverse of :func:`preprocess_data_for_encryption`."""
    b64 = encoded_data[::-1]
    xored = base64.b64decode(b64)
    key_stream = (_XOR_KEY * (len(xored) // len(_XOR_KEY) + 1))[: len(xored)]
    compressed = bytes(a ^ b for a, b in zip(xored, key_stream))
    wrapped = zlib.decompress(compressed)
    data_bytes = wrapped[len(_HEADER) : -len(_FOOTER)]
    return data_bytes.decode("utf-8")
