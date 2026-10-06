"""Daily read-only recommendation feed (IRE #75), open-weight models only.

Turns the two committed lists (cheap Top 20 and frontier) into a small static, versioned JSON
feed that agents, dependent repos and the design-bakery page can fetch with one GET and no key:

    feed/v2/today.json             today's picks: cheap + strongest_open tiers, best route, price
    feed/v2/days/YYYY-MM-DD.json   the same document for each ET day (append-only history)
    feed/v2/index.json             list of days with sha256 and URL
    feed/v2/schema.json            JSON Schema for today.json and days/*.json
    feed/v1/...                    the same open-weight data in the old v1 shape (deprecated)

The two text tiers (cheap, strongest_open) carry open-weight model families only.
``model_licences.v1.json`` maps each family to its licence and weights URL; a family that isn't
listed there with open_weight true is left out, so an unknown family never reaches those tiers.
The optional utility tier (image and multimodal families, from ``utility.py``) records the
open-weight verdict per entry instead: a family with an unverified licence is listed with
``open_weight`` null and ``recommended`` false, and closed families are excluded entirely. The
lists in ``lists/`` are not changed by this module.

It is published to the orphan branch ``data/ire-feed`` (no CI, no PRs, no noise on ``main``),
the same pattern as ``data/inferhub-price-snapshots``. Stable URL:
https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v2/today.json

Commands:
    feed.py build --out DIR      write feed/v2/ + feed/v1/ under DIR (keeps existing days/ and index)
    feed.py check DIR            guards: fail on credentials, closed models or price-comparison keys
    feed.py publish              build into a checkout of data/ire-feed, check, commit, push

The feed only carries public list data. ``check`` runs before every publish and refuses to push
anything key-like (API keys, bearer tokens, Authorization / x-api-key headers, GitHub tokens),
any closed model family (GPT, Claude, Gemini, Grok, Muse Spark and their vendors), any entry
without a verified open-weight licence, and any official-price or discount field.
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

try:  # package import on the host; file import (tests, CLI) falls back to a sibling load
    from .licences import CLOSED_FAMILY_RX, LICENCES_SRC, load_licences, open_licence
except ImportError:  # pragma: no cover
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "ihub_licences", os.path.join(os.path.dirname(os.path.abspath(__file__)), "licences.py")
    )
    assert _spec and _spec.loader
    _licences = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_licences)
    CLOSED_FAMILY_RX = _licences.CLOSED_FAMILY_RX
    LICENCES_SRC = _licences.LICENCES_SRC
    load_licences = _licences.load_licences
    open_licence = _licences.open_licence

HERE = os.path.dirname(os.path.abspath(__file__))
LISTS_DIR = os.path.join(HERE, "lists")
SCHEMA_SRC = os.path.join(HERE, "schemas", "feed.v1.schema.json")
SCHEMA_V2_SRC = os.path.join(HERE, "schemas", "feed.v2.schema.json")
SCHEMA_VERSION = "ire-feed/v1"
SCHEMA_VERSION_V2 = "ire-feed/v2"
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
UTILITY_JSON = "research_model_utility_recommendations.json"
UTILITY_CSV = "research_model_utility_recommendations.csv"

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


# Closed (non-open-weight) model families and their vendors, matched by CLOSED_FAMILY_RX
# (licences.py). The public feed must never name one of these in a model, vendor or route field.
# Price comparisons the public feed must not carry (official list price, discount vs official).
FORBIDDEN_KEY_RX = re.compile(r"official|discount", re.I)


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
def _licence_field(rec: dict[str, Any]) -> dict[str, Any]:
    return {"name": rec["licence"], "url": rec["licence_url"], "weights_url": rec["weights_url"]}


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


NOTICE = (
    "Read-only daily picks from IRE. The cheap and strongest_open tiers are open-weight models "
    "only; every entry there has open_weight true and names its licence and where the weights are "
    "published. The utility tier (image and multimodal models) records the open-weight verdict per "
    "entry: open_weight true when a published licence and weights were verified, open_weight null "
    "when the family is listed but its licence is unverified (such a row is never recommended). No "
    "closed-weight family appears in any tier. Prices are the lowest listed ask in USD per 1M tokens "
    "at as_of (the served price can be higher), health is InferHub's public, platform-wide status, "
    "and capability is a low-confidence prior. Bring your own InferHub key; this feed never contains "
    "one. Treat the feed as stale after stale_after."
)


def _utility_entry(e: dict[str, Any]) -> dict[str, Any]:
    """A utility-tier entry: keeps the open_weight verdict (true or null) and licence if verified."""
    ow = e.get("open_weight")
    caveats = []
    if ow is not True:
        caveats.append("licence unverified: no published weights were checked for this family")
    return {
        "rank": e["recommendation_rank"],
        "model_family": e["model_family"],
        "vendor": e.get("vendor"),
        "utility_kind": e.get("utility_kind"),
        "recommended": bool(e["recommended"]),
        "gate_reasons": list(e.get("gate_reasons") or []),
        "best_route": e["best_route"],
        "price_usd_per_mtok": {
            "input": e.get("best_route_min_ask_in"),
            "output": e.get("best_route_min_ask_out"),
            "basis": "best route lowest listed ask at as_of",
        },
        "health": {
            "status": e.get("best_route_health"),
            "confidence": None,
            "reasons": list(e.get("best_route_health_reasons") or []),
        },
        "routes": list(e.get("routes") or []),
        "open_weight": ow,
        "licence": e.get("licence"),
        "caveats": caveats,
    }


def _open_only(entries: list[dict[str, Any]], licences: dict[str, dict[str, Any]]) -> list[dict]:
    """Keep verified open-weight entries, re-rank them 1..n and attach the licence."""
    out = []
    for e in entries:
        rec = open_licence(e["model_family"], licences)
        if rec is None:
            continue
        out.append({**e, "rank": len(out) + 1, "open_weight": True, "licence": _licence_field(rec)})
    return out


def build_feed(
    top20: dict[str, Any],
    frontier: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    code_commit: str | None,
    generated_at: str,
    licences: dict[str, dict[str, Any]] | None = None,
    utility: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """feed/v2 today.json from the list documents (pure).

    The two text tiers carry verified open-weight families only. The optional utility tier
    records the open-weight verdict per entry, so a licence-unverified utility family can be
    listed (with open_weight null and recommended false) without weakening the text tiers.
    """
    lic = load_licences() if licences is None else licences
    oldest = min(_parse(top20["generated_at"]), _parse(frontier["generated_at"]))
    picks = [m for m in frontier["models"] if m.get("recommendation_status") == "recommended"]
    snap = (top20.get("provenance") or {}).get("snapshot_sha256")
    tiers: dict[str, Any] = {
        "cheap": {
            "list": "top20",
            "as_of": top20["generated_at"],
            "snapshot_sha256": snap,
            "description": "Open-weight models from IRE's cheap Top 20, ranked; gated rows "
            "stay visible with their gate_reasons, recommended marks the usable ones.",
            "entries": _open_only([_cheap_entry(e) for e in top20["entries"]], lic),
        },
        "strongest_open": {
            "list": "frontier",
            "as_of": frontier["generated_at"],
            "snapshot_sha256": (frontier.get("provenance") or {}).get("snapshot_sha256"),
            "description": "The strongest recommended open-weight models, capability first, "
            "each with its cheapest healthy route.",
            "entries": _open_only([_frontier_entry(m) for m in picks], lic),
        },
    }
    if utility is not None:
        oldest = min(oldest, _parse(utility["generated_at"]))
        tiers["utility"] = {
            "list": "utility",
            "as_of": utility["generated_at"],
            "snapshot_sha256": (utility.get("provenance") or {}).get("snapshot_sha256"),
            "description": "Image-generation and multimodal families, ranked on availability and "
            "price. open_weight is the verdict for the family: true (verified licence and weights) "
            "or null (listed, licence unverified, never recommended). No closed family is listed.",
            "entries": [_utility_entry(e) for e in utility["entries"]],
        }
    return {
        "schema_version": SCHEMA_VERSION_V2,
        "schema_url": RAW_BASE + "feed/v2/schema.json",
        "generated_at": generated_at,
        "day_et": _et_day(top20["generated_at"]),
        "stale_after": _iso(oldest + dt.timedelta(hours=STALE_AFTER_HOURS)),
        "code_commit": code_commit,
        "source_repo": SOURCE_REPO,
        "snapshot_sha256": snap,
        "sources": sources,
        "open_weight_only": True,
        "licences_url": SOURCE_REPO
        + "/blob/main/operational/telemetry/gravebuster/pipeline/ihub/model_licences.v1.json",
        "notice": NOTICE,
        "tiers": tiers,
    }


def to_v1(doc: dict[str, Any]) -> dict[str, Any]:
    """The same open-weight data in the deprecated v1 shape (tiers cheap + frontier)."""
    tiers = doc["tiers"]
    return {
        **{k: v for k, v in doc.items() if k != "tiers"},
        "schema_version": SCHEMA_VERSION,
        "schema_url": RAW_BASE + "feed/v1/schema.json",
        "deprecated": "Use feed/v2. In v1 the frontier tier now holds the strongest open-weight "
        "models; closed models are no longer published.",
        "superseded_by": RAW_BASE + "feed/v2/today.json",
        "tiers": {"cheap": tiers["cheap"], "frontier": tiers["strongest_open"]},
    }


# ---------------------------------------------------------------- open-weight guard
def _walk_keys(node: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if FORBIDDEN_KEY_RX.search(str(k)):
                found.append(f"{path}/{k}")
            found += _walk_keys(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += _walk_keys(v, f"{path}/{i}")
    return found


def open_weight_problems(doc: Any, licences: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Reasons the two text tiers are not fit to publish (empty list = fine).

    The utility tier is checked by ``utility_problems``: it may carry an unverified family, which
    this function would reject, so it is skipped here."""
    lic = load_licences() if licences is None else licences
    probs = [f"forbidden price-comparison field {p}" for p in _walk_keys(doc)]
    tiers = doc.get("tiers") if isinstance(doc, dict) else None
    if not isinstance(tiers, dict):
        return probs
    for tname, tier in tiers.items():
        if tname == "utility":
            continue
        for e in (tier or {}).get("entries") or []:
            fam = str(e.get("model_family"))
            where = f"tiers.{tname} {fam!r}"
            names = [fam, str(e.get("vendor") or ""), str(e.get("best_route") or "")]
            names += [str(r) for r in e.get("routes") or []]
            if any(CLOSED_FAMILY_RX.search(n) for n in names):
                probs.append(f"{where}: closed model family, vendor or route")
            if e.get("open_weight") is not True:
                probs.append(f"{where}: open_weight is not true")
            if open_licence(fam, lic) is None:
                probs.append(f"{where}: no verified open-weight licence in model_licences.v1.json")
    return probs


def utility_problems(doc: Any, licences: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Reasons the optional utility tier is not fit to publish (empty list = fine).

    The utility tier may list a licence-unverified family, but only with ``open_weight`` null,
    ``recommended`` false, the ``open_weight_unverified`` gate and no licence record. A closed
    family or an entry claiming verification without one is refused."""
    lic = load_licences() if licences is None else licences
    tiers = doc.get("tiers") if isinstance(doc, dict) else None
    if not isinstance(tiers, dict):
        return []
    tier = tiers.get("utility")
    if tier is None:
        return []
    probs: list[str] = []
    for e in (tier or {}).get("entries") or []:
        fam = str(e.get("model_family"))
        where = f"tiers.utility {fam!r}"
        names = [fam, str(e.get("vendor") or ""), str(e.get("best_route") or "")]
        names += [str(r) for r in e.get("routes") or []]
        if any(CLOSED_FAMILY_RX.search(n) for n in names):
            probs.append(f"{where}: closed model family, vendor or route")
        ow = e.get("open_weight")
        if ow is False:
            probs.append(f"{where}: closed-weight (open_weight false) must not be listed")
        elif ow is None:
            if e.get("recommended") is not False:
                probs.append(f"{where}: unverified licence must have recommended false")
            if "open_weight_unverified" not in (e.get("gate_reasons") or []):
                probs.append(f"{where}: unverified licence missing the open_weight_unverified gate")
            if e.get("licence"):
                probs.append(f"{where}: unverified licence must not carry a licence record")
        elif ow is True:
            if open_licence(fam, lic) is None:
                probs.append(f"{where}: open_weight true without a verified licence")
        else:
            probs.append(f"{where}: open_weight must be true, false or null")
        if e.get("recommended") is True and ow is not True:
            probs.append(f"{where}: recommended true without a verified open-weight licence")
    return probs


def build_index(days: dict[str, bytes], today: dict[str, Any], version: str = "v2") -> dict:
    base = f"{RAW_BASE}feed/{version}/"
    return {
        "schema_version": today["schema_version"],
        "generated_at": today["generated_at"],
        "today": {
            "url": base + "today.json",
            "day_et": today["day_et"],
            "stale_after": today["stale_after"],
        },  # fmt: skip
        "schema": base + "schema.json",
        "days": [
            {"day_et": d, "url": f"{base}days/{d}.json", "sha256": _sha(b)}
            for d, b in sorted(days.items(), reverse=True)
        ],
    }


# ---------------------------------------------------------------- secret guard
def scan_bytes(name: str, body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="replace")
    return [f"{name}: looks like {label}" for label, rx in SECRET_PATTERNS if rx.search(text)]


def _is_feed_doc(rel: str) -> bool:
    parts = rel.replace(os.sep, "/").split("/")
    return (
        len(parts) >= 3
        and parts[0] == "feed"
        and parts[-1].endswith(".json")
        and (parts[-1] == "today.json" or parts[-2] == "days")
    )


def check_tree(root: str) -> list[str]:
    """Secret guard over every file, plus the open-weight guard over today/days documents."""
    hits: list[str] = []
    for d, _, files in os.walk(root):
        if ".git" in d.split(os.sep):
            continue
        for f in files:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root)
            with open(p, "rb") as fh:
                body = fh.read()
            hits += scan_bytes(rel, body)
            if _is_feed_doc(rel):
                try:
                    doc = json.loads(body)
                except ValueError:
                    hits.append(f"{rel}: not valid JSON")
                    continue
                hits += [f"{rel}: {p_}" for p_ in open_weight_problems(doc)]
                hits += [f"{rel}: {p_}" for p_ in utility_problems(doc)]
    return hits


# ---------------------------------------------------------------- I/O
def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def load_sources(
    lists_dir: str = LISTS_DIR,
) -> tuple[dict[str, Any], dict[str, Any], dict | None, dict]:
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
        "licences": {
            "file": "operational/telemetry/gravebuster/pipeline/ihub/model_licences.v1.json",
            "sha256": _sha(_read(LICENCES_SRC)),
        },
    }
    # The utility list is optional: a checkout without it still builds the two text tiers.
    utility: dict | None = None
    u_json_path = os.path.join(lists_dir, UTILITY_JSON)
    if os.path.exists(u_json_path):
        u_json = _read(u_json_path)
        utility = json.loads(u_json)
        sources["utility"] = {
            "file": "operational/telemetry/gravebuster/pipeline/ihub/lists/" + UTILITY_CSV,
            "sha256": _sha(_read(os.path.join(lists_dir, UTILITY_CSV))),
            "sidecar_sha256": _sha(u_json),
        }
    return json.loads(t_json), json.loads(f_json), utility, sources


def _git_head() -> str | None:
    try:
        out = subprocess.check_output(["git", "-C", HERE, "rev-parse", "HEAD"], text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.strip() or None


def _version_files(out_root: str, version: str, doc: dict, schema_src: str) -> dict[str, bytes]:
    body = _dump(doc)
    base = os.path.join(out_root, "feed", version)
    days_dir = os.path.join(base, "days")
    os.makedirs(days_dir, exist_ok=True)
    days = {
        f[:-5]: _read(os.path.join(days_dir, f))
        for f in os.listdir(days_dir)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f)
    }
    days[doc["day_et"]] = body
    index = build_index(days, doc, version)
    return {
        os.path.join(base, "today.json"): body,
        os.path.join(days_dir, doc["day_et"] + ".json"): body,
        os.path.join(base, "index.json"): _dump(index),
        os.path.join(base, "schema.json"): _read(schema_src),
    }


def write_feed(out_root: str, code_commit: str | None, lists_dir: str = LISTS_DIR) -> dict:
    """Write feed/v2 (and the deprecated v1 mirror) under ``out_root``; returns the v2 doc."""
    top20, frontier, utility, sources = load_sources(lists_dir)
    today = build_feed(top20, frontier, sources, code_commit, _iso_now(), utility=utility)
    v1 = to_v1(today)
    problems = (
        open_weight_problems(today)
        + utility_problems(today)
        + open_weight_problems(v1)
        + utility_problems(v1)
    )
    if problems:
        raise RuntimeError("open-weight guard refused the feed: " + "; ".join(problems))
    files = _version_files(out_root, "v2", today, SCHEMA_V2_SRC)
    files.update(_version_files(out_root, "v1", v1, SCHEMA_SRC))
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

Open-weight models only: each entry names its licence and where the weights are published.

- `feed/v2/today.json`: today's picks (schema `feed/v2/schema.json`), tiers `cheap` and `strongest_open`
- `feed/v2/days/YYYY-MM-DD.json`: one file per ET day
- `feed/v2/index.json`: the list of days
- `feed/v1/`: the same data in the old shape (deprecated)

Stable URL: https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v2/today.json
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
            raise RuntimeError("feed guard refused the feed: " + "; ".join(hits))
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
                "day_et": today["day_et"], "url": RAW_BASE + "feed/v2/today.json"}  # fmt: skip
    finally:
        if workdir is None:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="feed", description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write feed/v2/ (and the v1 mirror) under --out")
    b.add_argument("--out", required=True)
    c = sub.add_parser("check", help="secret + open-weight guard over a directory")
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
