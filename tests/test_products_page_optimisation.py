from pathlib import Path


PRODUCT_PAGE = Path("jobhub/pages/products.py").read_text(encoding="utf-8")


def test_products_page_uses_lazy_section_selector():
    assert "PRODUCT_SECTIONS" in PRODUCT_PAGE
    assert 'key="products_section"' in PRODUCT_PAGE
    assert "st.expander(" not in PRODUCT_PAGE


def test_product_register_query_only_exists_in_register_renderer():
    register = PRODUCT_PAGE.split("def _render_product_register", 1)[1].split("def render_products", 1)[0]
    form = PRODUCT_PAGE.split("def _render_product_form", 1)[1].split("def _render_product_register", 1)[0]
    assert "FROM products" in register
    assert "FROM products" not in form


def test_product_upsert_semantics_are_preserved():
    assert "ON CONFLICT(product_code) DO UPDATE SET" in PRODUCT_PAGE
    assert "price_ex_gst = excluded.price_ex_gst" in PRODUCT_PAGE
