"""Real browser/React probe; export only counts and boolean observations."""

import argparse
import json
import os
import time
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from caddy_browser_diagnostics import BrowserProgress, success
from caddy_fixture import CANARIES, SECRET, private_write


def probe(url, directory, hold, progress):
    # Import/start errors belong in the same closed report as browser errors.
    from playwright.sync_api import expect, sync_playwright

    violations = []
    # Synthetic query/cookie canaries originate in the browser itself. Their
    # absence is required in server logs, whereas browser privacy protects the
    # actual private origin key, which the browser must never receive.
    forbidden = [SECRET.encode()]

    def inspect(value):
        data = value if isinstance(value, bytes) else str(value).encode()
        if any(item in data for item in forbidden):
            violations.append("private-data-in-browser")

    with sync_playwright() as playwright:
        options = {
            "headless": True,
            "chromium_sandbox": os.name != "nt",
            "args": [
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-sync",
            ],
        }
        executable = os.environ.get("NM_BROWSER_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        progress.reached("browser_launch")
        browser = playwright.chromium.launch(**options)
        progress.reached("browser_version")
        if not executable:
            metadata = json.loads(files("playwright").joinpath("driver/package/browsers.json").read_text())
            bundle = next(item for item in metadata["browsers"] if item["name"] == "chromium-headless-shell")
            assert browser.version == bundle["browserVersion"], "required-chromium-version-mismatch"
        progress.reached("context_setup")
        context = browser.new_context(ignore_https_errors=True, service_workers="block")

        # Limit functional traffic to this fixture, including bundled fonts.
        def route(request_route):
            if request_route.request.url.startswith(url + "/"):
                if request_route.request.is_navigation_request():
                    request_route.continue_(headers=request_route.request.headers | {"Authorization": CANARIES["AUTH"]})
                else:
                    request_route.continue_()
            else:
                host = urlsplit(request_route.request.url).hostname
                violations.append("external-browser-request:" + str(host))
                request_route.abort()

        context.route("**/*", route)
        context.add_cookies([{"name": "fixture", "value": CANARIES["COOKIE"], "url": url}])
        context.set_extra_http_headers(
            {
                "X-NuevaMente-Origin-Key": CANARIES["HEADER"],
                "X-NuevaMente-Client-IP": "192.0.2.7",
                "Forwarded": "for=192.0.2.7;proto=http;host=evil.invalid",
                "X-Real-IP": "192.0.2.7",
                "X-Forwarded-For": "192.0.2.7",
                "X-Forwarded-Proto": "http",
                "X-Forwarded-Host": "evil.invalid",
            }
        )
        progress.reached("page_setup")
        page = context.new_page()

        def response(response):
            headers = response.headers
            assert "x-nuevamente-origin-key" not in headers, "public-private-header"
            assert "x-nuevamente-client-ip" not in headers, "public-private-client-header"
            inspect(headers)

        page.on("response", response)
        progress.reached("navigation")
        page.goto(url + "/?probe=" + CANARIES["QUERY"], wait_until="domcontentloaded")
        progress.reached("first_interaction")
        selector = page.get_by_label("Idioma de la interfaz", exact=True)
        selector.select_option("en")
        page.get_by_label("Interface language", exact=True).wait_for(timeout=15000)
        progress.reached("reconnection")
        page.reload(wait_until="domcontentloaded")
        selector = page.get_by_label("Interface language", exact=True)
        selector.wait_for(timeout=15000)
        assert selector.input_value() == "en", "language-lost-after-reload"
        page.get_by_role("heading", name="Upload a document", exact=True).wait_for(timeout=30000)
        start = time.monotonic()
        progress.reached("hold")
        if hold:
            page.wait_for_timeout(hold * 1000)
        progress.reached("second_interaction")
        selector.select_option("es")
        page.get_by_label("Idioma de la interfaz", exact=True).wait_for(timeout=15000)
        expect(page.locator('main input[type="file"]')).to_have_count(1)
        expect(page.locator('aside input[type="file"]')).to_have_count(0)
        progress.reached("privacy_audit")
        inspect(page.content())
        # Browser state deliberately contains injected cookie/header canaries;
        # audit only the actual private origin key across all browser storage.
        state = json.dumps(context.storage_state()) + page.evaluate(
            "JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})"
        )
        assert SECRET not in state, "origin-key-in-browser-storage"
        assert not violations, "browser-privacy-or-egress-violation:" + ",".join(sorted(set(violations)))
        result = {
            "browser_version": browser.version,
            "browser_source": "explicit-existing-executable" if executable else "pinned-playwright-bundle",
            "interactions": 2,
            "reload_verified": True,
            "main_upload_verified": True,
            "hold_seconds": round(time.monotonic() - start, 2),
            "private_origin_absent": True,
            "external_requests": 0,
        }
        progress.reached("browser_close")
        browser.close()
    progress.reached("result_write")
    private_write(directory / "browser-result.json", json.dumps(result))


def run_probe(url, directory, hold):
    progress = BrowserProgress()
    try:
        probe(url, directory, hold, progress)
    except Exception as error:
        report, exit_code = progress.failed(error), 1
    else:
        report, exit_code = success(), 0
    try:
        private_write(directory / f"browser-{hold}.diagnostic.json", json.dumps(report))
    except OSError:
        # The parent reports missing/invalid evidence, never successful fallback.
        return 1
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--hold", type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(run_probe(args.url, args.directory, args.hold))
