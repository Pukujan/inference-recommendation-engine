"""Daily read-only recommendation feed (IRE #75).

Turns the two committed lists (cheap Top 20 and frontier) into a small static, versioned JSON
feed that agents, dependent repos and the design-bakery page can fetch with one GET and no key:

    feed/v1/today.json             today's picks: both tiers, best route, live price, health
    feed/v1/days/YYYY-MM-DD.json   the same document for each ET day (append-only history)
    feed/v1/index.json             list of days with sha256 and URL
    feed/v1/schema.json            JSON Schema for today.json and days/*.json

It is published to the orphan branch ``data/ire-feed`` (no CI, no PRs, no noise on ``main``),
the same pattern as ``data/inferhub-price-snapshots``. Stable URL:
https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v1/today.json

Commands:
    feed.py build --out DIR      write feed/v1/ under DIR (keeps existing days/ and index)
    feed.py check DIR            secret guard: fail if any file looks like it holds a credential
    feed.py publish              build into a checkout of data/ire-feed, check, commit, push

The feed only carries public list data. ``check`` runs before every publish and refuses to push
anything key-like (API keys, bearer tokens, Authorization / x-api-key headers, GitHub tokens).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
LISTS_DIR = os.path.join(HERE, "lists")
SCHEMA_SRC = os.path.join(HERE, "schemas", "feed.v1.schema.json")
SCHEMA_VERSION = "ire-feed/v1"
SOURCE_REPO = "https://github.com/Pukujan/inference-recommendation-engine"
REPO_URL = os.environ.get("IRE_FEED_REPO", SOURCE_REPO + ".git")
BRANCH = os.environ.get("IRE_FEED_BRANCH", "data/ire-feed")
RAW_BASE = (
    "https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/"
)
STALE_AFTER_HOURS = 36
TOP20_JSON = "research_model_top20_recommendations.json"
TOP20_CSV = "research_model_top20_recommendations.csv"
FRONTIER_JSON = "research_model_frontier_recommendations.json"

# Anything that looks like a credential or an auth header. Kept broad on purpose: a false
# positive blocks one publish; a false negative leaks a key.
SECRET_PATTERNS = [
    ("api_key_sk", re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.I)),
    ("authorization_header", re.compile(r"\bAuthorization\s*[:=]", re.I)),
    ("x_api_key_header", re.compile(r"\bx-api-key\s*[:=]", re.I)),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("env_assignment", re.compile(r"\b[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET)\s*=\s*\S")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


# ---------------------------------------------------------------- helpers
def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _et_day(ts: str) -> str:
    """ET calendar day of a UTC timestamp (EDT -4 / EST -5 by the US rule)."""
    t = _parse(ts).astimezone(dt.timezone.utc)
    y = t.year
    # second Sunday of March, first Sunday of November, 2:00 local -> UTC
    mar = dt.datetime(y, 3, 8, 7, tzinfo=dt.timezone.utc)
    start = mar + dt.timedelta(days=(6 - mar.weekday()) % 7)
    nov = dt.datetime(y, 11, 1, 6, tzinfo=dt.timezone.utc)
    end = nov + dt.timedelta(days=(6 - nov.weekday()) % 7)
    off = -4 if start <= t < end else -5
    return (t + dt.timedelta(hours=off)).date().isoformat()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _dump(doc: Any) -> bytes:
    return (json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=False) + "\n").encode()


# ---------------------------------------------------------------- build (pure)
def _cheap_entry(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": e["recommendation_rank"],
        "model_family": e["model_family"],
        "vendor": e.get("vendor"),
        "recommended": bool(e["recommendation_eligible"]),
        "gate_reasons": list(e.get("gate_reasons") or []),
        "best_route": e["best_route"],
        "price_usd_per_mtok": {
            "input": e.get("best_route_min_ask_in"),
            "output": e.get("best_route_min_ask_out"),
            "basis": "best route lowest listed ask at as_of",
        },
        "health": {
            "status": e.get("best_route_health"),
            "confidence": e.get("health_confidence"),
            "reasons": list(e.get("best_route_health_reasons") or []),
        },
        "confidence": e.get("confidence"),
        "tier": e.get("tier"),
        "capability_score_100": e.get("capability_score_100"),
        "price_regime": e.get("price_regime"),
        "system_prompt_handling": e.get("best_route_system_prompt_handling"),
        "preferred_endpoint": e.get("best_route_preferred_endpoint"),
        "caveats": list(e.get("best_route_caveats") or []),
        "routes": list(e.get("routes") or []),
    }


def _frontier_entry(m: dict[str, Any]) -> dict[str, Any]:
    ev = m.get("evidence") or {}
    return {
        "rank": m["frontier_rank"],
        "model_family": m["model_family"],
        "vendor": m.get("vendor"),
        "recommended": m.get("recommendation_status") == "recommended",
        "gate_reasons": list(m.get("gate_reasons") or []),
        "best_route": m["best_route"],
        "price_usd_per_mtok": {
            "input": m.get("best_route_min_ask_in"),
            "output": m.get("best_route_min_ask_out"),
            "basis": "best route lowest listed ask at as_of",
        },
        "health": {
            "status": m.get("best_route_health"),
            "confidence": m.get("health_confidence"),
            "reasons": list(ev.get("best_route_health_reasons") or []),
        },
        "confidence": m.get("confidence"),
        "tier": m.get("tier"),
        "capability_score_100": m.get("capability_score_100"),
        "price_regime": m.get("price_policy_status"),
        "system_prompt_handling": m.get("best_route_system_prompt_handling"),
        "preferred_endpoint": m.get("best_route_preferred_endpoint"),
        "caveats": list(m.get("best_route_caveats") or []),
        "routes": list(m.get("routes") or []),
    }


def build_feed(
    top20: dict[str, Any],
    frontier: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    code_commit: str | None,
    generated_at: str,
) -> dict[str, Any]:
    """today.json document from the two list documents (pure)."""
    oldest = min(_parse(top20["generated_at"]), _parse(frontier["generated_at"]))
    picks = [m for m in frontier["models"] if m.get("recommendation_status") == "recommended"]
    snap = (top20.get("provenance") or {}).get("snapshot_sha256")
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_url": RAW_BASE + "feed/v1/schema.json",
        "generated_at": generated_at,
        "day_et": _et_day(top20["generated_at"]),
        "stale_after": _iso(oldest + dt.timedelta(hours=STALE_AFTER_HOURS)),
        "code_commit": code_commit,
        "source_repo": SOURCE_REPO,
        "snapshot_sha256": snap,
        "sources": sources,
        "notice": (
            "Read-only daily picks from IRE. Every row is a hypothesis: prices are the lowest "
            "listed ask in USD per 1M tokens at as_of (the served price can be higher), health is "
            "InferHub's public, platform-wide status, and capability is a low-confidence prior. "
            "Bring your own InferHub key; this feed never contains one. Treat the feed as stale "
            "after stale_after."
        ),
        "tiers": {
            "cheap": {
                "list": "top20",
                "as_of": top20["generated_at"],
                "snapshot_sha256": snap,
                "description": "Top 20 cheap, reliable, recent models; gated rows stay visible "
                "with their gate_reasons, recommended marks the usable ones.",
                "entries": [_cheap_entry(e) for e in top20["entries"]],
            },
            "frontier": {
                "list": "frontier",
                "as_of": frontier["generated_at"],
                "snapshot_sha256": (frontier.get("provenance") or {}).get("snapshot_sha256"),
                "description": "Recommended frontier-tier models, capability first, with the "
                "cheapest healthy route.",
                "entries": [_frontier_entry(m) for m in picks],
            },
        },
    }


def build_index(days: dict[str, bytes], today: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": today["generated_at"],
        "today": {
            "url": RAW_BASE + "feed/v1/today.json",
            "day_et": today["day_et"],
            "stale_after": today["stale_after"],
        },  # fmt: skip
        "schema": RAW_BASE + "feed/v1/schema.json",
        "days": [
            {"day_et": d, "url": f"{RAW_BASE}feed/v1/days/{d}.json", "sha256": _sha(b)}
            for d, b in sorted(days.items(), reverse=True)
        ],
    }


# ---------------------------------------------------------------- secret guard
def scan_bytes(name: str, body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="replace")
    return [f"{name}: looks like {label}" for label, rx in SECRET_PATTERNS if rx.search(text)]


def check_tree(root: str) -> list[str]:
    hits: list[str] = []
    for d, _, files in os.walk(root):
        if ".git" in d.split(os.sep):
            continue
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                hits += scan_bytes(os.path.relpath(p, root), fh.read())
    return hits


# ---------------------------------------------------------------- I/O
def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def load_sources(lists_dir: str = LISTS_DIR) -> tuple[dict[str, Any], dict[str, Any], dict]:
    t_json, f_json = (
        _read(os.path.join(lists_dir, TOP20_JSON)),
        _read(os.path.join(lists_dir, FRONTIER_JSON)),
    )
    sources = {
        "top20": {
            "file": "operational/telemetry/gravebuster/pipeline/ihub/lists/" + TOP20_CSV,
            "sha256": _sha(_read(os.path.join(lists_dir, TOP20_CSV))),
            "sidecar_sha256": _sha(t_json),
        },
        "frontier": {
            "file": "operational/telemetry/gravebuster/pipeline/ihub/lists/" + FRONTIER_JSON,
            "sha256": _sha(f_json),
        },
    }
    return json.loads(t_json), json.loads(f_json), sources


def _git_head() -> str | None:
    try:
        out = subprocess.check_output(["git", "-C", HERE, "rev-parse", "HEAD"], text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.strip() or None


def write_feed(out_root: str, code_commit: str | None, lists_dir: str = LISTS_DIR) -> dict:
    top20, frontier, sources = load_sources(lists_dir)
    today = build_feed(top20, frontier, sources, code_commit, _iso_now())
    body = _dump(today)
    base = os.path.join(out_root, "feed", "v1")
    days_dir = os.path.join(base, "days")
    os.makedirs(days_dir, exist_ok=True)
    days = {
        f[:-5]: _read(os.path.join(days_dir, f))
        for f in os.listdir(days_dir)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f)
    }
    days[today["day_et"]] = body
    files = {
        os.path.join(base, "today.json"): body,
        os.path.join(days_dir, today["day_et"] + ".json"): body,
        os.path.join(base, "index.json"): _dump(build_index(days, today)),
        os.path.join(base, "schema.json"): _read(SCHEMA_SRC),
    }
    hits = [h for p, b in files.items() for h in scan_bytes(os.path.relpath(p, out_root), b)]
    if hits:
        raise RuntimeError("secret guard refused the feed: " + "; ".join(hits))
    for p, b in files.items():
        tmp = p + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(b)
        os.replace(tmp, p)
    return today


README = """# IRE daily feed (data branch)

Machine-written by `operational/telemetry/gravebuster/pipeline/ihub/feed.py` on `main`.
No CI, no PRs: each run commits the day's files here.

- `feed/v1/today.json`: today's picks (schema `feed/v1/schema.json`)
- `feed/v1/days/YYYY-MM-DD.json`: one file per ET day
- `feed/v1/index.json`: the list of days

Stable URL: https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v1/today.json
"""


def _git(*args: str, cwd: str) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {p.stderr.strip()[-400:]}")
    return p.stdout.strip()


def publish(code_commit: str | None, workdir: str | None = None, push: bool = True) -> dict:
    """Build into a checkout of the orphan data branch, guard, commit and push."""
    tmp = workdir or tempfile.mkdtemp(prefix="ire-feed-")
    try:
        if not os.path.isdir(os.path.join(tmp, ".git")):
            _git("init", "-q", tmp, cwd=os.path.dirname(tmp) or ".")
            _git("remote", "add", "origin", REPO_URL, cwd=tmp)
        if _git("ls-remote", "--heads", "origin", BRANCH, cwd=tmp):
            _git("fetch", "-q", "--depth", "1", "origin", BRANCH, cwd=tmp)
            _git("checkout", "-q", "-B", BRANCH, "FETCH_HEAD", cwd=tmp)
        else:
            _git("checkout", "-q", "--orphan", BRANCH, cwd=tmp)
        today = write_feed(tmp, code_commit)
        with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(README)
        hits = check_tree(tmp)
        if hits:
            raise RuntimeError("secret guard refused the feed: " + "; ".join(hits))
        _git("add", "-A", cwd=tmp)
        if not _git("status", "--porcelain", cwd=tmp):
            return {"published": False, "reason": "no change", "day_et": today["day_et"]}
        _git(
            "-c", "user.name=ire-feed", "-c", "user.email=ire-feed@users.noreply.github.com",
            "commit", "-q", "-m", f"feed: IRE daily picks {today['day_et']} (ET)", cwd=tmp,
        )  # fmt: skip
        if push:
            _git("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=tmp)
        return {"published": push, "branch": BRANCH, "commit": _git("rev-parse", "HEAD", cwd=tmp),
                "day_et": today["day_et"], "url": RAW_BASE + "feed/v1/today.json"}  # fmt: skip
    finally:
        if workdir is None:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="feed", description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write feed/v1/ under --out")
    b.add_argument("--out", required=True)
    c = sub.add_parser("check", help="secret guard over a directory")
    c.add_argument("dir")
    p = sub.add_parser("publish", help="build, guard, commit and push to the data branch")
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--workdir", default=None)
    for s in (b, p):
        s.add_argument("--code-commit", default=None)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            hits = check_tree(a.dir)
            print(json.dumps({"ok": not hits, "hits": hits}))
            return 1 if hits else 0
        cc = a.code_commit or _git_head()
        if a.cmd == "build":
            t = write_feed(a.out, cc)
            print(
                json.dumps({"out": a.out, "day_et": t["day_et"], "stale_after": t["stale_after"]})
            )
            return 0
        print(json.dumps(publish(cc, a.workdir, push=not a.no_push)))
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
