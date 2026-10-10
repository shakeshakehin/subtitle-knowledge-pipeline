from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


class CredentialStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def _windows_apis():
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer,
        wintypes.LPCWSTR,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer,
        ctypes.c_void_p,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _protect_windows(data: bytes) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("安全保存 API Key 目前只支持 Windows DPAPI")
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(b"fusion-video-pipeline/api-key/v1")
    protected = _DataBlob()
    crypt32, kernel32 = _windows_apis()
    # Keep buffers alive until CryptProtectData returns.
    _ = source_buffer, entropy_buffer
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Fusion Video Pipeline API Key",
        ctypes.byref(entropy),
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(protected),
    ):
        raise CredentialStoreError(f"Windows DPAPI 加密失败：{ctypes.get_last_error()}")
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(protected.pbData)


def _unprotect_windows(data: bytes) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("读取已保存 API Key 目前只支持 Windows DPAPI")
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(b"fusion-video-pipeline/api-key/v1")
    clear = _DataBlob()
    crypt32, kernel32 = _windows_apis()
    _ = source_buffer, entropy_buffer
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        ctypes.byref(entropy),
        None,
        None,
        0x1,
        ctypes.byref(clear),
    ):
        raise CredentialStoreError(f"Windows DPAPI 解密失败：{ctypes.get_last_error()}")
    try:
        return ctypes.string_at(clear.pbData, clear.cbData)
    finally:
        kernel32.LocalFree(clear.pbData)


@dataclass(frozen=True)
class ApiCredentialStore:
    path: Path

    def available(self) -> bool:
        return self.path.is_file() and not self.path.is_symlink()

    def save(self, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise ValueError("要保存的 API Key 不能为空")
        if self.path.exists() and self.path.is_symlink():
            raise CredentialStoreError("拒绝向符号链接写入 API Key")
        encrypted = _protect_windows(value.encode("utf-8"))
        payload = {
            "version": 1,
            "protection": "windows-dpapi-current-user",
            "ciphertext": base64.b64encode(encrypted).decode("ascii"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def load(self) -> str:
        if not self.available():
            raise CredentialStoreError("尚未保存可复用的 API Key")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("protection") != "windows-dpapi-current-user":
                raise CredentialStoreError("API Key 存储格式不受支持")
            encrypted = base64.b64decode(payload["ciphertext"], validate=True)
            value = _unprotect_windows(encrypted).decode("utf-8").strip()
        except CredentialStoreError:
            raise
        except (KeyError, ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialStoreError("已保存的 API Key 无法读取或已损坏") from exc
        if not value:
            raise CredentialStoreError("已保存的 API Key 为空")
        return value
