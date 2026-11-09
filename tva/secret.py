"""Password storage. On Windows the password is encrypted with DPAPI (tied to the Windows user account),
so the database copied to another PC or user cannot reveal it. Elsewhere (tests) it is only base64."""
from __future__ import annotations

import base64
import sys

PREFIX_DPAPI = "dpapi:"
PREFIX_B64 = "b64:"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out))  # UI_FORBIDDEN
    if not ok:
        raise OSError("Windows could not encrypt/decrypt the saved password.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect(plain: str) -> str:
    if not plain:
        return ""
    if sys.platform.startswith("win"):
        return PREFIX_DPAPI + base64.b64encode(_dpapi(plain.encode("utf-8"), True)).decode()
    return PREFIX_B64 + base64.b64encode(plain.encode("utf-8")).decode()


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith(PREFIX_DPAPI):
        return _dpapi(base64.b64decode(stored[len(PREFIX_DPAPI):]), False).decode("utf-8")
    if stored.startswith(PREFIX_B64):
        return base64.b64decode(stored[len(PREFIX_B64):]).decode("utf-8")
    return stored
