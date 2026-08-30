"""Products page with lazy section rendering."""
from __future__ import annotations

from ..runtime import *


PRODUCT_SECTIONS = ["Product Register", "Add / Update Product"]


def _render_product_form():
    pb_section_heading("Add / update product", "Create a new product or update an existing product code.")
    with st.form("product_form"):
        col1, col2 = st.columns(2)
        code = col1.text_input("Product Code")
        product_name = col2.text_input("Product Name")
        col3, col4, col5 = st.columns(3)
        supplier = col3.text_input("Supplier")
        unit = col4.text_input("Unit")
        price = col5.number_input("Price Ex GST", min_value=0.0, step=1.0)
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save Product", type="primary")

    if submitted and code:
        execute("""
            INSERT INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_code) DO UPDATE SET
                product_name = excluded.product_name,
                supplier = excluded.supplier,
                unit = excluded.unit,
                price_ex_gst = excluded.price_ex_gst,
                notes = excluded.notes
        """, (code, product_name, supplier, unit, price, notes))
        st.success(f"Saved product {code}")
        refresh()


def _render_product_register():
    pb_section_heading("Product register", "Browse saved products, suppliers, units and current prices.")
    df = df_query("""
        SELECT product_code AS 'Product Code',
               product_name AS 'Product Name',
               supplier AS 'Supplier',
               unit AS 'Unit',
               price_ex_gst AS 'Price Ex GST',
               notes AS 'Notes'
        FROM products
        ORDER BY product_code
    """)
    if df.empty:
        pb_empty_state("No products yet", "Add the first product to build the JobHub product register.")
    else:
        st.dataframe(df, width="stretch", hide_index=True)


def render_products():
    pb_page_header(
        "Products",
        "Maintain the reusable product and pricing register used by JobHub materials workflows.",
    )
    section = st.radio(
        "Product section",
        PRODUCT_SECTIONS,
        horizontal=True,
        key="products_section",
        label_visibility="collapsed",
    )
    if section == "Add / Update Product":
        _render_product_form()
    else:
        _render_product_register()


# =============================
# MATERIAL COSTS
# =============================
