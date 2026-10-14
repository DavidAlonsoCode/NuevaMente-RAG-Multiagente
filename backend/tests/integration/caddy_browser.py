"""Real browser/Streamlit probe; export only counts and boolean observations."""

import argparse
import json
import os
import time
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from caddy_fixture import CANARIES, SECRET, private_write
from playwright.sync_api import sync_playwright


def probe(url, directory, hold):
    sockets, received, sent, violations, blocked_fonts = [], [], [], [], []
    # Synthetic query/cookie canaries originate in the browser itself. Their
    # absence is required in server logs, whereas browser privacy protects the
    # actual private origin key, which the browser must never receive.
    forbidden = [SECRET.encode()]

    def inspect(value):
        data = value if isinstance(value, bytes) else str(value).encode()
        if any(item in data for item in forbidden):
            violations.append("private-data-in-browser")

    def websocket(ws):
        sockets.append(ws)
        ws.on("framereceived", lambda payload: (inspect(payload), received.append(time.monotonic())))
        ws.on("framesent", lambda payload: (inspect(payload), sent.append(time.monotonic())))

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
        browser = playwright.chromium.launch(**options)
        if not executable:
            metadata = json.loads(files("playwright").joinpath("driver/package/browsers.json").read_text())
            bundle = next(item for item in metadata["browsers"] if item["name"] == "chromium-headless-shell")
            assert browser.version == bundle["browserVersion"], "required-chromium-version-mismatch"
        context = browser.new_context(ignore_https_errors=True, service_workers="block")

        # Limit functional traffic to this fixture. Existing remote fonts are
        # explicitly blocked and counted; every other external request fails.
        def route(request_route):
            if request_route.request.url.startswith(url + "/"):
                request_route.continue_()
            else:
                host = urlsplit(request_route.request.url).hostname
                if host in {"fonts.googleapis.com", "fonts.gstatic.com"}:
                    # Existing UI references web fonts. Keep this probe offline;
                    # record explicitly that remote typography is not validated.
                    blocked_fonts.append(host)
                else:
                    violations.append("external-browser-request:" + str(host))
                request_route.abort()

        context.route("**/*", route)
        context.add_cookies([{"name": "fixture", "value": CANARIES["COOKIE"], "url": url}])
        context.set_extra_http_headers(
            {
                "X-NuevaMente-Origin-Key": CANARIES["HEADER"],
                "Authorization": CANARIES["AUTH"],
                "X-NuevaMente-Client-IP": "192.0.2.7",
                "Forwarded": "for=192.0.2.7;proto=http;host=evil.invalid",
                "X-Real-IP": "192.0.2.7",
                "X-Forwarded-For": "192.0.2.7",
                "X-Forwarded-Proto": "http",
                "X-Forwarded-Host": "evil.invalid",
            }
        )
        page = context.new_page()
        handshakes = []
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")

        def handshake(event):
            headers = {name.lower(): value for name, value in event["response"]["headers"].items()}
            assert event["response"]["status"] == 101, "websocket-handshake-failed"
            assert not {"x-nuevamente-origin-key", "x-nuevamente-client-ip"} & headers.keys(), (
                "private-websocket-header"
            )
            inspect(headers)
            handshakes.append(1)

        cdp.on("Network.webSocketHandshakeResponseReceived", handshake)
        page.add_init_script("""
            window.__fixtureSockets = [];
            const NativeSocket = window.WebSocket;
            window.WebSocket = class extends NativeSocket {
                constructor(...args) { super(...args); window.__fixtureSockets.push(this); }
            };
        """)
        page.on("websocket", websocket)

        def response(response):
            headers = response.headers
            assert "x-nuevamente-origin-key" not in headers, "public-private-header"
            assert "x-nuevamente-client-ip" not in headers, "public-private-client-header"
            inspect(headers)

        page.on("response", response)
        page.goto(url + "/?probe=" + CANARIES["QUERY"], wait_until="domcontentloaded")
        selector = page.get_by_test_id("stSelectbox").first
        selector.wait_for(timeout=30000)
        selector.click()
        page.get_by_role("option", name="English", exact=True).click()
        page.get_by_text("Interface language", exact=True).wait_for(timeout=15000)
        assert received and sent, "websocket-no-bidirectional-messages"
        first_count = len(sockets)
        page.evaluate("window.__fixtureSockets.forEach(socket => socket.close())")
        deadline = time.monotonic() + 25
        while len(sockets) <= first_count and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        assert len(sockets) > first_count, "websocket-no-reconnection"
        # Interaction after reconnection and after an optional >120s silence.
        start = time.monotonic()
        if hold:
            page.wait_for_timeout(hold * 1000)
        before = len(received)
        selector.click()
        page.get_by_role("option", name="Español", exact=True).click()
        page.get_by_text("Idioma de la interfaz", exact=True).wait_for(timeout=15000)
        assert len(received) > before, "websocket-no-messages-after-interaction"
        assert len(handshakes) >= 2, "websocket-reconnect-handshake-not-observed"
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
            "websockets": len(sockets),
            "handshakes_audited": len(handshakes),
            "received_frames": len(received),
            "sent_frames": len(sent),
            "hold_seconds": round(time.monotonic() - start, 2),
            "private_origin_absent": True,
            "blocked_remote_fonts": len(blocked_fonts),
        }
        browser.close()
    private_write(directory / "browser-result.json", json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--hold", type=int, default=0)
    args = parser.parse_args()
    probe(args.url, args.directory, args.hold)
