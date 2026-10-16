"""API keys in Windows Credential Manager instead of a plain file.

data/api_keys.json held every provider key in clear text on disk (16 Oct
2026). Credential Manager keeps them encrypted for this Windows account
(Control Panel > Credential Manager > Windows Credentials, "Athena/<name>").
Plain ctypes on advapi32 - no extra library.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

_PREFIX = "Athena/"
_GENERIC, _PERSIST_LOCAL = 1, 2


class _CRED(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _api():
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CRED))]
    a.CredWriteW.argtypes = [ctypes.POINTER(_CRED), wintypes.DWORD]
    a.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    a.CredFree.argtypes = [ctypes.c_void_p]
    return a


def get(name: str) -> str:
    try:
        a, p = _api(), ctypes.POINTER(_CRED)()
        if not a.CredReadW(_PREFIX + name, _GENERIC, 0, ctypes.byref(p)):
            return ""
        try:
            c = p.contents
            return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        finally:
            a.CredFree(p)
    except (OSError, AttributeError):
        return ""                                   # not Windows: the caller falls back


def put(name: str, secret: str) -> bool:
    blob = secret.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob) if blob else (ctypes.c_ubyte * 1)()
    c = _CRED(Type=_GENERIC, TargetName=_PREFIX + name, CredentialBlobSize=len(blob),
              CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)), Persist=_PERSIST_LOCAL,
              UserName="Athena", Comment="Athena API key")
    try:
        return bool(_api().CredWriteW(ctypes.byref(c), 0))
    except (OSError, AttributeError):
        return False


def delete(name: str) -> bool:
    try:
        return bool(_api().CredDeleteW(_PREFIX + name, _GENERIC, 0))
    except (OSError, AttributeError):
        return False


if __name__ == "__main__":
    assert put("__selfcheck__", "s3cr€t-value") and get("__selfcheck__") == "s3cr€t-value"
    assert delete("__selfcheck__") and get("__selfcheck__") == ""
    print("credstore: checks pass")
