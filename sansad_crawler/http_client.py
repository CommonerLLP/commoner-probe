from __future__ import annotations

import json
import types
import urllib.error
import urllib.request
from urllib.parse import urlencode


try:
    import requests  # type: ignore
except ModuleNotFoundError:

    class StdlibResponse:
        def __init__(self, url: str, status_code: int, body: bytes):
            self.url = url
            self.status_code = status_code
            self._body = body
            self.text = body.decode("utf-8", errors="replace")

        def json(self) -> dict | list:
            return json.loads(self.text)

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code} for {self.url}")

        def iter_content(self, chunk_size: int = 16384):
            for i in range(0, len(self._body), chunk_size):
                yield self._body[i : i + chunk_size]

    class StdlibSession:
        def get(self, url: str, **kwargs) -> StdlibResponse:
            params = kwargs.get("params")
            if params:
                sep = "&" if "?" in url else "?"
                url = url + sep + urlencode(params)
            headers = kwargs.get("headers") or {}
            timeout = kwargs.get("timeout") or 60
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return StdlibResponse(url, resp.status, resp.read())
            except urllib.error.HTTPError as exc:
                return StdlibResponse(url, exc.code, exc.read())

    requests = types.SimpleNamespace(Session=StdlibSession)


def make_session():
    return requests.Session()

