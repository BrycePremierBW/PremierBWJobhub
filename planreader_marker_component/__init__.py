"""PlanReader marker editor custom Streamlit component.

A tiny static component (plain HTML/JS, no bundler) that renders a plan page
and lets the user tap rooms to place markers. Marker coordinates, labels and
dimensions are returned to Python as JSON on every edit, so the app can store
them as corrections and feed them back into room detection.
"""
import base64
from pathlib import Path

import streamlit.components.v1 as components

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

_plan_marker_component = components.declare_component(
    "planreader_marker_editor",
    path=str(FRONTEND_DIR),
)


def plan_marker_editor(
    image_bytes,
    markers=None,
    hints=None,
    key=None,
    height=780,
):
    """Render a plan page with editable room markers.

    ``image_bytes`` is the PNG bytes of the rendered plan page. ``markers`` is a
    list of marker dicts (label/x/y/dim1_m/dim2_m) already saved for this page.
    ``hints`` lists detected rooms so typing a matching label pre-fills sizes.

    Returns the current markers list (or an empty list if untouched).
    """
    image_data_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    return _plan_marker_component(
        image=image_data_uri,
        markers=list(markers or []),
        hints=list(hints or []),
        default=[],
        key=key,
        height=height,
    )
