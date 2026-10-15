"""Real frontend, with a private boolean-only server context observation."""

import hashlib
import json
import os
import runpy
from pathlib import Path

import streamlit as st

headers = st.context.headers
audit = {
    "origin_authenticated": hashlib.sha256(headers.get("X-NuevaMente-Origin-Key", "").encode()).hexdigest()
    == os.environ["NM_UI_ORIGIN_DIGEST"],
    "client_loopback": headers.get("X-NuevaMente-Client-IP") == "127.0.0.1",
    "host_coherent": headers.get("Host") == os.environ["NM_PUBLIC_AUTHORITY"],
    "origin_coherent": headers.get("Origin") == "https://" + os.environ["NM_PUBLIC_AUTHORITY"],
    "forwarding_coherent": (
        headers.get("X-Forwarded-Proto") == "https"
        and headers.get("X-Forwarded-Host") == os.environ["NM_PUBLIC_AUTHORITY"]
        and headers.get("X-Forwarded-For") == "127.0.0.1"
        and not headers.get("Forwarded")
        and not headers.get("X-Real-IP")
    ),
    "cors_enabled": st.get_option("server.enableCORS"),
    "xsrf_enabled": st.get_option("server.enableXsrfProtection"),
}
path = Path(os.environ["NM_UI_AUDIT"])
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
    json.dump(audit, file)
# No st.write of headers, digest, APIClient or this audit. No secret session_state.
runpy.run_path(str(Path(__file__).resolve().parents[3] / "frontend/app.py"), run_name="__main__")
