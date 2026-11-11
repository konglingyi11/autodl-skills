"""User-scoped encrypted session storage. No password or plaintext session files."""
import ctypes
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


class SessionStoreError(Exception):
    pass


def _crypt(data, decrypt=False):
    if sys.platform != 'win32':
        raise SessionStoreError('本机加密会话存储目前仅支持 Windows')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    func.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise SessionStoreError('无法加密或解密本机会话')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class SessionStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'autodl-skill' / 'sessions'

    def _path(self, method, account=''):
        if method not in ('sub', 'sms', 'wechat'):
            raise ValueError('invalid session type')
        suffix = '-' + hashlib.sha256(account.encode('utf-8')).hexdigest() if method == 'sub' else ''
        return self.root / (method + suffix + '.bin')

    def load(self, method, account=''):
        path = self._path(method, account)
        if not path.exists():
            return None
        try:
            record = json.loads(_crypt(path.read_bytes(), decrypt=True).decode('utf-8'))
            if not isinstance(record, dict) or record.get('schema') != 1 or record.get('method') != method:
                raise ValueError('invalid session')
            return record.get('data')
        except (OSError, ValueError, TypeError, UnicodeError, SessionStoreError) as exc:
            raise SessionStoreError('本机会话无法读取；请使用 --fresh-login 重新登录') from exc

    def save(self, method, data, account=''):
        encrypted = _crypt(json.dumps({'schema': 1, 'method': method, 'data': data}, ensure_ascii=False).encode('utf-8'))
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(method, account)
        fd, temp = tempfile.mkstemp(prefix='.session-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encrypted)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def clear(self, method, account=''):
        self._path(method, account).unlink(missing_ok=True)
