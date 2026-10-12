"""Loopback browser session keys, memory only. Never return a key or its prefix."""
from contextlib import contextmanager
from contextvars import ContextVar
from http.cookies import SimpleCookie, CookieError
from secrets import token_urlsafe
from threading import RLock
from time import monotonic

current_key = ContextVar('deepseek_request_key', default=None)
COOKIE = 'logireflow_ai_session'


class KeyVault:
    def __init__(self, ttl=3600, limit=32):
        self.ttl, self.limit = ttl, limit
        self.values = {}
        self.lock = RLock()

    @staticmethod
    def token(cookie):
        try:
            jar = SimpleCookie(); jar.load(cookie or '')
            return jar[COOKIE].value if COOKIE in jar else None
        except CookieError:
            return None

    def _prune(self):
        for token, (_, expiry) in list(self.values.items()):
            if monotonic() >= expiry:
                del self.values[token]

    def put(self, cookie, key):
        if not isinstance(key, str) or not 16 <= len(key) <= 256 or not key.isascii() or any(c.isspace() or ord(c)<33 or ord(c)>126 for c in key):
            raise ValueError('密钥需为16–256个ASCII字符，不含空白；请重新粘贴')
        with self.lock:
            self._prune()
            token = self.token(cookie)
            if token not in self.values:
                if len(self.values) >= self.limit:
                    raise ValueError('临时密钥会话数量已达上限，请稍后重试')
                token = token_urlsafe(32)
            self.values[token] = (key, monotonic() + self.ttl)
            return token

    def get(self, cookie):
        with self.lock:
            self._prune()
            entry = self.values.get(self.token(cookie))
            return entry[0] if entry else None

    def clear(self, cookie):
        with self.lock:
            self.values.pop(self.token(cookie), None)

    @contextmanager
    def using(self, cookie):
        handle = current_key.set(self.get(cookie))
        try:
            yield
        finally:
            current_key.reset(handle)
