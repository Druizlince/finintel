"""Tiny read-only JSON fetcher with a file cache. Stdlib only.

API keys travel only in the query string to the provider; they are stripped from every
error message and never written to the cache (cache keys are hashes of the key-less URL).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
SECRET_PARAMS = {"token", "apikey", "api_key"}
UA = "finintel/1.0 (personal research; read-only)"


class FetchError(Exception):
    pass


def _strip(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k.lower() not in SECRET_PARAMS]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


class Fetcher:
    def __init__(self, cache_dir: Path | None, timeout: float = 15.0) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_file(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(_strip(url).encode()).hexdigest()[:32] + ".json")

    def get_json(self, url: str, ttl: int = 3600, refresh: bool = False) -> Any:
        f = self._cache_file(url)
        if f and f.exists() and not refresh:
            try:
                blob = json.loads(f.read_text(encoding="utf-8"))
                if time.time() - blob["fetched"] < ttl:
                    return blob["data"]
            except (ValueError, KeyError, OSError):
                pass
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        last = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                last = f"HTTP {exc.code} from {_strip(url)}"
                if exc.code in (401, 402, 403, 404):
                    raise FetchError(last + (" (check the API key / free-plan limits)"
                                             if exc.code in (401, 402, 403) else "")) from None
                if exc.code == 429:
                    time.sleep(1.5)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last = f"{type(exc).__name__} for {_strip(url)}"
                time.sleep(0.5)
        else:
            raise FetchError(last or "request failed")
        if isinstance(data, dict) and any(k in data for k in ("Error Message", "error")) \
                and len(data) <= 2:
            raise FetchError(f"{_strip(url)}: {str(data.get('Error Message') or data.get('error'))[:200]}")
        if f:
            try:
                f.write_text(json.dumps({"fetched": time.time(), "data": data}), encoding="utf-8")
            except OSError:
                log.warning("could not write research cache")
        return data
