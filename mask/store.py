"""Files that hold originals or the mapping are sealed with Windows DPAPI for the current user account.

Another Windows account, or a copy of the file on another machine, cannot open them. A program
running as the same user still can; this is not isolation from software the user runs.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path

MAGIC = b'MASK1\n'


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]


def _dpapi(name, data):
    buffer = ctypes.create_string_buffer(data, len(data))
    source, result = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), _Blob()
    call = getattr(ctypes.windll.crypt32, name)
    if not call(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free(ctypes.cast(result.data, ctypes.c_void_p))


def write(path: Path, value):
    temp = path.with_suffix('.tmp')
    temp.write_bytes(MAGIC + _dpapi('CryptProtectData', json.dumps(value, ensure_ascii=False).encode('utf-8')))
    temp.replace(path)


def read(path: Path):
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError(f'{path.name} 不是 Mask 的本机加密文件')
    try:
        return json.loads(_dpapi('CryptUnprotectData', data[len(MAGIC):]))
    except OSError as exc:
        raise ValueError(f'{path.name} 只能由创建它的 Windows 账户在本机打开') from exc
