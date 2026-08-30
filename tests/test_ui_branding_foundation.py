from jobhub.ui import (
    DEFAULT_COMPANY_NAME,
    DEFAULT_PRODUCT_NAME,
    pb_brand_context,
    pb_company_initials,
    pb_html,
)


def test_company_initials_are_safe_for_subscriber_brand_fallbacks():
    assert pb_company_initials("Premier Brushworks") == "PB"
    assert pb_company_initials("Coastal Coatings Group") == "CC"
    assert pb_company_initials("Acme") == "AC"
    assert pb_company_initials("") == "JH"


def test_brand_context_defaults_to_existing_pb_identity():
    brand = pb_brand_context({})
    assert brand["company_name"] == DEFAULT_COMPANY_NAME
    assert brand["product_name"] == DEFAULT_PRODUCT_NAME
    assert brand["initials"] == "PB"
    assert brand["logo_data_uri"] == ""


def test_brand_context_accepts_subscriber_specific_identity():
    brand = pb_brand_context(
        {
            "jobhub_company_name": "Coastal Coatings",
            "jobhub_product_name": "JobHub",
            "jobhub_company_subtitle": "Projects and field operations",
            "jobhub_company_logo_data_uri": "data:image/png;base64,abc123",
        }
    )
    assert brand["company_name"] == "Coastal Coatings"
    assert brand["subtitle"] == "Projects and field operations"
    assert brand["logo_data_uri"].startswith("data:image/png")
    assert brand["initials"] == "CC"


def test_html_escapes_uploaded_company_text_before_rendering():
    assert pb_html('<script>alert("x")</script>') == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"
