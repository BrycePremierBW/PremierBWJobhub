"""Drawing/specification revision comparison and variation signals."""

from __future__ import annotations

from difflib import SequenceMatcher, unified_diff
import re
from typing import Any


VARIATION_TERMS = {
    "additional": 3,
    "add": 2,
    "extra": 3,
    "new": 1,
    "change": 2,
    "changed": 2,
    "replace": 2,
    "repaint": 3,
    "upgrade": 2,
    "coating": 1,
    "colour": 1,
    "finish": 1,
    "substrate": 1,
    "out of scope": 4,
}


def _normalise_lines(value: Any) -> list[str]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return [re.sub(r"\s+", " ", line).strip() for line in text.split("\n") if line.strip()]


def compare_revisions(
    previous_text: Any,
    current_text: Any,
    *,
    previous_label: str = "previous",
    current_label: str = "current",
) -> dict[str, Any]:
    """Compare extracted drawing/spec text and flag likely scope changes."""
    previous = _normalise_lines(previous_text)
    current = _normalise_lines(current_text)
    if not previous and not current:
        raise ValueError("At least one revision must contain text.")

    matcher = SequenceMatcher(a=previous, b=current, autojunk=False)
    added: list[str] = []
    removed: list[str] = []
    changed_groups = 0
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed_groups += 1
        if tag in {"insert", "replace"}:
            added.extend(current[new_start:new_end])
        if tag in {"delete", "replace"}:
            removed.extend(previous[old_start:old_end])

    scored_terms: dict[str, int] = {}
    searchable = "\n".join(added).casefold()
    for term, weight in VARIATION_TERMS.items():
        occurrences = searchable.count(term)
        if occurrences:
            scored_terms[term] = occurrences * weight
    risk_score = min(100, sum(scored_terms.values()) * 10)
    likely_variation = risk_score >= 20 or len(added) >= 3

    diff = "\n".join(
        unified_diff(
            previous,
            current,
            fromfile=previous_label,
            tofile=current_label,
            lineterm="",
        )
    )
    return {
        "previous_line_count": len(previous),
        "current_line_count": len(current),
        "similarity_percent": round(matcher.ratio() * 100, 1),
        "changed_groups": changed_groups,
        "added_lines": added,
        "removed_lines": removed,
        "variation_terms": scored_terms,
        "variation_risk_score": risk_score,
        "likely_variation": likely_variation,
        "diff": diff,
    }
