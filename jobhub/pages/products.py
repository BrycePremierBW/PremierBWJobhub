"""Products page."""
from __future__ import annotations

from ..runtime import *


def render_products():
    st.header("Products")

    with st.expander("Add / Update Product", expanded=True):
        with st.form("product_form"):
            col1, col2 = st.columns(2)
            code = col1.text_input("Product Code")
            product_name = col2.text_input("Product Name")
            col3, col4, col5 = st.columns(3)
            supplier = col3.text_input("Supplier")
            unit = col4.text_input("Unit")
            price = col5.number_input("Price Ex GST", min_value=0.0, step=1.0)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Product")

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
    st.dataframe(df, width="stretch", hide_index=True)


# =============================
# MATERIAL COSTS
# =============================
