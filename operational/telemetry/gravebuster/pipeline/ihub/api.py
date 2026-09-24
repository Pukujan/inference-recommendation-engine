"""GET-only InferHub client. The key is read from a 0600 env file and sent only as a header.

* Only ``GET`` is implemented; there is no code path that can POST (no paid inference possible).
* Management API + MCP share 30 req/min per account; calls to https://inferhub.dev/api are paced
  (``IHUB_MGMT_MIN_INTERVAL_S``, default 2.5 s => <= 24/min, leaving room for the PC helper).
* 429: honours ``Retry-After`` (or the body ``retryAfter``), up to 3 retries, then gives up on that
  endpoint for this run (the next timer run retries). 5xx/timeouts: 2 retries with backoff.
* Every error string passes through ``redact`` so a key can never reach a log.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

INFERENCE_BASE = "https://api.inferhub.dev"
MGMT_BASE = "https://inferhub.dev/api"
DEFAULT_ENV = "/srv/agent-telemetry/secrets/inferhub.env"
USER_AGENT = "agent-telemetry-ihub/1 (GET-only market+billing collector; IRE #46)"
_KEY_RE = re.compile(r"sk-[A-Za-z0-9]+-[A-Za-z0-9_\-]+")


def redact(s: str) -> str:
    return _KEY_RE.sub("sk-<redacted>", s or "")


def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


@dataclass
class Resp:
    url: str  # never contains the key (key is a header)
    status: int
    body: bytes
    headers: dict[str, str]
    elapsed_ms: float
    attempts: int
    error: str | None = None


class Client:
    def __init__(self, env_file: str | None = None, timeout: float = 60.0) -> None:
        import requests  # imported lazily so transform/tests need no third-party packages

        self._requests = requests
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        self.timeout = timeout
        self.inference_base = INFERENCE_BASE
        self.mgmt_base = MGMT_BASE
        self._key: str | None = None
        path = env_file or os.environ.get("IHUB_ENV_FILE", DEFAULT_ENV)
        if os.path.exists(path):
            env = load_env(path)
            self._key = env.get("INFERHUB_API_KEY") or None
            if env.get("INFERHUB_API_URL"):
                self.inference_base = re.sub(r"/v1/?$", "", env["INFERHUB_API_URL"]).rstrip("/")
            if env.get("INFERHUB_MANAGEMENT_URL"):
                self.mgmt_base = env["INFERHUB_MANAGEMENT_URL"].rstrip("/")
        self.mgmt_min_interval = float(os.environ.get("IHUB_MGMT_MIN_INTERVAL_S", "2.5"))
        self._last_mgmt = 0.0
        self.calls = {"mgmt": 0, "inference": 0, "429": 0}

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def url_for(self, path: str) -> str:
        base = self.inference_base if path.startswith("/v1/") else self.mgmt_base
        return base + path

    def get(self, path: str, params: dict[str, Any] | None = None, auth: bool = True) -> Resp:
        url = self.url_for(path)
        is_mgmt = not path.startswith("/v1/")
        headers = {}
        if auth:
            if not self._key:
                return Resp(url, 0, b"", {}, 0.0, 0, "no INFERHUB_API_KEY in env file")
            headers["Authorization"] = "Bearer " + self._key
        attempts, rl_retries, err_retries = 0, 0, 0
        while True:
            if is_mgmt:
                wait = self.mgmt_min_interval - (time.monotonic() - self._last_mgmt)
                if wait > 0:
                    time.sleep(wait)
                self._last_mgmt = time.monotonic()
            attempts += 1
            self.calls["mgmt" if is_mgmt else "inference"] += 1
            t0 = time.monotonic()
            try:
                r = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            except self._requests.RequestException as e:  # timeout / connection
                if err_retries < 2:
                    err_retries += 1
                    time.sleep(5 * err_retries)
                    continue
                return Resp(url, 0, b"", {}, 0.0, attempts, redact(f"{type(e).__name__}: {e}"))
            ms = (time.monotonic() - t0) * 1000.0
            hdrs = {
                k.lower(): v
                for k, v in r.headers.items()
                if k.lower().startswith("x-ratelimit") or k.lower() in ("retry-after", "date")
            }
            if r.status_code == 429 and rl_retries < 3:
                rl_retries += 1
                self.calls["429"] += 1
                ra = 10.0
                try:
                    ra = float(r.headers.get("Retry-After") or r.json()["error"]["retryAfter"])
                except Exception:
                    pass
                time.sleep(min(65.0, ra + 1.0))
                continue
            if r.status_code >= 500 and err_retries < 2:
                err_retries += 1
                time.sleep(5 * err_retries)
                continue
            err = None if r.status_code == 200 else redact(f"HTTP {r.status_code}: {r.text[:300]}")
            return Resp(url_with(url, params), r.status_code, r.content, hdrs, ms, attempts, err)


def url_with(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    from urllib.parse import urlencode

    return url + "?" + urlencode({k: v for k, v in params.items() if v is not None})
