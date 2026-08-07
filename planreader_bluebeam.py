"""Bluebeam-style measurement helpers for PlanReader.

Mirrors how Bluebeam Revu keeps drawings accurate:

* **Scale as a ratio** — instead of only a drawn reference line, the page
  scale can be given directly as ``1:100`` / ``1:200`` / ``1 in 50`` etc.
  (the Bluebeam "preset / custom scale" path), and the ratio is converted to
  the metres-per-PDF-point and metres-per-pixel used by the measurement math.
* **Recalculate** — a scale change flows straight into every drawn box, like
  Bluebeam's Recalculate.
* **Consistency checks** — flags boxes whose drawn measurement disagrees with
  the typed quantity, so mis-calibration is caught early.
* **Story / level inference** — reads level markers out of plan titles and
  text so external side areas and the 3D render can be split per storey.

The module is standalone (no Streamlit, no JobHub guards) so it is safe to
import from anywhere in the PlanReader toolchain.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

# Architectural drawing-unit assumptions. One PDF point is 25.4/72 mm.
PT_TO_MM = 25.4 / 72.0
PT_TO_M = PT_TO_MM / 1000.0

COMMON_SCALE_RATIOS = [1, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]

# Floor level markers used to infer storeys, in priority order.
LEVEL_MARKERS = {
    0: ["ground floor", "ground level", "ground", "level 0", "floor 0"],
    1: ["first floor", "1st floor", "level 1", "floor 1", "upper floor"],
    2: ["second floor", "2nd floor", "level 2", "floor 2"],
    3: ["third floor", "3rd floor", "level 3", "floor 3"],
    4: ["fourth floor", "4th floor", "level 4", "floor 4"],
    5: ["fifth floor", "5th floor", "level 5", "floor 5"],
    6: ["sixth floor", "6th floor", "level 6", "floor 6"],
}


def parse_scale_ratio(text: Any) -> Optional[float]:
    """Parse an architectural scale into its real-units-per-drawing-unit ratio.

    Accepts ``1:100``, ``1 : 200``, ``SCALE 1:100 @ A3``, ``1 in 50``,
    ``1/50`` and ``1:100 (mm)``. Returns the right-hand number (e.g. ``100``)
    or None when the text is not a scale.
    """
    t = str(text or "").strip()
    if not t:
        return None
    low = t.lower()
    if any(k in low for k in ("bar scale", "scale bar")):
        return None
    patterns = [
        r"\b1\s*[:/]\s*([0-9]{1,6})(?:\b|@|\(|mm|m\b)",
        r"\b1\s+in\s+([0-9]{1,6})\b",
        r"\b1\s+to\s+([0-9]{1,6})\b",
        r"\b1\s*=\s*([0-9]{1,6})\b",
    ]
    for pat in patterns:
        m = re.search(pat, low)
        if m:
            try:
                ratio = int(m.group(1))
            except ValueError:
                continue
            if ratio > 0:
                return float(ratio)
    return None


def scale_ratio_to_m_per_pt(ratio: Any) -> Optional[float]:
    """Convert a ``1:N`` scale to metres per PDF point.

    A page is plotted at ``1:N``, so each PDF point on paper is ``N`` real
    points: ``N * 25.4/72 mm``. Architectural and site scales are both handled
    by the same conversion because the paper length is fixed.
    """
    r = _to_float(ratio)
    if r is None or r <= 0:
        return None
    return round(r * PT_TO_M, 8)


def scale_ratio_to_m_per_px(ratio: Any, dpi: Any = 150) -> Optional[float]:
    """Convert a ``1:N`` scale to metres per rendered pixel at a DPI."""
    r = _to_float(ratio)
    d = _to_float(dpi)
    if r is None or r <= 0 or d is None or d <= 0:
        return None
    return round(r * 25.4 / 1000.0 / d, 8)


def nearest_scale_ratio(m_per_pt: Any) -> Optional[float]:
    """Snap a detected metres-per-point scale to the nearest common ratio.

    Bluebeam-style: auto-detected scales get labelled with the closest preset
    (1:50, 1:100, 1:200, ...) so the estimator can sanity-check the result.
    """
    mpt = _to_float(m_per_pt)
    if mpt is None or mpt <= 0:
        return None
    best, best_dist = None, float("inf")
    for ratio in COMMON_SCALE_RATIOS:
        expected = scale_ratio_to_m_per_pt(ratio)
        if expected is None:
            continue
        dist = abs(math.log(mpt) - math.log(expected))
        if dist < best_dist:
            best_dist = dist
            best = ratio
    if best is None:
        return None
    return float(best)


def scale_ratio_label(ratio: Any) -> str:
    r = _to_float(ratio)
    if r is None or r <= 0:
        return ""
    return "1:%g" % r


def manual_calibration_from_scale(
    ratio: Any,
    dpi: Any,
    img_w: Any,
    img_h: Any,
) -> Optional[Dict[str, Any]]:
    """Build a PlanReader calibration dict from a ``1:N`` scale ratio.

    The calibration spans the full image width so every drawn box inherits the
    scale, mirroring how a preset page scale applies to all markups.
    """
    w = _to_float(img_w)
    h = _to_float(img_h)
    mpp = scale_ratio_to_m_per_px(ratio, dpi)
    if mpp is None or w is None or w <= 0 or h is None or h <= 0:
        return None
    return {
        "x1": 0.0,
        "y1": 0.0,
        "x2": 100.0,
        "y2": 0.0,
        "len_m": round(w * mpp, 4),
    }


def detect_scale_from_text(text: Any) -> Optional[Dict[str, Any]]:
    """Find a scale marker in title-block/sheet text (e.g. ``SCALE 1:100``)."""
    lines = [re.sub(r"\s+", " ", str(l)).strip() for l in str(text or "").splitlines()]
    for line in lines:
        if "scale" not in line.lower():
            continue
        ratio = parse_scale_ratio(line)
        if ratio is None:
            continue
        return {
            "ratio": ratio,
            "label": scale_ratio_label(ratio),
            "source": "title-block",
            "text": line[:160],
        }
    return None


def extract_story_levels(text: Any) -> List[int]:
    """Extract the floor levels referenced by a piece of plan text.

    Returns sorted, unique level numbers (ground floor = 0).
    """
    low = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    found: List[int] = []
    for level, markers in LEVEL_MARKERS.items():
        if any(marker in low for marker in markers):
            found.append(level)
    return sorted(set(found))


def infer_stories_from_pages(pages: Any, all_text: Any = None) -> int:
    """Estimate the number of storeys from plan pages and their text.

    Combines explicit ``LEVEL N`` / ``FIRST FLOOR`` markers found in page
    titles and the document text; floors above a single ground level push the
    count up. Returns at least 1.
    """
    levels: set = set()
    for page in pages or []:
        levels.update(extract_story_levels(page.get("title")))
    levels.update(extract_story_levels(all_text))
    if not levels:
        return 1
    return max(levels) + 1


def calibration_consistency_warnings(
    boxes: Any,
    mpp: Any,
    img_w: Any,
    img_h: Any,
    tolerance: float = 1.5,
) -> List[str]:
    """Flag drawn boxes whose measured area disagrees with the typed quantity.

    Uses the same metres-per-pixel math as the take-off, so this catches a
    mis-set page scale before the quantities are exported.
    """
    warnings: List[str] = []
    for b in boxes or []:
        manual = _to_float(b.get("manual_m2")) or 0.0
        if manual <= 0:
            continue
        measured = _measured_box_m2(b, mpp, img_w, img_h)
        if measured <= 0:
            continue
        ratio = measured / manual
        if ratio < 1.0 / tolerance or ratio > tolerance:
            label = str(b.get("label") or b.get("substrate") or "Box")
            warnings.append(
                f"'{label}': drawn measurement {measured:g} m² disagrees with "
                f"typed {manual:g} m² — check the page scale."
            )
    return warnings


def side_area_summary(
    width_m: Any,
    depth_m: Any,
    stories: Any = 1,
    wall_height_m: Any = 2.7,
    openings_m2: Any = 0.0,
) -> Dict[str, float]:
    """Per-storey external side-area breakdown (Bluebeam story take-off style)."""
    w = max(_to_float(width_m) or 0.0, 0.0)
    d = max(_to_float(depth_m) or 0.0, 0.0)
    h = max(_to_float(wall_height_m) or 0.0, 0.0)
    n = max(int(_to_float(stories) or 1.0), 1)
    openings = max(_to_float(openings_m2) or 0.0, 0.0)
    per_story = round(2 * (w + d) * h, 2)
    gross = round(per_story * n, 2)
    return {
        "stories": n,
        "wall_height_m": round(h, 2),
        "per_story_m2": per_story,
        "gross_walls_m2": gross,
        "openings_m2": round(openings, 2),
        "net_walls_m2": round(max(gross - openings, 0.0), 2),
        "volume_m3": round(w * d * h * n, 2),
    }


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _measured_box_m2(box: Dict[str, Any], mpp: Any, img_w: Any, img_h: Any) -> float:
    w = _to_float(box.get("w"))
    h = _to_float(box.get("h"))
    iw = _to_float(img_w)
    ih = _to_float(img_h)
    m = _to_float(mpp)
    if m is None or not w or w <= 0 or not h or h <= 0 or not iw or iw <= 0 or not ih or ih <= 0:
        return 0.0
    return (w / 100.0 * iw) * (h / 100.0 * ih) * m * m
