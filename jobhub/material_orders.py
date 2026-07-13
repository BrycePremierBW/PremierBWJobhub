"""Material-order request, approval and approved-PDF workflow."""
from __future__ import annotations

import secrets
from pathlib import Path

from .runtime import *

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image as RLImage,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except Exception:  # pragma: no cover - deployment dependency gives the actionable error.
    colors = None


EDITABLE_ORDER_STATUSES = ("Draft", "Returned")
FINAL_ORDER_STATUSES = ("Approved", "Rejected")


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_text(value):
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def _new_order_number(job_id):
    job_df = df_query("SELECT job_no FROM jobs WHERE id = ?", (job_id,))
    job_no = str(job_df.iloc[0]["job_no"] or f"JOB{job_id}") if not job_df.empty else f"JOB{job_id}"
    clean_job_no = re.sub(r"[^A-Za-z0-9-]", "", job_no).upper() or f"JOB{job_id}"
    return f"MO-{clean_job_no}-{datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(2).upper()}"


def get_material_order(order_id):
    df = df_query(
        """
        SELECT r.*,
               j.job_no,
               j.job_name,
               j.site_address,
               bc.name AS builder_client
        FROM material_order_requests r
        JOIN jobs j ON j.id = r.job_id
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE r.id = ?
        """,
        (order_id,),
    )
    return None if df.empty else df.iloc[0].to_dict()


def get_material_order_items(order_id):
    return df_query(
        """
        SELECT i.id,
               i.request_id,
               i.product_id,
               COALESCE(NULLIF(i.product_code, ''), p.product_code, '') AS product_code,
               COALESCE(NULLIF(i.product_name, ''), p.product_name, '') AS product_name,
               COALESCE(NULLIF(i.supplier, ''), p.supplier, '') AS supplier,
               COALESCE(NULLIF(i.unit, ''), p.unit, '') AS unit,
               COALESCE(i.unit_price, p.price_ex_gst, 0) AS unit_price,
               i.colour,
               i.qty_required,
               i.qty_received,
               i.notes,
               i.sort_order,
               ROUND(CAST((COALESCE(i.unit_price, p.price_ex_gst, 0) * COALESCE(i.qty_required, 0)) AS numeric), 2) AS line_total
        FROM material_order_items i
        LEFT JOIN products p ON p.id = i.product_id
        WHERE i.request_id = ?
        ORDER BY i.sort_order, i.id
        """,
        (order_id,),
    )


def find_editable_material_order(job_id, requested_by_user_id):
    df = df_query(
        """
        SELECT id
        FROM material_order_requests
        WHERE job_id = ?
          AND requested_by_user_id = ?
          AND status IN ('Draft', 'Returned')
        ORDER BY id DESC
        LIMIT 1
        """,
        (job_id, requested_by_user_id),
    )
    return None if df.empty else int(df.iloc[0]["id"])


def create_material_order_request(
    job_id,
    requested_by_user_id,
    requested_by_employee_id,
    requested_by_name,
    required_delivery_date="",
    supplier="",
    employee_notes="",
):
    order_no = _new_order_number(job_id)
    created_at = _now_text()
    execute(
        """
        INSERT INTO material_order_requests
        (
            order_no, job_id, requested_by_user_id, requested_by_employee_id,
            requested_by_name, required_delivery_date, supplier, status,
            employee_notes, admin_notes, submitted_at, reviewed_at, reviewed_by,
            approved_pdf_path, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Draft', ?, '', '', '', '', '', ?, ?)
        """,
        (
            order_no,
            job_id,
            requested_by_user_id,
            requested_by_employee_id,
            requested_by_name,
            required_delivery_date,
            supplier,
            employee_notes,
            created_at,
            created_at,
        ),
    )
    df = df_query("SELECT id FROM material_order_requests WHERE order_no = ?", (order_no,))
    if df.empty:
        raise RuntimeError("The material order draft could not be created.")
    return int(df.iloc[0]["id"])


def update_material_order_header(order_id, required_delivery_date, supplier, employee_notes):
    order = get_material_order(order_id)
    if not order or str(order.get("status")) not in EDITABLE_ORDER_STATUSES:
        raise ValueError("Only Draft or Returned material orders can be edited.")
    execute(
        """
        UPDATE material_order_requests
        SET required_delivery_date = ?, supplier = ?, employee_notes = ?,
            status = 'Draft', updated_at = ?
        WHERE id = ?
        """,
        (required_delivery_date, supplier, employee_notes, _now_text(), order_id),
    )


def add_material_order_item(
    order_id,
    product_id,
    product_code,
    product_name,
    supplier,
    unit,
    unit_price,
    colour,
    qty_required,
    qty_received,
    notes,
):
    order = get_material_order(order_id)
    if not order or str(order.get("status")) not in EDITABLE_ORDER_STATUSES:
        raise ValueError("Only Draft or Returned material orders can be edited.")
    if float(qty_required or 0) <= 0:
        raise ValueError("Quantity required must be greater than zero.")
    if not str(product_name or "").strip() and not product_id:
        raise ValueError("Enter or select a product/material.")

    sort_df = df_query(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_sort FROM material_order_items WHERE request_id = ?",
        (order_id,),
    )
    sort_order = int(sort_df.iloc[0]["next_sort"] or 1)
    execute(
        """
        INSERT INTO material_order_items
        (
            request_id, product_id, product_code, product_name, supplier, unit,
            unit_price, colour, qty_required, qty_received, notes, sort_order, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            product_id,
            product_code,
            product_name,
            supplier,
            unit,
            float(unit_price or 0),
            colour,
            float(qty_required or 0),
            float(qty_received or 0),
            notes,
            sort_order,
            _now_text(),
        ),
    )
    execute(
        "UPDATE material_order_requests SET status = 'Draft', updated_at = ? WHERE id = ?",
        (_now_text(), order_id),
    )


def delete_material_order_item(order_id, item_id):
    order = get_material_order(order_id)
    if not order or str(order.get("status")) not in EDITABLE_ORDER_STATUSES:
        raise ValueError("Only Draft or Returned material orders can be edited.")
    execute("DELETE FROM material_order_items WHERE id = ? AND request_id = ?", (item_id, order_id))
    execute("UPDATE material_order_requests SET updated_at = ? WHERE id = ?", (_now_text(), order_id))


def submit_material_order(order_id):
    order = get_material_order(order_id)
    if not order or str(order.get("status")) not in EDITABLE_ORDER_STATUSES:
        raise ValueError("This material order cannot be submitted in its current status.")
    items = get_material_order_items(order_id)
    if items.empty:
        raise ValueError("Add at least one material item before submitting.")
    execute(
        """
        UPDATE material_order_requests
        SET status = 'Awaiting Approval', submitted_at = ?, reviewed_at = '', reviewed_by = '', updated_at = ?
        WHERE id = ?
        """,
        (_now_text(), _now_text(), order_id),
    )


def return_material_order(order_id, reviewed_by, admin_notes):
    order = get_material_order(order_id)
    if not order or str(order.get("status")) != "Awaiting Approval":
        raise ValueError("Only orders awaiting approval can be returned.")
    if not str(admin_notes or "").strip():
        raise ValueError("Add a reason before returning the order for changes.")
    execute(
        """
        UPDATE material_order_requests
        SET status = 'Returned', admin_notes = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (admin_notes, _now_text(), reviewed_by, _now_text(), order_id),
    )


def reject_material_order(order_id, reviewed_by, admin_notes):
    order = get_material_order(order_id)
    if not order or str(order.get("status")) != "Awaiting Approval":
        raise ValueError("Only orders awaiting approval can be rejected.")
    if not str(admin_notes or "").strip():
        raise ValueError("Add a reason before rejecting the order.")
    execute(
        """
        UPDATE material_order_requests
        SET status = 'Rejected', admin_notes = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (admin_notes, _now_text(), reviewed_by, _now_text(), order_id),
    )


def _pdf_paragraph(value, style):
    return Paragraph(html.escape(_safe_text(value)).replace("\n", "<br/>"), style)


def generate_approved_material_order_pdf(order_id, approved_by, admin_notes=""):
    if colors is None:
        raise RuntimeError("PDF support is unavailable. Add reportlab to requirements.txt and redeploy.")

    order = get_material_order(order_id)
    if not order:
        raise ValueError("Material order not found.")
    items = get_material_order_items(order_id)
    if items.empty:
        raise ValueError("This material order has no items.")

    job_no = str(order.get("job_no") or f"job_{order.get('job_id')}")
    order_no = str(order.get("order_no") or f"MO-{order_id}")
    output_dir = Path(get_job_folder(job_no)) / "material_orders"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', order_no)}_APPROVED.pdf"

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="PBTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=colors.HexColor("#242321")))
    styles.add(ParagraphStyle(name="PBSub", parent=styles["Normal"], fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#5f5b55")))
    styles.add(ParagraphStyle(name="PBCell", parent=styles["BodyText"], fontName="Helvetica", fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="PBCellRight", parent=styles["PBCell"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="PBApproved", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=14, leading=16, alignment=TA_CENTER, textColor=colors.HexColor("#2f6f4e")))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=f"Approved Material Order {order_no}",
        author="Premier Brushworks JobHub",
    )

    story = []
    if os.path.exists(PB_LOGO_BACKGROUND_IMAGE):
        try:
            logo = RLImage(PB_LOGO_BACKGROUND_IMAGE, width=48 * mm, height=18 * mm)
            logo.hAlign = "LEFT"
            story.append(logo)
            story.append(Spacer(1, 3 * mm))
        except Exception:
            pass

    story.append(Paragraph("Premier Brushworks", styles["PBTitle"]))
    story.append(Paragraph("Approved Paint & Materials Order", styles["PBSub"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("APPROVED FOR ORDERING", styles["PBApproved"]))
    story.append(Spacer(1, 4 * mm))

    info_rows = [
        ["Order number", order_no, "Status", "Approved"],
        ["Job", f"{_safe_text(order.get('job_no'))} - {_safe_text(order.get('job_name'))}", "Required delivery", _safe_text(order.get("required_delivery_date"))],
        ["Builder / client", _safe_text(order.get("builder_client")), "Supplier", _safe_text(order.get("supplier"))],
        ["Site", _safe_text(order.get("site_address")), "Requested by", _safe_text(order.get("requested_by_name"))],
        ["Submitted", _safe_text(order.get("submitted_at")), "Approved by", approved_by],
        ["Approved at", _now_text(), "", ""],
    ]
    info_table = Table(
        [[_pdf_paragraph(cell, styles["PBCell"]) for cell in row] for row in info_rows],
        colWidths=[28 * mm, 64 * mm, 28 * mm, 52 * mm],
        repeatRows=0,
    )
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eee9e2")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eee9e2")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cfc8be")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6 * mm))

    item_data = [[
        _pdf_paragraph("#", styles["PBCell"]),
        _pdf_paragraph("Product / material", styles["PBCell"]),
        _pdf_paragraph("Colour / finish", styles["PBCell"]),
        _pdf_paragraph("Supplier", styles["PBCell"]),
        _pdf_paragraph("Unit", styles["PBCell"]),
        _pdf_paragraph("Qty", styles["PBCellRight"]),
        _pdf_paragraph("Notes", styles["PBCell"]),
    ]]
    for idx, row in items.iterrows():
        item_data.append([
            _pdf_paragraph(idx + 1, styles["PBCell"]),
            _pdf_paragraph(f"{_safe_text(row['product_code'])} {_safe_text(row['product_name'])}".strip(), styles["PBCell"]),
            _pdf_paragraph(row["colour"], styles["PBCell"]),
            _pdf_paragraph(row["supplier"], styles["PBCell"]),
            _pdf_paragraph(row["unit"], styles["PBCell"]),
            _pdf_paragraph(row["qty_required"], styles["PBCellRight"]),
            _pdf_paragraph(row["notes"], styles["PBCell"]),
        ])

    items_table = Table(
        item_data,
        colWidths=[8 * mm, 51 * mm, 32 * mm, 29 * mm, 16 * mm, 13 * mm, 31 * mm],
        repeatRows=1,
    )
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#242321")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cfc8be")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf8f5")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 6 * mm))

    employee_notes = _safe_text(order.get("employee_notes"))
    final_admin_notes = _safe_text(admin_notes or order.get("admin_notes"))
    if employee_notes:
        story.append(Paragraph("Request notes", styles["Heading3"]))
        story.append(_pdf_paragraph(employee_notes, styles["PBSub"]))
        story.append(Spacer(1, 3 * mm))
    if final_admin_notes:
        story.append(Paragraph("Approval notes", styles["Heading3"]))
        story.append(_pdf_paragraph(final_admin_notes, styles["PBSub"]))
        story.append(Spacer(1, 3 * mm))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("This order was approved electronically in Premier Brushworks JobHub.", styles["PBSub"]))
    doc.build(story)
    return str(output_path)


def _sync_approved_items_to_material_entries(cur, order_id, order, items):
    for _, row in items.iterrows():
        cur.execute(
            "SELECT id FROM material_entries WHERE material_order_item_id = ? LIMIT 1",
            (int(row["id"]),),
        )
        if cur.fetchone():
            continue
        product_id = None if pd.isna(row["product_id"]) else int(row["product_id"])
        is_custom = product_id is None
        cur.execute(
            """
            INSERT INTO material_entries
            (
                job_id, product_id, qty_required, qty_received, date_ordered,
                supplier, notes, custom_product_code, custom_product_name,
                custom_supplier, custom_unit, custom_unit_price, custom_colour,
                material_order_request_id, material_order_item_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(order["job_id"]),
                product_id,
                float(row["qty_required"] or 0),
                float(row["qty_received"] or 0),
                str(order.get("required_delivery_date") or date.today()),
                str(row["supplier"] or order.get("supplier") or ""),
                f"Approved material order {order.get('order_no')}. {str(row['notes'] or '')}".strip(),
                str(row["product_code"] or ("CUSTOM" if is_custom else "")),
                str(row["product_name"] or "") if is_custom else "",
                str(row["supplier"] or "") if is_custom else "",
                str(row["unit"] or "") if is_custom else "",
                float(row["unit_price"] or 0) if is_custom else None,
                str(row["colour"] or ""),
                order_id,
                int(row["id"]),
            ),
        )


def approve_material_order(order_id, approved_by, admin_notes=""):
    order = get_material_order(order_id)
    if not order or str(order.get("status")) != "Awaiting Approval":
        raise ValueError("Only orders awaiting approval can be approved.")
    items = get_material_order_items(order_id)
    if items.empty:
        raise ValueError("This material order has no items.")

    pdf_path = generate_approved_material_order_pdf(order_id, approved_by, admin_notes)
    reviewed_at = _now_text()
    conn = connect()
    try:
        cur = conn.cursor()
        _sync_approved_items_to_material_entries(cur, order_id, order, items)
        cur.execute(
            """
            UPDATE material_order_requests
            SET status = 'Approved', admin_notes = ?, reviewed_at = ?, reviewed_by = ?,
                approved_pdf_path = ?, updated_at = ?
            WHERE id = ? AND status = 'Awaiting Approval'
            """,
            (admin_notes, reviewed_at, approved_by, pdf_path, reviewed_at, order_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError("The order status changed before approval completed. Refresh and try again.")
        cur.execute(
            """
            INSERT INTO job_documents
            (job_id, document_type, file_name, file_path, created_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(order["job_id"]),
                "Approved Paint & Materials Order",
                os.path.basename(pdf_path),
                pdf_path,
                reviewed_at,
                f"Approved material order {order.get('order_no')} by {approved_by} at {reviewed_at}.",
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception:
            pass
        raise
    finally:
        conn.close()

    return pdf_path


def _order_status_badge(status):
    status = str(status or "Draft")
    tones = {
        "Draft": "grey",
        "Awaiting Approval": "orange",
        "Approved": "green",
        "Returned": "orange",
        "Rejected": "red",
    }
    tone = tones.get(status, "grey")
    st.markdown(f'<span class="pb-status {tone}">{html.escape(status)}</span>', unsafe_allow_html=True)


def render_employee_material_orders(job_id, employee_id, employee_name, requested_by_user_id):
    st.markdown("### Material Order Request")
    st.caption("Build the order, submit it to admin, and download the final PDF after approval. Pricing remains hidden from employee accounts.")

    editable_id = find_editable_material_order(job_id, requested_by_user_id)
    if editable_id is None:
        with st.form(f"employee_start_material_order_{job_id}"):
            c1, c2 = st.columns(2)
            required_delivery = c1.date_input("Required delivery date", value=date.today() + timedelta(days=1))
            preferred_supplier = c2.text_input("Preferred supplier")
            request_notes = st.text_area("Order notes / site instructions")
            start_order = st.form_submit_button("Start Material Order")
        if start_order:
            editable_id = create_material_order_request(
                job_id,
                requested_by_user_id,
                employee_id,
                employee_name,
                str(required_delivery),
                preferred_supplier,
                request_notes,
            )
            st.success("Material order draft created.")
            refresh()
    else:
        order = get_material_order(editable_id)
        st.write(f"**Order:** {order.get('order_no')}")
        _order_status_badge(order.get("status"))
        if str(order.get("status")) == "Returned" and str(order.get("admin_notes") or "").strip():
            st.warning(f"Returned by admin: {order.get('admin_notes')}")

        with st.expander("Order details", expanded=True):
            with st.form(f"employee_material_order_header_{editable_id}"):
                c1, c2 = st.columns(2)
                current_delivery = str(order.get("required_delivery_date") or date.today())
                try:
                    delivery_value = date.fromisoformat(current_delivery[:10])
                except Exception:
                    delivery_value = date.today()
                required_delivery = c1.date_input("Required delivery date", value=delivery_value)
                preferred_supplier = c2.text_input("Preferred supplier", value=str(order.get("supplier") or ""))
                request_notes = st.text_area("Order notes / site instructions", value=str(order.get("employee_notes") or ""))
                save_header = st.form_submit_button("Save Draft Details")
            if save_header:
                update_material_order_header(editable_id, str(required_delivery), preferred_supplier, request_notes)
                st.success("Draft details saved.")
                refresh()

        st.markdown("#### Add an item")
        product_code_options = get_product_options()
        product_name_options = get_product_name_options()
        item_type_options = ["Saved Product", "One-off / Not Listed"] if product_code_options else ["One-off / Not Listed"]
        item_type = st.radio(
            "Item type",
            item_type_options,
            horizontal=True,
            key=f"employee_order_item_type_{editable_id}",
        )

        product_id = None
        product_code = ""
        product_name = ""
        supplier = str(order.get("supplier") or "")
        unit = ""
        unit_price = 0.0

        if item_type == "Saved Product":
            product_search = st.radio(
                "Select product by",
                ["Product Code", "Product Name"],
                horizontal=True,
                key=f"employee_order_product_search_{editable_id}",
            )
            options = product_code_options if product_search == "Product Code" else product_name_options
            selected_product = st.selectbox(
                product_search,
                list(options.keys()),
                key=f"employee_order_product_select_{editable_id}_{product_search}",
            )
            product_id = options[selected_product]
            product_df = df_query(
                "SELECT product_code, product_name, supplier, unit, price_ex_gst FROM products WHERE id = ?",
                (product_id,),
            )
            if not product_df.empty:
                row = product_df.iloc[0]
                product_code = str(row["product_code"] or "")
                product_name = str(row["product_name"] or "")
                supplier = str(row["supplier"] or supplier)
                unit = str(row["unit"] or "")
                unit_price = float(row["price_ex_gst"] or 0)
                st.info(f"Selected: {product_code} — {product_name}")
        else:
            c1, c2 = st.columns(2)
            product_code = c1.text_input("Product code / reference", value="CUSTOM", key=f"employee_order_custom_code_{editable_id}")
            product_name = c2.text_input("Product / material name", key=f"employee_order_custom_name_{editable_id}")
            c3, c4 = st.columns(2)
            supplier = c3.text_input("Supplier", value=supplier, key=f"employee_order_custom_supplier_{editable_id}")
            unit = c4.text_input("Unit", value="each", key=f"employee_order_custom_unit_{editable_id}")

        with st.form(f"employee_order_add_item_{editable_id}"):
            c1, c2, c3 = st.columns(3)
            colour = c1.text_input("Colour / finish")
            qty_required = c2.number_input("Qty required", min_value=0.0, step=1.0)
            qty_received = c3.number_input("Already received / loaded", min_value=0.0, step=1.0)
            item_notes = st.text_area("Item notes")
            add_item = st.form_submit_button("Add Item to Draft")
        if add_item:
            try:
                add_material_order_item(
                    editable_id,
                    product_id,
                    product_code,
                    product_name,
                    supplier,
                    unit,
                    unit_price,
                    colour,
                    qty_required,
                    qty_received,
                    item_notes,
                )
                st.success("Item added to the material order.")
                refresh()
            except Exception as exc:
                st.error(str(exc))

        items = get_material_order_items(editable_id)
        st.markdown("#### Draft items")
        if items.empty:
            st.info("No items have been added yet.")
        else:
            employee_items = items[["id", "product_code", "product_name", "supplier", "unit", "colour", "qty_required", "qty_received", "notes"]].copy()
            employee_items.columns = ["ID", "Code", "Product / Material", "Supplier", "Unit", "Colour / Finish", "Qty Required", "Already Received", "Notes"]
            st.dataframe(employee_items, width="stretch", hide_index=True)
            item_options = {
                f"ID {row['id']} | {row['product_code']} {row['product_name']} | Qty {row['qty_required']}": int(row["id"])
                for _, row in items.iterrows()
            }
            selected_delete = st.multiselect(
                "Remove draft items",
                list(item_options.keys()),
                key=f"employee_order_delete_items_{editable_id}",
            )
            if st.button("Remove Selected Items", key=f"employee_order_delete_button_{editable_id}"):
                if not selected_delete:
                    st.error("Select at least one item to remove.")
                else:
                    for label in selected_delete:
                        delete_material_order_item(editable_id, item_options[label])
                    st.success("Selected draft items removed.")
                    refresh()

            st.divider()
            if st.button("Submit Material Order to Admin", type="primary", key=f"employee_submit_order_{editable_id}"):
                try:
                    submit_material_order(editable_id)
                    st.success("Material order submitted to admin for approval.")
                    refresh()
                except Exception as exc:
                    st.error(str(exc))

    st.markdown("#### My material-order history for this job")
    history = df_query(
        """
        SELECT id, order_no AS 'Order No', status AS 'Status', required_delivery_date AS 'Required Delivery',
               supplier AS 'Supplier', submitted_at AS 'Submitted', reviewed_at AS 'Reviewed',
               reviewed_by AS 'Reviewed By', admin_notes AS 'Admin Notes', approved_pdf_path
        FROM material_order_requests
        WHERE job_id = ? AND requested_by_user_id = ?
        ORDER BY id DESC
        """,
        (job_id, requested_by_user_id),
    )
    if history.empty:
        st.caption("No previous material orders for this job.")
    else:
        display_history = history.drop(columns=["approved_pdf_path"])
        st.dataframe(display_history, width="stretch", hide_index=True)
        for _, row in history.iterrows():
            pdf_path = str(row["approved_pdf_path"] or "")
            if str(row["Status"]) == "Approved" and pdf_path and os.path.exists(pdf_path):
                with open(pdf_path, "rb") as file_obj:
                    st.download_button(
                        f"Download approved PDF — {row['Order No']}",
                        data=file_obj.read(),
                        file_name=os.path.basename(pdf_path),
                        mime="application/pdf",
                        key=f"employee_approved_order_download_{int(row['id'])}",
                    )


def render_material_order_admin_queue():
    st.markdown("## Material Order Approval Queue")
    st.caption("Review employee requests. Only approval generates the final PDF and posts the approved items to job material costs.")

    summary = df_query(
        """
        SELECT
            SUM(CASE WHEN status = 'Awaiting Approval' THEN 1 ELSE 0 END) AS awaiting,
            SUM(CASE WHEN status = 'Returned' THEN 1 ELSE 0 END) AS returned,
            SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) AS approved,
            SUM(CASE WHEN status = 'Rejected' THEN 1 ELSE 0 END) AS rejected
        FROM material_order_requests
        """
    )
    row = summary.iloc[0] if not summary.empty else {}
    cols = st.columns(4)
    cols[0].metric("Awaiting Approval", int(row.get("awaiting") or 0))
    cols[1].metric("Returned", int(row.get("returned") or 0))
    cols[2].metric("Approved", int(row.get("approved") or 0))
    cols[3].metric("Rejected", int(row.get("rejected") or 0))

    status_filter = st.multiselect(
        "Show statuses",
        ["Awaiting Approval", "Draft", "Returned", "Approved", "Rejected"],
        default=["Awaiting Approval"],
        key="material_order_admin_status_filter",
    )
    if not status_filter:
        status_filter = ["Awaiting Approval"]
    placeholders = ",".join(["?"] * len(status_filter))
    orders = df_query(
        f"""
        SELECT r.id, r.order_no, r.status, j.job_no, j.job_name,
               r.requested_by_name, r.required_delivery_date, r.supplier,
               r.submitted_at, r.reviewed_at, r.reviewed_by,
               COALESCE(SUM(COALESCE(i.unit_price, p.price_ex_gst, 0) * COALESCE(i.qty_required, 0)), 0) AS estimated_total
        FROM material_order_requests r
        JOIN jobs j ON j.id = r.job_id
        LEFT JOIN material_order_items i ON i.request_id = r.id
        LEFT JOIN products p ON p.id = i.product_id
        WHERE r.status IN ({placeholders})
        GROUP BY r.id, r.order_no, r.status, j.job_no, j.job_name,
                 r.requested_by_name, r.required_delivery_date, r.supplier,
                 r.submitted_at, r.reviewed_at, r.reviewed_by
        ORDER BY CASE WHEN r.status = 'Awaiting Approval' THEN 0 ELSE 1 END, r.id DESC
        """,
        tuple(status_filter),
    )
    if orders.empty:
        st.info("No material orders match the selected status filter.")
        return

    order_options = {
        f"{row['order_no']} | {row['job_no']} - {row['job_name']} | {row['status']} | {row['requested_by_name']}": int(row["id"])
        for _, row in orders.iterrows()
    }
    selected_label = st.selectbox("Select material order", list(order_options.keys()), key="material_order_admin_select")
    order_id = order_options[selected_label]
    order = get_material_order(order_id)
    items = get_material_order_items(order_id)

    st.markdown(f"### {order.get('order_no')}")
    _order_status_badge(order.get("status"))
    details = {
        "Job": f"{order.get('job_no')} - {order.get('job_name')}",
        "Site": order.get("site_address"),
        "Requested by": order.get("requested_by_name"),
        "Required delivery": order.get("required_delivery_date"),
        "Preferred supplier": order.get("supplier"),
        "Submitted": order.get("submitted_at"),
        "Employee notes": order.get("employee_notes"),
        "Admin notes": order.get("admin_notes"),
    }
    st.json(details, expanded=False)

    if items.empty:
        st.warning("This request has no items.")
    else:
        admin_items = items[["id", "product_code", "product_name", "supplier", "unit", "colour", "qty_required", "qty_received", "unit_price", "line_total", "notes"]].copy()
        admin_items.columns = ["ID", "Code", "Product / Material", "Supplier", "Unit", "Colour / Finish", "Qty Required", "Already Received", "Unit Price Ex GST", "Line Total Ex GST", "Notes"]
        st.dataframe(admin_items, width="stretch", hide_index=True)
        st.metric("Estimated Order Value Ex GST", f"${float(items['line_total'].fillna(0).sum()):,.2f}")

    status = str(order.get("status") or "")
    current_user = get_current_user() or {}
    reviewer = str(current_user.get("employee_name") or current_user.get("username") or "Admin")

    if status == "Awaiting Approval":
        if not is_admin():
            st.info("This order is awaiting admin review. Manager accounts can view it, but only an admin can approve, return or reject it.")
        else:
            admin_notes = st.text_area("Admin notes / approval comments", key=f"material_order_admin_notes_{order_id}")
            c1, c2, c3 = st.columns(3)
            if c1.button("Approve and Generate PDF", type="primary", key=f"approve_material_order_{order_id}"):
                try:
                    pdf_path = approve_material_order(order_id, reviewer, admin_notes)
                    st.success("Order approved. The PDF was generated, attached to the job, and approved items were posted to material costs.")
                    st.session_state[f"approved_pdf_{order_id}"] = pdf_path
                    refresh()
                except Exception as exc:
                    st.error(f"Approval failed: {exc}")
            if c2.button("Return for Changes", key=f"return_material_order_{order_id}"):
                try:
                    return_material_order(order_id, reviewer, admin_notes)
                    st.success("Order returned to the employee for changes.")
                    refresh()
                except Exception as exc:
                    st.error(str(exc))
            if c3.button("Reject Request", key=f"reject_material_order_{order_id}"):
                try:
                    reject_material_order(order_id, reviewer, admin_notes)
                    st.success("Material order rejected.")
                    refresh()
                except Exception as exc:
                    st.error(str(exc))

    pdf_path = str(order.get("approved_pdf_path") or "")
    if status == "Approved" and pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as file_obj:
            st.download_button(
                "Download Approved Material Order PDF",
                data=file_obj.read(),
                file_name=os.path.basename(pdf_path),
                mime="application/pdf",
                key=f"admin_download_approved_order_{order_id}",
            )
