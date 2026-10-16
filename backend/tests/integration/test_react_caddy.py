"""React compilado y Caddy/TLS locales reales; proveedores explícitamente simulados."""

import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path

import pytest
from caddy_fixture import ROOT, SECRET, ProxyHarness, private_write

pytestmark = pytest.mark.caddy_proxy


def test_react_browser_upload_through_caddy_without_external_requests():
    from playwright.sync_api import expect, sync_playwright

    violations, errors, requests = [], [], []
    with tempfile.TemporaryDirectory(prefix="react-caddy-", dir=ROOT / ".data") as temporary:
        proxy = ProxyHarness(Path(temporary), frontend="react")
        try:
            proxy.launch()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
                bundle = json.loads(files("playwright").joinpath("driver/package/browsers.json").read_text())
                expected = next(item for item in bundle["browsers"] if item["name"] == "chromium-headless-shell")
                assert browser.version == expected["browserVersion"], "pinned-browser-required"
                context = browser.new_context(ignore_https_errors=True, service_workers="block")

                def route(request_route):
                    if request_route.request.url.startswith(proxy.url + "/"):
                        requests.append(request_route.request.url)
                        request_route.continue_()
                    else:
                        violations.append("external-request")
                        request_route.abort()

                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda _error: errors.append("page-error"))
                page.on("console", lambda message: errors.append("console-error") if message.type == "error" else None)

                def response(result):
                    headers = result.headers
                    if {"x-nuevamente-origin-key", "x-nuevamente-client-ip"} & headers.keys():
                        violations.append("private-header")
                    if SECRET in json.dumps(headers):
                        violations.append("private-secret")

                page.on("response", response)
                page.goto(proxy.url + "/")
                page.get_by_role("button", name="Ya guardé mi código", exact=True).click()
                page.get_by_role("button", name="Cargar como documento de texto", exact=True).click()
                page.get_by_label("Título del texto", exact=True).fill("Prueba React por HTTPS local")
                page.get_by_label("Pegá el contenido del texto", exact=True).fill(
                    "# Red de prueba\nUna red virtual conecta recursos de una aplicación. "
                    "Las subredes organizan las direcciones de los recursos. "
                    "Las reglas de seguridad controlan el tráfico permitido."
                )
                form = page.locator(".nm-card").filter(has=page.get_by_label("Título del texto", exact=True))
                form.get_by_role("checkbox").check()
                page.get_by_role("button", name="Cargar como documento de texto", exact=True).last.click()
                expect(page.get_by_text("Listo", exact=True).first).to_be_visible(timeout=30000)
                expect(page.get_by_text("Prueba React por HTTPS local", exact=True).first).to_be_visible()
                assert page.evaluate("Object.keys(localStorage).length") == 0
                assert not violations and not errors, "react-browser-or-private-origin-failure"
                assert any("/assets/" in url and ".woff2" in url for url in requests), "local-font-required"
                context.close()
                browser.close()
            result = {
                "react_build": True,
                "caddy_tls_local": True,
                "browser_upload_ready": True,
                "external_requests": len(violations),
                "browser_errors": len(errors),
                "providers": "explicit-doubles",
                "public_acceptance": False,
            }
            evidence = os.environ.get("NM_CADDY_EVIDENCE_DIR")
            if evidence:
                output = Path(evidence)
                output.mkdir(parents=True, exist_ok=True)
                private_write(output / "react-observations.json", json.dumps(result, indent=2))
        finally:
            proxy.close()
