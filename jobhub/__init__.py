"""Premier Brushworks JobHub application package."""

from .mobile_sidebar_guard import install_mobile_sidebar_guard

# Install before the main app calls st.set_page_config/apply_pb_branding.  The
# guard only wraps Streamlit functions and renders nothing during import, so
# Streamlit's page configuration still remains the first UI command.
install_mobile_sidebar_guard()
