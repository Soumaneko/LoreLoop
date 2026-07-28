from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes
from typing import Protocol


class SecretProtectionError(RuntimeError):
    """A persisted secret could not be protected or recovered."""


class SecretProtector(Protocol):
    scheme: str

    def protect(self, secret: str) -> str: ...

    def unprotect(self, protected: str) -> str: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class DPAPICurrentUserProtector:
    """Protect secrets for the current Windows logon using DPAPI."""

    scheme = "dpapi-current-user"
    _description = "LoreLoop model API key"
    # Protected model-config format v2 is permanently tied to entropy v1.
    # A future entropy change must use a new config format while retaining
    # the v2 loader so existing ciphertext can still be decrypted.
    _entropy = b"LoreLoop/model-api-key/v1"
    _ui_forbidden = 0x1

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise SecretProtectionError("DPAPI is available only on Windows")
        try:
            self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise SecretProtectionError("Windows DPAPI is unavailable") from exc
        self._configure_functions()

    def protect(self, secret: str) -> str:
        if not secret:
            return ""
        plaintext = secret.encode("utf-8")
        input_blob, input_buffer = _blob_from_bytes(plaintext)
        entropy_blob, entropy_buffer = _blob_from_bytes(self._entropy)
        output_blob = _DataBlob()
        _keep_alive = (input_buffer, entropy_buffer, output_blob)
        if not self._crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            self._description,
            ctypes.byref(entropy_blob),
            None,
            None,
            self._ui_forbidden,
            ctypes.byref(output_blob),
        ):
            raise SecretProtectionError(
                f"DPAPI protection failed: {_windows_error_text()}"
            )
        try:
            protected = ctypes.string_at(output_blob.pbData, output_blob.cbData)
            return base64.b64encode(protected).decode("ascii")
        finally:
            self._kernel32.LocalFree(output_blob.pbData)

    def unprotect(self, protected: str) -> str:
        if not protected:
            return ""
        try:
            ciphertext = base64.b64decode(protected, validate=True)
        except (ValueError, TypeError) as exc:
            raise SecretProtectionError("Protected API Key is not valid Base64") from exc
        input_blob, input_buffer = _blob_from_bytes(ciphertext)
        entropy_blob, entropy_buffer = _blob_from_bytes(self._entropy)
        output_blob = _DataBlob()
        _keep_alive = (input_buffer, entropy_buffer, output_blob)
        if not self._crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            self._ui_forbidden,
            ctypes.byref(output_blob),
        ):
            raise SecretProtectionError(
                f"DPAPI recovery failed: {_windows_error_text()}"
            )
        try:
            plaintext = ctypes.string_at(output_blob.pbData, output_blob.cbData)
            try:
                return plaintext.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SecretProtectionError(
                    "Recovered API Key is not valid UTF-8"
                ) from exc
        finally:
            self._kernel32.LocalFree(output_blob.pbData)

    def _configure_functions(self) -> None:
        blob_pointer = ctypes.POINTER(_DataBlob)
        self._crypt32.CryptProtectData.argtypes = [
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            blob_pointer,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p


def default_secret_protector(
    *, platform: str | None = None
) -> SecretProtector | None:
    active_platform = sys.platform if platform is None else platform
    if active_platform == "win32":
        return DPAPICurrentUserProtector()
    return None


def _blob_from_bytes(
    value: bytes,
) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(value), pointer), buffer


def _windows_error_text() -> str:
    code = ctypes.get_last_error()
    return f"[WinError {code}] {ctypes.FormatError(code).strip()}"
