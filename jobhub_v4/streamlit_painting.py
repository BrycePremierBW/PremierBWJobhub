"""Streamlit workspace for JobHub V4 painting-specific operations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

import pandas as pd
import streamlit as st

from .handover import build_handover_manifest, build_handover_zip
from .paint import calculate_paint_quantity, colour_order_allowed, optimise_pack_mix
from .revisions import compare_revisions
from .schema import ensure_v4_schema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_user(ctx: dict[str, Any]) -> dict[str, Any]:
    return ctx["get_current_user"]() or {}


def _current_user_name(ctx: dict[str, Any]) -> str:
    user = _current_user(ctx)
    return str(user.get("username") or user.get("name") or user.get("id") or "unknown")


def _job_picker(ctx: dict[str, Any]) -> tuple[int, dict[str, Any]] | tuple[None, None]:
    jobs = ctx["df_query"](
        """
        SELECT j.id, j.job_no, j.job_name, j.site_address,
               COALESCE(bc.name, '') AS builder_client
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE COALESCE(j.archived_at, '') = ''
        ORDER BY j.job_no
        """
    )
    if jobs.empty:
        st.info("Create a job before using Painting Intelligence.")
        return None, None
    options = {
        f"{row['job_no']} — {row['job_name']}": row.to_dict()
        for _, row in jobs.iterrows()
    }
    selected = st.selectbox("Job", list(options), key="v4_job")
    job = options[selected]
    return int(job["id"]), job


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _paint_calculator(ctx: dict[str, Any], job_id: int) -> None:
    st.subheader("Paint quantity and pack optimisation")
    st.caption(
        "Calculate coating-system litres, use warehouse stock first and select "
        "the lowest-cost 4 L / 10 L / 15 L supplier mix."
    )
    left, right = st.columns(2)
    area_name = left.text_input("Area / location", key="v4_area_name")
    substrate = left.text_input("Substrate", key="v4_substrate")
    product = left.text_input("Paint product", key="v4_product")
    colour = left.text_input("Colour", key="v4_colour")
    area_sqm = right.number_input("Area (m²)", min_value=0.0, step=10.0, key="v4_area")
    coats = right.number_input(
        "Coats",
        min_value=1,
        max_value=10,
        value=2,
        step=1,
        key="v4_coats",
    )
    coverage = right.number_input(
        "Coverage (m²/L/coat)",
        min_value=0.1,
        value=12.0,
        step=0.5,
        key="v4_coverage",
    )
    waste = right.number_input(
        "Waste allowance (%)",
        min_value=0.0,
        max_value=100.0,
        value=10.0,
        step=1.0,
        key="v4_waste",
    )

    st.markdown("#### Warehouse stock and supplier pricing")
    pack_columns = st.columns(3)
    stock: dict[int, int] = {}
    prices: dict[int, float] = {}
    for column, size in zip(pack_columns, (4, 10, 15)):
        with column:
            st.markdown(f"**{size} L**")
            stock[size] = int(
                st.number_input(
                    "Warehouse packs",
                    min_value=0,
                    step=1,
                    key=f"v4_stock_{size}",
                )
            )
            prices[size] = float(
                st.number_input(
                    "Supplier price ex GST",
                    min_value=0.0,
                    step=1.0,
                    key=f"v4_price_{size}",
                )
            )

    if not st.button("Calculate paint and packs", type="primary", key="v4_calculate"):
        return
    try:
        quantity = calculate_paint_quantity(
            area_sqm=area_sqm,
            coats=coats,
            coverage_sqm_per_litre=coverage,
            waste_percent=waste,
        )
        plan = optimise_pack_mix(
            required_litres=quantity["required_litres"],
            warehouse_stock=stock,
            supplier_prices=prices,
        )
    except ValueError as exc:
        st.error(str(exc))
        return

    st.session_state["v4_last_calculation"] = {
        "quantity": quantity,
        "plan": plan,
        "form": {
            "area_name": area_name,
            "substrate": substrate,
            "product": product,
            "colour": colour,
        },
    }
    metric_columns = st.columns(4)
    metric_columns[0].metric("Required", f"{quantity['required_litres']:.2f} L")
    metric_columns[1].metric("Supplied", f"{plan['supplied_litres']:.0f} L")
    metric_columns[2].metric("Excess", f"{plan['excess_litres']:.2f} L")
    metric_columns[3].metric("Purchase ex GST", f"${plan['purchase_cost']:,.2f}")
    st.dataframe(pd.DataFrame(plan["lines"]), hide_index=True, width="stretch")

    approval = ctx["df_query"](
        """
        SELECT status, approved_by, approved_at
        FROM colour_approvals
        WHERE job_id = ? AND LOWER(colour_name) = LOWER(?)
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (job_id, colour),
    )
    if approval.empty:
        allowed, reason = False, "No colour approval is recorded for this colour."
    else:
        row = approval.iloc[0].to_dict()
        allowed, reason = colour_order_allowed(
            row.get("status", ""),
            approved_by=row.get("approved_by", ""),
            approved_at=row.get("approved_at", ""),
        )
    if allowed:
        st.success("Colour gate passed. The material order may be prepared.")
    else:
        st.warning(reason)

    if st.button("Save coating system", key="v4_save_system"):
        if not area_name.strip() or not product.strip():
            st.error("Area / location and paint product are required.")
            return
        timestamp = _now()
        system_id = str(uuid4())
        ctx["execute"](
            """
            INSERT INTO paint_systems
            (id, job_id, area_name, substrate, product_name, colour_name,
             area_sqm, coat_count, coverage_sqm_per_litre, waste_percent,
             required_litres, pack_plan_json, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                job_id,
                area_name.strip(),
                substrate.strip(),
                product.strip(),
                colour.strip(),
                area_sqm,
                int(coats),
                coverage,
                waste,
                quantity["required_litres"],
                json.dumps(plan),
                _current_user_name(ctx),
                timestamp,
                timestamp,
            ),
        )
        ctx["record_audit_event"](
            "paint_system_created",
            "paint_system",
            system_id,
            {"job_id": job_id, "required_litres": quantity["required_litres"]},
        )
        ctx["pb_success"]("Coating system and pack plan saved.")


def _colour_approvals(ctx: dict[str, Any], job_id: int) -> None:
    st.subheader("Colour approval gate")
    st.caption("Orders remain blocked until an approved colour, approver and date are recorded.")
    with st.form("v4_colour_approval_form", clear_on_submit=True):
        area_name = st.text_input("Area / location")
        colour_name = st.text_input("Colour name")
        colour_code = st.text_input("Colour code")
        product_name = st.text_input("Product")
        status = st.selectbox("Status", ["pending", "requested", "approved", "rejected"])
        approval_reference = st.text_input("Approval reference")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save colour approval", type="primary")
    if submitted:
        if not area_name.strip() or not colour_name.strip():
            st.error("Area / location and colour name are required.")
        else:
            timestamp = _now()
            user_name = _current_user_name(ctx)
            approval_id = str(uuid4())
            approved_by = user_name if status == "approved" else ""
            approved_at = timestamp if status == "approved" else ""
            ctx["execute"](
                """
                INSERT INTO colour_approvals
                (id, job_id, area_name, colour_name, colour_code, product_name,
                 status, requested_by, requested_at, approved_by, approved_at,
                 approval_reference, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    job_id,
                    area_name.strip(),
                    colour_name.strip(),
                    colour_code.strip(),
                    product_name.strip(),
                    status,
                    user_name,
                    timestamp,
                    approved_by,
                    approved_at,
                    approval_reference.strip(),
                    notes.strip(),
                    timestamp,
                    timestamp,
                ),
            )
            ctx["record_audit_event"](
                "colour_approval_recorded",
                "colour_approval",
                approval_id,
                {"job_id": job_id, "status": status},
            )
            ctx["pb_success"]("Colour approval saved.")
            ctx["pb_rerun"]()

    approvals = ctx["df_query"](
        """
        SELECT area_name AS "Area", colour_name AS "Colour",
               colour_code AS "Code", product_name AS "Product",
               status AS "Status", approved_by AS "Approved by",
               approved_at AS "Approved at", approval_reference AS "Reference"
        FROM colour_approvals
        WHERE job_id = ?
        ORDER BY updated_at DESC
        """,
        (job_id,),
    )
    st.dataframe(approvals, hide_index=True, width="stretch")


def _plan_evidence(ctx: dict[str, Any], job_id: int) -> None:
    st.subheader("Plan-linked evidence")
    st.caption("Link progress, defect and close-out evidence to a plan and revision reference.")
    with st.form("v4_evidence_form", clear_on_submit=True):
        plan_reference = st.text_input("Plan / specification reference")
        revision = st.text_input("Revision")
        location = st.text_input("Location / grid reference")
        evidence_type = st.selectbox(
            "Evidence type",
            [
                "progress_photo",
                "completion_photo",
                "defect",
                "defect_closeout",
                "warranty",
                "other",
            ],
        )
        title = st.text_input("Title")
        description = st.text_area("Description")
        photo_id = st.number_input("Existing JobHub photo ID (optional)", min_value=0, step=1)
        document_id = st.number_input(
            "Existing JobHub document ID (optional)",
            min_value=0,
            step=1,
        )
        submitted = st.form_submit_button("Link evidence", type="primary")
    if submitted:
        if not plan_reference.strip() or not title.strip():
            st.error("Plan reference and title are required.")
        elif not int(photo_id) and not int(document_id) and evidence_type not in {"warranty", "other"}:
            st.error("Select an existing photo or document for this evidence.")
        else:
            evidence_id = str(uuid4())
            timestamp = _now()
            ctx["execute"](
                """
                INSERT INTO plan_evidence
                (id, job_id, plan_reference, revision, location_reference,
                 evidence_type, title, description, photo_id, document_id,
                 status, captured_by, captured_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    evidence_id,
                    job_id,
                    plan_reference.strip(),
                    revision.strip(),
                    location.strip(),
                    evidence_type,
                    title.strip(),
                    description.strip(),
                    int(photo_id) or None,
                    int(document_id) or None,
                    _current_user_name(ctx),
                    timestamp,
                    timestamp,
                ),
            )
            ctx["record_audit_event"](
                "plan_evidence_linked",
                "plan_evidence",
                evidence_id,
                {"job_id": job_id, "plan_reference": plan_reference.strip()},
            )
            ctx["pb_success"]("Evidence linked to the plan reference.")
            ctx["pb_rerun"]()

    evidence = ctx["df_query"](
        """
        SELECT plan_reference AS "Plan", revision AS "Revision",
               location_reference AS "Location", evidence_type AS "Type",
               title AS "Title", photo_id AS "Photo ID",
               document_id AS "Document ID", captured_by AS "Captured by",
               captured_at AS "Captured"
        FROM plan_evidence
        WHERE job_id = ? AND status <> 'void'
        ORDER BY created_at DESC
        """,
        (job_id,),
    )
    st.dataframe(evidence, hide_index=True, width="stretch")


def _revision_compare(ctx: dict[str, Any], job_id: int) -> None:
    st.subheader("Revision comparison and draft variation detection")
    document_name = st.text_input("Drawing / specification name", key="v4_revision_document")
    previous_revision = st.text_input("Previous revision", key="v4_previous_revision")
    current_revision = st.text_input("Current revision", key="v4_current_revision")
    previous_text = st.text_area(
        "Previous extracted text",
        height=180,
        key="v4_previous_text",
    )
    current_text = st.text_area(
        "Current extracted text",
        height=180,
        key="v4_current_text",
    )
    if not st.button("Compare revisions", type="primary", key="v4_compare"):
        return
    try:
        comparison = compare_revisions(
            previous_text,
            current_text,
            previous_label=previous_revision or "previous",
            current_label=current_revision or "current",
        )
    except ValueError as exc:
        st.error(str(exc))
        return

    st.session_state["v4_last_revision_comparison"] = comparison
    metrics = st.columns(3)
    metrics[0].metric("Similarity", f"{comparison['similarity_percent']:.1f}%")
    metrics[1].metric("Added lines", len(comparison["added_lines"]))
    metrics[2].metric("Variation risk", f"{comparison['variation_risk_score']}/100")
    if comparison["likely_variation"]:
        st.warning("Likely scope change detected. A draft variation will be created for review.")
    else:
        st.info("No strong variation signal detected. Review the diff before closing.")
    st.code(comparison["diff"] or "No textual changes.", language="diff")

    timestamp = _now()
    revision_id = str(uuid4())
    ctx["execute"](
        """
        INSERT INTO drawing_revisions
        (id, job_id, document_name, previous_revision, current_revision,
         similarity_percent, variation_risk_score, comparison_json,
         compared_by, compared_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision_id,
            job_id,
            document_name.strip() or "Drawing / specification",
            previous_revision.strip(),
            current_revision.strip(),
            comparison["similarity_percent"],
            comparison["variation_risk_score"],
            json.dumps(comparison),
            _current_user_name(ctx),
            timestamp,
        ),
    )
    if comparison["likely_variation"]:
        suggestion_id = str(uuid4())
        ctx["execute"](
            """
            INSERT INTO variation_suggestions
            (id, job_id, drawing_revision_id, title, reason, risk_score,
             status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            (
                suggestion_id,
                job_id,
                revision_id,
                f"Review scope change — {document_name.strip() or current_revision or 'revision'}",
                "; ".join(comparison["added_lines"][:8])
                or "Revision comparison identified a likely scope change.",
                comparison["variation_risk_score"],
                _current_user_name(ctx),
                timestamp,
                timestamp,
            ),
        )
        ctx["create_management_notifications"](
            event_type="draft_variation_detected",
            title="Drawing revision may require a variation",
            message=f"{document_name or 'A drawing/specification'} has a risk score of {comparison['variation_risk_score']}/100.",
            job_id=job_id,
            entity_type="variation_suggestion",
            entity_id=suggestion_id,
        )
    ctx["record_audit_event"](
        "drawing_revisions_compared",
        "drawing_revision",
        revision_id,
        {"job_id": job_id, "risk_score": comparison["variation_risk_score"]},
    )


def _handover(ctx: dict[str, Any], job_id: int, job: dict[str, Any]) -> None:
    st.subheader("Builder close-out and handover pack")
    evidence = ctx["df_query"](
        """
        SELECT plan_reference, revision, location_reference, evidence_type,
               title, description, photo_id, document_id, status,
               captured_by, captured_at
        FROM plan_evidence
        WHERE job_id = ? AND status <> 'void'
        ORDER BY created_at
        """,
        (job_id,),
    )
    colours = ctx["df_query"](
        """
        SELECT area_name, colour_name, colour_code, product_name, status,
               approved_by, approved_at, approval_reference, notes
        FROM colour_approvals
        WHERE job_id = ?
        ORDER BY created_at
        """,
        (job_id,),
    )
    manifest = build_handover_manifest(
        job=job,
        evidence=_records(evidence),
        colour_approvals=_records(colours),
    )
    completed = sum(1 for item in manifest["checklist"] if item["complete"])
    st.progress(completed / max(1, len(manifest["checklist"])))
    st.write(f"{completed} of {len(manifest['checklist'])} close-out requirements complete.")
    checklist_frame = pd.DataFrame(manifest["checklist"]).rename(
        columns={"label": "Requirement", "complete": "Complete"}
    )
    st.dataframe(checklist_frame[["Requirement", "Complete"]], hide_index=True)
    if manifest["ready"]:
        st.success("The handover evidence checklist is complete.")
    else:
        st.warning("Missing: " + ", ".join(manifest["missing_requirements"]))

    if st.button("Generate handover pack", type="primary", key="v4_handover"):
        pack_id = str(uuid4())
        timestamp = _now()
        manifest["generated_at"] = timestamp
        ctx["execute"](
            """
            INSERT INTO handover_packs
            (id, job_id, status, manifest_json, generated_by, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                pack_id,
                job_id,
                "ready" if manifest["ready"] else "draft",
                json.dumps(manifest),
                _current_user_name(ctx),
                timestamp,
            ),
        )
        ctx["record_audit_event"](
            "handover_pack_generated",
            "handover_pack",
            pack_id,
            {"job_id": job_id, "ready": manifest["ready"]},
        )
        st.session_state["v4_handover_download"] = {
            "name": f"{job.get('job_no', 'job')}_handover_pack.zip",
            "data": build_handover_zip(manifest),
        }

    download = st.session_state.get("v4_handover_download")
    if download:
        st.download_button(
            "Download handover pack",
            data=download["data"],
            file_name=download["name"],
            mime="application/zip",
            type="primary",
        )


def render_painting_intelligence(ctx: dict[str, Any]) -> None:
    st.header("Painting Intelligence")
    st.caption(
        "Coating quantities, pack optimisation, colour gates, plan evidence, "
        "revision risk and builder handover."
    )
    ensure_v4_schema(ctx["connect"])
    job_id, job = _job_picker(ctx)
    if not job_id:
        return
    tabs = st.tabs(
        [
            "Paint & packs",
            "Colour approvals",
            "Plan evidence",
            "Revision compare",
            "Handover",
        ]
    )
    with tabs[0]:
        _paint_calculator(ctx, job_id)
    with tabs[1]:
        _colour_approvals(ctx, job_id)
    with tabs[2]:
        _plan_evidence(ctx, job_id)
    with tabs[3]:
        _revision_compare(ctx, job_id)
    with tabs[4]:
        _handover(ctx, job_id, job)
