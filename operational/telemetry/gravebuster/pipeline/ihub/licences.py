"""Open-weight licence check shared by the two list builders and the public feed (IRE #94).

``model_licences.v1.json`` maps each model family to its licence, weights URL and an
``open_weight`` verdict. A family counts as verified open-weight only when the map says
``open_weight: true`` *and* the record carries a licence, a licence URL and a weights URL,
and neither the family name nor its vendor names a closed vendor. Everything else — missing
from the map, ``false``, ``null``, or incomplete — is not verified and must not be shown as a
recommendation.

The public feed already drops those families. The list builders apply the same check so the
committed lists, and the launcher tables that read them, stop carrying closed models as
eligible.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
LICENCES_SRC = os.path.join(HERE, "model_licences.v1.json")

# Closed families, matched case-insensitively on the family name or the vendor name.
CLOSED_FAMILY_RX = re.compile(
    r"\b(?:gpt|openai|claude|anthropic|gemini|google|grok|xai|muse[ -]?spark|meta)\b", re.I
)


def load_licences(path: str = LICENCES_SRC) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return dict(json.load(fh)["families"])


def open_licence(family: str, licences: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The licence record when ``family`` is verified open-weight, else None (excluded)."""
    rec = licences.get(family)
    if not rec or rec.get("open_weight") is not True:
        return None
    if not rec.get("licence") or not rec.get("licence_url") or not rec.get("weights_url"):
        return None
    if CLOSED_FAMILY_RX.search(family) or CLOSED_FAMILY_RX.search(str(rec.get("vendor") or "")):
        return None
    return rec
