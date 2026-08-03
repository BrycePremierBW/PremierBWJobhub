"""Smoke tests for progress baseline unlock panel."""

from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_progress_baseline_unlock_guard_source_parses():
    ast.parse(read("jobhub/progress_baseline_unlock_guard.py"), filename="jobhub/progress_baseline_unlock_guard.py")


def test_progress_baseline_unlock_guard_is_installed():
    source = read("jobhub/__init__.py")
    assert "from .progress_baseline_unlock_guard import install_progress_baseline_unlock_guard" in source
    assert "install_progress_baseline_unlock_guard()" in source


def test_progress_baseline_unlock_panel_has_required_controls():
    source = read("jobhub/progress_baseline_unlock_guard.py")
    required = [
        "Baseline tools - unlock / clear",
        "estimate_baselines",
        "COALESCE(eb.active,1)=1",
        "Clear / unlock locked baseline",
        "I understand this unlocks the baseline for this job",
        "Also unlink this estimate from the progress tracker",
        "UPDATE estimate_baselines SET active=0",
        "progress_tracker_job",
    ]
    for marker in required:
        assert marker in source
