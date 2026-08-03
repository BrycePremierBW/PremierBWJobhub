"""Smoke tests for the setup defaults route guard."""

from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_setup_defaults_route_guard_source_parses():
    ast.parse(read("jobhub/setup_defaults_route_guard.py"), filename="jobhub/setup_defaults_route_guard.py")


def test_setup_defaults_route_guard_is_installed_before_setup_menu():
    source = read("jobhub/__init__.py")
    assert "from .setup_defaults_route_guard import install_setup_defaults_route_guard" in source
    assert "install_setup_defaults_route_guard()" in source
    assert source.index("install_setup_defaults_route_guard()") < source.index("install_setup_defaults_guard()")


def test_setup_defaults_route_is_protected_from_dashboard_reset():
    source = read("jobhub/setup_defaults_route_guard.py")
    required = [
        "JobHub Setup / Edit Defaults",
        "_install_session_state_reset_guard",
        "RESET_SAFE_VALUES",
        "main_menu",
        "management_menu",
        "Dashboard",
        "render_setup_defaults_page()",
        "st.stop()",
    ]
    for marker in required:
        assert marker in source
