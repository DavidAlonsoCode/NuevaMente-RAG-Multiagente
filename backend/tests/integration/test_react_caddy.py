"""React compilado y Caddy/TLS locales reales; proveedores explícitamente simulados."""

import json
import os
import re
import tempfile
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from caddy_fixture import ROOT, SECRET, ProxyHarness, private_write

pytestmark = pytest.mark.caddy_proxy


def upload_from_browser(page, url, title):
    from playwright.sync_api import expect

    page.goto(url + "/")
    recovery = page.locator("main code").inner_text()
    page.get_by_role("button", name="Ya guardé mi código", exact=True).click()
    page.get_by_role("button", name="Cargar como documento de texto", exact=True).click()
    page.get_by_label("Título del texto", exact=True).fill(title)
    page.get_by_label("Pegá el contenido del texto", exact=True).fill(
        "# Red de prueba\nUna red virtual conecta recursos de una aplicación. "
        "Las subredes organizan las direcciones de los recursos. "
        "Las reglas de seguridad controlan el tráfico permitido."
    )
    form = page.locator(".nm-card").filter(has=page.get_by_label("Título del texto", exact=True))
    form.get_by_role("checkbox").check()
    page.get_by_role("button", name="Cargar como documento de texto", exact=True).last.click()
    expect(page.get_by_text("Listo", exact=True).first).to_be_visible(timeout=30000)
    expect(page.get_by_role("main").get_by_text(title, exact=True)).to_be_visible()
    return recovery


def generate_from_browser(page):
    page.get_by_role("button", name="Generar material", exact=True).click()
    page.get_by_role("button", name="Confirmar y generar", exact=True).click()


def test_react_browser_upload_through_caddy_without_external_requests():
    from playwright.sync_api import sync_playwright

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

                def console(message):
                    if message.type != "error":
                        return
                    status = re.search(r"status of (\d{3})", message.text)
                    network = re.search(r"net::(ERR_[A-Z_]+)", message.text)
                    if "Content Security Policy" in message.text:
                        # Los mensajes CSP describen recursos/directivas estáticos.
                        # Omitir URLs para que el diagnóstico no publique contexto.
                        detail = re.sub(r"(?:https?|blob|data):[^\s'\"]+", "[resource]", message.text)
                        errors.append("console-csp:" + detail[:400])
                        return
                    errors.append("console-http-" + status[1] if status else network[1] if network else "console-other")

                page.on("console", console)

                def response(result):
                    headers = result.headers
                    if {"x-nuevamente-origin-key", "x-nuevamente-client-ip"} & headers.keys():
                        violations.append("private-header")
                    if SECRET in json.dumps(headers):
                        violations.append("private-secret")
                    if result.status >= 400:
                        route = "/".join(urlsplit(result.url).path.split("/")[:3])
                        errors.append(f"http-{result.status}:{route}")

                page.on("response", response)
                upload_from_browser(page, proxy.url, "Prueba React por HTTPS local")
                assert page.evaluate("Object.keys(localStorage).length") == 0
                assert not violations and not errors, json.dumps({"violations": violations, "errors": errors})
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


def test_generation_sse_reconnection_recovery_and_cooperative_cancel():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory(prefix="sse-react-", dir=ROOT / ".data") as temporary:
        proxy = ProxyHarness(Path(temporary), frontend="react")
        try:
            proxy.launch()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
                context = browser.new_context(ignore_https_errors=True, service_workers="block")
                page = context.new_page()
                admissions, cursors, failures = [], [], []

                def request(value):
                    path = urlsplit(value.url).path
                    if path == "/api/generate" and value.method == "POST":
                        admissions.append(1)
                    if path.startswith("/api/generations/") and path.endswith("/events"):
                        cursor = value.headers.get("last-event-id", "0")
                        cursors.append(int(cursor))

                page.on("request", request)
                page.on("pageerror", lambda _error: failures.append("unhandled-browser-error"))
                recovery = upload_from_browser(page, proxy.url, "Fuente conservada durante cancelación")
                generate_from_browser(page)
                expect(page.get_by_text("Etapa: Redactando el material", exact=True)).to_be_visible(timeout=30000)
                expect(page.get_by_text("Redacción pedagógica 1 de hasta 3", exact=True)).to_be_visible()

                # Cambiar idioma cierra la suscripción anterior y la retoma con
                # su cursor, sin perder el trabajo ni su idioma de contenido.
                def resumed(value):
                    path = urlsplit(value.url).path
                    cursor = value.headers.get("last-event-id", "0")
                    return path.startswith("/api/generations/") and path.endswith("/events") and int(cursor) > 0

                with page.expect_request(resumed):
                    page.get_by_label("Idioma de la interfaz", exact=True).select_option("en")
                expect(page.get_by_text("Stage: Writing the material", exact=True)).to_be_visible()
                with page.expect_request(resumed):
                    page.get_by_label("Interface language", exact=True).select_option("es")
                expect(page.get_by_text("Etapa: Redactando el material", exact=True)).to_be_visible()
                page.reload()
                page.get_by_role("button", name="Ya guardé mi código", exact=True).click()
                page.get_by_role("button", name="Recuperar mi espacio", exact=True).click()
                page.get_by_role("textbox", name="xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx", exact=True).fill(recovery)
                page.get_by_role("button", name="Entrar con mi código", exact=True).click()
                expect(page.get_by_text("Etapa: Redactando el material", exact=True)).to_be_visible(timeout=15000)
                page.get_by_role("button", name="Cancelar generación", exact=True).click()
                expect(
                    page.get_by_text(
                        "Cancelación solicitada. Una llamada en curso debe terminar antes de liberar el trabajo.",
                        exact=True,
                    )
                ).to_be_visible()
                proxy.release_generation()
                expect(
                    page.get_by_text("Generación cancelada. Tus documentos guardados se conservan.", exact=True)
                ).to_be_visible(timeout=15000)
                page.get_by_role("button", name="Documentos", exact=True).click()
                expect(page.get_by_text("Listo", exact=True).first).to_be_visible()
                assert len(admissions) == 1 and proxy.counters()["generation_calls"] == 1
                assert any(cursor > 0 for cursor in cursors), "last-event-id-not-resumed"
                assert not failures
                context.close()
                browser.close()
            evidence = os.environ.get("NM_CADDY_EVIDENCE_DIR")
            if evidence:
                private_write(
                    Path(evidence) / "generation-sse.json",
                    json.dumps(
                        {
                            "one_admission": True,
                            "cursor_resumed": True,
                            "recovered_existing_job": True,
                            "cooperative_cancel": True,
                            "documents_retained": True,
                            "providers": "explicit-doubles",
                            "public_acceptance": False,
                        },
                        indent=2,
                    ),
                )
        finally:
            proxy.close()


def test_quality_rejection_in_browser_exposes_no_draft():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory(prefix="reject-react-", dir=ROOT / ".data") as temporary:
        proxy = ProxyHarness(Path(temporary), frontend="react", reject_quality=True)
        try:
            proxy.launch()
            proxy.release_generation()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
                context = browser.new_context(ignore_https_errors=True, service_workers="block")
                page = context.new_page()
                upload_from_browser(page, proxy.url, "Fuente de rechazo simulado")
                generate_from_browser(page)
                expect(page.get_by_text("El material no superó el control de calidad", exact=True)).to_be_visible(
                    timeout=30000
                )
                expect(
                    page.get_by_text(
                        "Se agotaron las redacciones permitidas sin superar todos los controles de calidad.", exact=True
                    )
                ).to_be_visible()
                expect(page.get_by_role("button", name="Acotar a una sección", exact=True)).to_be_visible()
                expect(
                    page.get_by_role("button", name="Aportar otra versión del documento", exact=True)
                ).to_be_visible()
                assert not page.get_by_text("[SOURCE_TITLE]", exact=True).count()
                assert not page.get_by_text("Contenido revisado con fuentes", exact=True).count()
                assert proxy.counters()["generation_calls"] == 3
                context.close()
                browser.close()
        finally:
            proxy.close()


def test_basic_and_multiagent_reuse_document_without_relabeling_results():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory(prefix="modes-react-", dir=ROOT / ".data") as temporary:
        proxy = ProxyHarness(Path(temporary), frontend="react")
        try:
            proxy.launch()
            proxy.release_generation()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
                context = browser.new_context(ignore_https_errors=True, service_workers="block")
                page = context.new_page()
                modes, generation_ids, uploads, errors = [], [], [], []

                def request(req):
                    path = urlsplit(req.url).path
                    if req.method == "POST" and path == "/api/generate":
                        modes.append(req.post_data_json["modo_generacion"])
                    if req.method == "POST" and path == "/api/documents/upload":
                        uploads.append(1)

                def response(res):
                    if res.request.method == "POST" and urlsplit(res.url).path == "/api/generate":
                        generation_ids.append(res.json()["generation_id"])

                page.on("request", request)
                page.on("response", response)
                page.on("pageerror", lambda _error: errors.append("browser-error"))
                upload_from_browser(page, proxy.url, "Documento compartido por ambos modos")
                selector = page.get_by_label("Modo de generación", exact=True)
                expect(selector).to_have_value("multiagente")
                selector.select_option("basico")
                assert not modes
                generate_from_browser(page)
                expect(page.get_by_role("note").filter(has_text="Sin evaluación independiente")).to_be_visible(
                    timeout=30000
                )
                assert proxy.counters()["generation_calls"] == 1
                assert proxy.counters()["verification_calls"] == 0
                assert not page.get_by_text("Contenido revisado con fuentes", exact=True).count()
                selector.select_option("multiagente")
                assert modes == ["basico"]
                assert not page.get_by_text("Contenido revisado con fuentes", exact=True).count()
                generate_from_browser(page)
                expect(page.get_by_text("Contenido revisado con fuentes", exact=True)).to_be_visible(timeout=30000)
                assert modes == ["basico", "multiagente"]
                assert len(set(generation_ids)) == 2 and len(uploads) == 1
                assert proxy.counters()["generation_calls"] == 2
                assert not errors
                page.set_viewport_size({"width": 320, "height": 800})
                assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
                private_write(
                    ROOT / ".data/caddy-evidence/generation-modes.json",
                    json.dumps(
                        {
                            "modes": modes,
                            "independent_results": 2,
                            "uploads": 1,
                            "providers": "explicit-doubles",
                            "public_acceptance": False,
                        },
                        indent=2,
                    ),
                )
                context.close()
                browser.close()
        finally:
            proxy.close()
