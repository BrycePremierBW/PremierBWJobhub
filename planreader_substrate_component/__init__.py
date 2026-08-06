"""PlanReader substrate box editor custom Streamlit component.

A static HTML/JS component (no bundler) that renders an elevation image and
lets the user drag-and-drop rectangles ("boxes") onto it. Each box can carry a
label, a substrate, a progress percentage and an optional m² quantity. Box
coordinates are percentages of the image (0-100), matching the existing zone
format used by the elevation progress tracker and overlay rendering.
"""
import base64
from pathlib import Path

import streamlit.components.v1 as components

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

_substrate_box_component = components.declare_component(
    "planreader_substrate_box_editor",
    path=str(FRONTEND_DIR),
)


def substrate_box_editor(
    image_bytes,
    boxes=None,
    substrates=None,
    calibration=None,
    revision=0,
    key=None,
    height=860,
):
    """Render an elevation image with editable substrate boxes.

    ``image_bytes`` is the PNG bytes of the elevation image. ``boxes`` is the
    current list of box dicts (x/y/w/h in percent, label, substrate, progress,
    qty_m2, manual_m2). ``substrates`` lists the substrate choices for the
    dropdown. ``calibration`` is the drawing's scale calibration (a reference
    line in percent coordinates plus its real-world length in metres) used to
    auto-measure each box's m²; pass ``None`` when the drawing has no scale.
    ``revision`` is bumped whenever Python changes the boxes so the component
    adopts the new set.

    Returns ``{boxes: [...], calibration: {...} | null}`` or None if untouched.
    """
    image_data_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    return _substrate_box_component(
        image=image_data_uri,
        boxes=list(boxes or []),
        substrates=list(substrates or []),
        calibration=calibration or None,
        revision=int(revision or 0),
        default=None,
        key=key,
        height=height,
    )
