"""Append-only raw store for API responses.

data/inferhub/raw/<endpoint>/<YYYY-MM-DD>/<HHMMSSmmm>Z-<sha8>.json.zst   (UTC day of fetch)
data/inferhub/raw/manifest.jsonl  one line per fetch: endpoint, url (no key), http status, fetched_at,
                                  body sha256 + bytes, zst sha256 + bytes, path (null when the body
                                  equals the previous stored body of the same endpoint: dup_of).
Files are written to a temp name, decompressed and verified against the sha256, then renamed.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .transform import sha256_hex


class RawStore:
    def __init__(self, root: str) -> None:
        self.root = root
        self.manifest = os.path.join(root, "manifest.jsonl")
        self.state_path = os.path.join(root, "_last_sha.json")
        os.makedirs(root, exist_ok=True)
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                self.last: dict[str, str] = json.load(fh)
        except (OSError, ValueError):
            self.last = {}

    def put(
        self,
        endpoint: str,
        body: bytes,
        fetched_at: str,
        url: str,
        status: int,
        meta: dict[str, Any] | None = None,
        dedup_consecutive: bool = True,
    ) -> dict[str, Any]:
        import zstandard

        sha = sha256_hex(body)
        rec: dict[str, Any] = {
            "endpoint": endpoint,
            "fetched_at": fetched_at,
            "url": url,
            "http_status": status,
            "sha256": sha,
            "bytes": len(body),
        }
        if meta:
            rec.update(meta)
        if dedup_consecutive and self.last.get(endpoint) == sha:
            rec.update(path=None, dup_of_previous=True)
        else:
            day = fetched_at[:10]
            stamp = fetched_at[11:23].replace(":", "").replace(".", "")
            rel = os.path.join(endpoint, day, f"{stamp}Z-{sha[:8]}.json.zst")
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            comp = zstandard.ZstdCompressor(level=12).compress(body)
            if zstandard.ZstdDecompressor().decompress(comp) != body:
                raise RuntimeError("zstd round-trip mismatch")
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(comp)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            rec.update(path=rel, zst_sha256=sha256_hex(comp), zst_bytes=len(comp))
            self.last[endpoint] = sha
        with open(self.manifest, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.last, fh)
        os.replace(tmp, self.state_path)
        return rec

    def read(self, rel: str) -> bytes:
        import zstandard

        with open(os.path.join(self.root, rel), "rb") as fh:
            return zstandard.ZstdDecompressor().decompress(fh.read())
