"""Visible baseline unlock panel for the Job Progress Tracker."""

from __future__ import annotations

import importlib
import sys
from typing import Any


PATCH_KEY = "_pb_progress_baseline_unlock_guard"
SELECTBOX_KEY = "progress_tracker_job"


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _df_query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    fn = _app_attr("df_query") or _app_attr("safe_df_query")
    if not callable(fn):
        raise RuntimeError("JobHub database query helper is not available.")
    return fn(sql, params)


def _execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    fn = _app_attr("execute")
    if not callable(fn):
        raise RuntimeError("JobHub database execute helper is not available.")
    fn(sql, params)


def _notify(kind: str, message: str) -> None:
    st = _st()
    fn = _app_attr(f"pb_{kind}") or (getattr(st, kind, None) if st is not None else None)
    if callable(fn):
        fn(message)


def _rerun() -> None:
    st = _st()
    fn = _app_attr("pb_rerun") or getattr(st, "rerun", None)
    if callable(fn):
        fn()


def _job_id_from_label(label: Any) -> int | None:
    label_text = str(label or "")
    if not label_text:
        return None
    try:
        jobs = _df_query(
            """
            SELECT j.id, j.job_no, j.job_name, COALESCE(b.name,'') AS builder
            FROM jobs j
            LEFT JOIN builders_clients b ON b.id=j.builder_client_id
            WHERE LOWER(COALESCE(j.status,'')) NOT IN ('archived','cancelled','deleted')
            ORDER BY j.job_no, j.job_name
            """
        )
    except Exception:
        return None
    if jobs is None or getattr(jobs, "empty", True):
        return None
    for _, row in jobs.iterrows():
        expected = f"{row['job_no']} · {row['job_name']} · {row['builder']}"
        if expected == label_text:
            return int(row["id"])
    return None


def _active_baseline(job_id: int) -> Any:
    try:
        return _df_query(
            """
            SELECT eb.id, eb.estimate_id, COALESCE(eb.total_ex_gst,0) AS total_ex_gst,
                   COALESCE(eb.locked_at,'') AS locked_at,
                   COALESCE(eb.locked_by,'') AS locked_by,
                   COALESCE(e.estimate_no,'') AS estimate_no,
                   COALESCE(e.revision,'') AS revision
            FROM estimate_baselines eb
            LEFT JOIN estimate_working_sheets e ON e.id=eb.estimate_id
            WHERE eb.job_id=? AND COALESCE(eb.active,1)=1
            ORDER BY eb.locked_at DESC, eb.id DESC
            LIMIT 1
            """,
            (int(job_id),),
        )
    except Exception:
        return None


def render_progress_baseline_unlock_panel(job_id: int) -> None:
    st = _st()
    if st is None or not job_id:
        return
    baseline = _active_baseline(int(job_id))
    if baseline is None:
        return
    with st.expander("Baseline tools - unlock / clear", expanded=False):
        if getattr(baseline, "empty", True):
            st.caption("No active locked baseline found for this job.")
            return
        row = baseline.iloc[0]
        baseline_id = int(row["id"])
        label = str(row.get("estimate_no") or "Estimate")
        revision = str(row.get("revision") or "")
        if revision:
            label = f"{label} · {revision}"
        st.warning("This job has a locked baseline. Clear it only when you need to replace the locked estimate.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Locked estimate", label)
        c2.metric("Baseline ex GST", f"${float(row.get('total_ex_gst') or 0):,.2f}")
        c3.metric("Locked by", str(row.get("locked_by") or "Unknown"))
        st.caption(f"Locked at: {row.get('locked_at') or 'Not recorded'}")
        unlink = st.checkbox(
            "Also unlink this estimate from the progress tracker",
            key=f"unlock_baseline_unlink_{job_id}_{baseline_id}",
        )
        confirm = st.checkbox(
            "I understand this unlocks the baseline for this job",
            key=f"unlock_baseline_confirm_{job_id}_{baseline_id}",
        )
        if st.button(
            "Clear / unlock locked baseline",
            disabled=not confirm,
            type="primary",
            key=f"unlock_baseline_button_{job_id}_{baseline_id}",
        ):
            try:
                _execute("UPDATE estimate_baselines SET active=0 WHERE id=? AND job_id=?", (baseline_id, int(job_id)))
                if unlink:
                    _execute("UPDATE job_progress_settings SET linked_estimate_id=NULL WHERE job_id=?", (int(job_id),))
                _notify("success", "Locked baseline cleared. You can now replace the estimate baseline.")
                _rerun()
            except Exception as exc:
                _notify("error", f"Could not clear locked baseline: {exc}")


def _patch_selectbox(owner: Any) -> bool:
    original = getattr(owner, "selectbox", None)
    if original is None or getattr(original, PATCH_KEY, False):
        return False

    def wrapped_selectbox(*args: Any, **kwargs: Any):
        key = str(kwargs.get("key") or "")
        result = original(*args, **kwargs)
        try:
            if key == SELECTBOX_KEY:
                job_id = _job_id_from_label(result)
                if job_id:
                    render_progress_baseline_unlock_panel(job_id)
        except Exception:
            pass
        return result

    setattr(wrapped_selectbox, PATCH_KEY, True)
    wrapped_selectbox._pb_original_selectbox = original
    setattr(owner, "selectbox", wrapped_selectbox)
    return True


def install_progress_baseline_unlock_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _patch_selectbox(st)
    try:
        delta_module = sys.modules.get("streamlit.delta_generator") or importlib.import_module("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None)
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_selectbox(delta_cls) or installed
    return installed
