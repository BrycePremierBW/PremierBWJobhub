from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
import streamlit as st


MOBILE_CSS = """
<style>
/* PB_JOBHUB_MOBILE_VIEWPORT_FIX */
section[data-testid="stSidebar"] > div:first-child {
    height: 100dvh !important;
    max-height: 100dvh !important;
}
[data-testid="stSidebarContent"] {
    overflow-y: auto !important;
    height: 100dvh !important;
    padding-bottom: calc(1rem + env(safe-area-inset-bottom)) !important;
    -webkit-overflow-scrolling: touch !important;
    touch-action: pan-y !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] label p {
    white-space: normal !important;
    overflow-wrap: anywhere !important;
    line-height: 1.25 !important;
}
div[data-testid="stHorizontalBlock"] {
    max-width: 100vw !important;
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch !important;
}
@media (max-width: 768px) {
    section[data-testid="stSidebar"] {
        width: min(92vw, 360px) !important;
        min-width: 0 !important;
        max-width: min(92vw, 360px) !important;
    }
    section[data-testid="stSidebar"] [role="radiogroup"] {
        max-height: 56vh !important;
        overflow-y: auto !important;
        -webkit-overflow-scrolling: touch !important;
    }
    input, textarea, select, button { font-size: 16px !important; }
}
</style>
"""


def install_mobile_shell() -> None:
    """Install viewport/PWA metadata and responsive sidebar behaviour."""
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)
    st.html(
        """
        <script>
        (() => {
          const head = document.head;
          let viewport = head.querySelector('meta[name="viewport"]');
          if (!viewport) {
            viewport = document.createElement('meta');
            viewport.name = 'viewport';
            head.appendChild(viewport);
          }
          viewport.content = 'width=device-width, initial-scale=1, viewport-fit=cover';

          let manifest = head.querySelector('link[rel="manifest"]');
          if (!manifest) {
            manifest = document.createElement('link');
            manifest.rel = 'manifest';
            head.appendChild(manifest);
          }
          manifest.href = '/app/static/manifest.webmanifest';

          const values = {
            'mobile-web-app-capable': 'yes',
            'apple-mobile-web-app-capable': 'yes',
            'apple-mobile-web-app-status-bar-style': 'black-translucent',
            'apple-mobile-web-app-title': 'JobHub'
          };
          Object.entries(values).forEach(([name, content]) => {
            let meta = head.querySelector(`meta[name="${name}"]`);
            if (!meta) {
              meta = document.createElement('meta');
              meta.name = name;
              head.appendChild(meta);
            }
            meta.content = content;
          });

          let icon = head.querySelector('link[rel="apple-touch-icon"]');
          if (!icon) {
            icon = document.createElement('link');
            icon.rel = 'apple-touch-icon';
            head.appendChild(icon);
          }
          icon.href = '/app/static/PB_JobHub_Icon.png';

          if (!window.__pbLeanSidebarCloseInstalled) {
            window.__pbLeanSidebarCloseInstalled = true;
            document.addEventListener('click', (event) => {
              if (!window.matchMedia('(max-width: 768px)').matches) return;
              const sidebar = event.target.closest('section[data-testid="stSidebar"]');
              const menuChoice = event.target.closest('[role="radiogroup"] label');
              if (!sidebar || !menuChoice) return;
              window.setTimeout(() => {
                const close = document.querySelector(
                  '[data-testid="stSidebarCollapseButton"] button, button[aria-label="Close sidebar"]'
                );
                if (close) close.click();
              }, 120);
            }, true);
          }
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def phone_push_provider_status(app_id: str, api_key: str) -> dict[str, Any]:
    if not app_id or not api_key:
        return {"configured": False, "connected": False, "message": "OneSignal credentials are not configured."}
    try:
        response = requests.get(
            "https://api.onesignal.com/notifications",
            params={"app_id": app_id, "limit": 1},
            headers={"Authorization": f"Key {api_key}"},
            timeout=12,
        )
        return {
            "configured": True,
            "connected": response.ok,
            "status_code": response.status_code,
            "message": "Connected" if response.ok else response.text[:300],
        }
    except Exception as exc:
        return {"configured": True, "connected": False, "message": str(exc)}


def render_phone_push_opt_in(key_prefix: str = "lean_phone_push") -> None:
    app_id = os.getenv("ONESIGNAL_APP_ID", "").strip()
    with st.sidebar.expander("Phone notifications", expanded=False):
        if not app_id:
            st.caption("Phone notifications are not configured on this deployment.")
            return
        st.caption("Enable JobHub alerts on this phone. On iPhone, first use Share → Add to Home Screen.")
        if st.button("Enable phone notifications", key=f"{key_prefix}_enable", use_container_width=True):
            st.html(
                f"""
                <script src="https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js" defer></script>
                <script>
                window.OneSignalDeferred = window.OneSignalDeferred || [];
                OneSignalDeferred.push(async function(OneSignal) {{
                  await OneSignal.init({{
                    appId: {json.dumps(app_id)},
                    serviceWorkerPath: 'app/static/OneSignalSDKWorker.js',
                    serviceWorkerParam: {{ scope: '/app/static/' }},
                    notifyButton: {{ enable: false }}
                  }});
                  await OneSignal.Notifications.requestPermission();
                  await OneSignal.User.PushSubscription.optIn();
                  const subscriptionId = OneSignal.User.PushSubscription.id;
                  window.parent.postMessage({{type:'pb-jobhub-push-id', subscriptionId}}, '*');
                }});
                </script>
                """,
                unsafe_allow_javascript=True,
            )
            st.success("The browser permission prompt has been opened.")
