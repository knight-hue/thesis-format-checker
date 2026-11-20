"""Normalize deterministic observed fields before the guard checks."""
from __future__ import annotations

import re

BARE_FIELD_RE = re.compile(r"^(?:word:|pdf:|assumed:)?[a-z][a-z0-9_]*$")


def sanitize_observed(parsed):
    """Return (sanitized parsed, issues).

    A bare field name such as font_size_pt is not an observation. Drop it,
    move it to unknown, force can_judge=false and let the guard rewrite.
    """
    parsed = dict(parsed or {})
    observed = list(parsed.get("observed") or [])
    invalid = [str(item) for item in observed if BARE_FIELD_RE.fullmatch(str(item).strip())]
    if not invalid:
        return parsed, []
    valid = [item for item in observed if str(item) not in invalid]
    unknown = list(parsed.get("unknown") or [])
    for item in invalid:
        marker = f"{item}_value_missing"
        if marker not in unknown:
            unknown.append(marker)
    if "observed_value_missing" not in unknown:
        unknown.append("observed_value_missing")
    parsed["observed"] = valid
    parsed["unknown"] = unknown
    parsed["can_judge"] = False
    return parsed, ["observed_missing_value"]
