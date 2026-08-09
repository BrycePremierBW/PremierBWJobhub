"""Premier Brushworks JobHub application package."""

from .ai_menu_guard import install_ai_menu_guard
from .bulk_delete_guard import install_bulk_delete_guard
from .database_timeout_guard import install_database_timeout_guard
from .document_centre_guard import install_document_centre_guard
from .integration_health_guard import install_integration_health_guard
from .job_folder_uploaded_documents_guard import install_job_folder_uploaded_documents_guard
from .mobile_sidebar_guard import install_mobile_sidebar_guard
from .mobile_top_navigation_guard import install_mobile_top_navigation_guard
from .navigation_state_guard import install_navigation_state_guard
from .notification_freeze_guard import install_notification_freeze_guard
from .notification_wording_guard import install_notification_wording_guard
from .page_render_freeze_guard import install_page_render_freeze_guard
from .permission_policy_guard import install_permission_policy_guard
from .po_job_switch_guard import install_po_job_switch_guard
from .po_stage_state_guard import install_po_stage_state_guard
from .po_upload_native_guard import install_po_upload_native_guard
from .po_upload_performance_guard import install_po_upload_performance_guard
from .progress_baseline_unlock_guard import install_progress_baseline_unlock_guard
from .progress_external_options_guard import install_progress_external_options_guard
from .push_configuration_guard import install_push_configuration_guard
from .runtime_performance_guard import install_runtime_performance_guard
from .session_keepalive_guard import install_session_keepalive_guard
from .setup_crew_leader_guard import install_setup_crew_leader_guard
from .setup_defaults_guard import install_setup_defaults_guard
from .setup_defaults_route_guard import install_setup_defaults_route_guard
from .setup_scheduler_crew_bridge_guard import install_setup_scheduler_crew_bridge_guard
from .setup_scheduler_startup_resilience_guard import install_setup_scheduler_startup_resilience_guard
from .sidebar_readability_guard import install_sidebar_readability_guard
from .stage_dwelling_builder_guard import install_stage_dwelling_builder_guard
from .stage_preset_guard import install_stage_preset_guard
from .stage_preset_selector_fix_guard import install_stage_preset_selector_fix_guard
from .stage_preset_visibility_guard import install_stage_preset_visibility_guard
from .stage_scope_refresh_guard import install_stage_scope_refresh_guard
from .stage_selection_guard import install_stage_selection_guard
from .stage_setup_simplifier_guard import install_stage_setup_simplifier_guard
from .startup_database_resilience_guard import install_startup_database_resilience_guard
from .swms_attach_fallback_guard import install_swms_attach_fallback_guard
from .swms_guard import install_swms_guard
from .swms_signature_index_guard import install_swms_signature_index_guard
from .swms_visibility_guard import install_swms_visibility_guard
from .system_health_guard import install_system_health_guard
from .timesheet_area_guard import install_timesheet_area_guard


def _retired_po_upload_route_guard() -> bool:
    """Retained as a no-op startup compatibility marker.

    The old guard patched every matching radio menu. The final direct route now
    owns desktop and mobile PO navigation, so reinstalling the legacy wrapper
    would recreate the ambiguous route chain that caused the production issue.
    """
    return False


install_po_upload_guard = _retired_po_upload_route_guard


# Install before the main app calls st.set_page_config/apply_pb_branding. These
# guards only wrap Streamlit/os/functions and render nothing during import, so
# Streamlit's page configuration still remains the first UI command.
install_push_configuration_guard()
install_session_keepalive_guard()
install_database_timeout_guard()
# The main app later decorates its idempotent startup bootstrap with
# st.cache_resource. Wrap only that named bootstrap so a brief database outage
# restarts the whole schema/seed sequence instead of ending the Streamlit run.
install_startup_database_resilience_guard()
install_runtime_performance_guard()
install_notification_freeze_guard()
install_page_render_freeze_guard()
install_notification_wording_guard()
install_mobile_sidebar_guard()
install_sidebar_readability_guard()
install_navigation_state_guard()
install_po_stage_state_guard()
install_po_job_switch_guard()

# Menu-injection guards must be installed before the mobile navigation wrapper.
# The mobile wrapper captures the current sidebar radio/selectbox functions.
install_setup_defaults_route_guard()
install_document_centre_guard()
install_permission_policy_guard()
install_system_health_guard()
install_integration_health_guard()
install_setup_defaults_guard()
install_setup_crew_leader_guard()
# The crew bridge performs a compatibility schema check during installation.
# Make only transient PostgreSQL connection failures fail-soft so a Render DB
# restart cannot crash all of JobHub during package import. The schema check is
# retried automatically when the bridge next needs it.
install_setup_scheduler_startup_resilience_guard()
install_setup_scheduler_crew_bridge_guard()

# Keep compatibility and storage helpers in their tested order. The legacy PO
# route call is intentionally a no-op; the native installer keeps the old helper
# reference aligned for callers that still import it directly.
install_po_upload_guard()
install_po_upload_performance_guard()
install_po_upload_native_guard()

# Upload PO is now a first-class route in pb_jobhub_app.py. Mobile navigation
# reads that same native menu instead of relying on a radio/session wrapper.
install_mobile_top_navigation_guard()

install_progress_baseline_unlock_guard()
install_job_folder_uploaded_documents_guard()
install_progress_external_options_guard()
install_stage_selection_guard()
install_stage_preset_selector_fix_guard()
install_stage_dwelling_builder_guard()
install_stage_scope_refresh_guard()
install_stage_preset_guard()
install_stage_preset_visibility_guard()
install_stage_setup_simplifier_guard()
install_timesheet_area_guard()
install_bulk_delete_guard()
install_swms_guard()
install_swms_attach_fallback_guard()
install_swms_signature_index_guard()
install_swms_visibility_guard()
install_ai_menu_guard()
