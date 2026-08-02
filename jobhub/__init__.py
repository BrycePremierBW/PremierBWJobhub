"""Premier Brushworks JobHub application package."""

from .mobile_sidebar_guard import install_mobile_sidebar_guard
from .navigation_state_guard import install_navigation_state_guard
from .notification_wording_guard import install_notification_wording_guard
from .push_configuration_guard import install_push_configuration_guard
from .stage_preset_guard import install_stage_preset_guard
from .stage_selection_guard import install_stage_selection_guard

# Install before the main app calls st.set_page_config/apply_pb_branding.  These
# guards only wrap Streamlit/os functions and render nothing during import, so
# Streamlit's page configuration still remains the first UI command.
install_push_configuration_guard()
install_notification_wording_guard()
install_mobile_sidebar_guard()
install_navigation_state_guard()
install_stage_selection_guard()
install_stage_preset_guard()
