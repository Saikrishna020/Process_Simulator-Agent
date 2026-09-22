"""Local Edge/CDP smoke test. Start the app on 127.0.0.1:8000 first.

Uses installed httpx/websockets/psutil. Screenshots and browser profile remain
under runs/ui-checks. Exercises the real model, forms, job polling and results.
"""
import base64
import argparse
import json
from pathlib import Path
import subprocess
import socket
import time
import uuid

import httpx
import psutil
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--assistant", action="store_true", help="Also verify live DeepSeek replies, confirmation and recovery")
parser.add_argument("--local-assistant", action="store_true", help="Verify chat using only the scripted fixture server on port 8001")
parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Use port 8001 with ui_fixture_server.py for local-only chat checks")
args = parser.parse_args()
if args.local_assistant:
    args.base_url = "http://127.0.0.1:8001"
    assert httpx.get(args.base_url + "/api/test-provider").json() == {"provider": "local-fixture", "external_requests": False}
OUTPUT = ROOT / "runs" / "ui-checks" / uuid.uuid4().hex[:8]
OUTPUT.mkdir(parents=True)
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
with socket.socket() as free_port:
    free_port.bind(("127.0.0.1", 0))
    port = free_port.getsockname()[1]
browser_log = (OUTPUT / 'browser.log').open('w', encoding='utf-8')
browser = subprocess.Popen([str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run", "--no-sandbox", "--disable-extensions",
    "--no-default-browser-check", f"--remote-debugging-port={port}", f"--user-data-dir={OUTPUT / 'profile'}", "about:blank"],
    creationflags=subprocess.CREATE_NO_WINDOW, stdout=browser_log, stderr=browser_log)
errors = []
try:
    for _ in range(100):
        try:
            pages = httpx.get(f"http://127.0.0.1:{port}/json", timeout=2).json()
            target = next(p for p in pages if p["type"] == "page")
            break
        except Exception:
            time.sleep(.2)
    else:
        raise RuntimeError("Headless Edge did not start.")
    time.sleep(2)
    target = httpx.put(f"http://127.0.0.1:{port}/json/new?about:blank", timeout=5).json()
    with connect(target["webSocketDebuggerUrl"], max_size=20_000_000, proxy=None) as ws:
        serial = 0
        def call(method, params=None):
            global serial
            serial += 1
            ws.send(json.dumps(dict(id=serial, method=method, params=params or {})))
            while True:
                message = json.loads(ws.recv(timeout=30))
                if message.get("method") == "Runtime.exceptionThrown":
                    errors.append(message["params"])
                if message.get("id") == serial:
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    return message.get("result", {})
        def js(expression):
            result = call("Runtime.evaluate", dict(expression=expression, returnByValue=True, awaitPromise=True))
            if "exceptionDetails" in result:
                raise RuntimeError(result["exceptionDetails"])
            return result.get("result", {}).get("value")
        def wait(expression, seconds=90):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if js(expression):
                    return
                time.sleep(.2)
            message = js('document.getElementById("notice").textContent')
            raise AssertionError(f"UI timed out: {expression}; notice={message}")
        def shot(name):
            data = call("Page.captureScreenshot", {"format":"png", "captureBeyondViewport":True})["data"]
            (OUTPUT / name).write_bytes(base64.b64decode(data))
        call("Page.enable"); call("Runtime.enable")
        call("Emulation.setDeviceMetricsOverride", dict(width=1440, height=1050, deviceScaleFactor=1, mobile=False))
        call("Page.navigate", {"url":args.base_url})
        wait("document.querySelectorAll('#resource-rows tr').length > 0")
        assert js("document.getElementById('resource-count').textContent") == "137"
        shot("resources-desktop.png")
        js("document.querySelector('[data-view=explore]').click()")
        wait("document.querySelectorAll('#explore-content .stat').length === 4")
        assert js("document.getElementById('explore-content').textContent.includes('31,500')")
        assert js("document.querySelectorAll('#explore-content .month-bars i').length > 0")
        js("document.getElementById('learn').click()")
        wait("!document.getElementById('learn').disabled")
        assert js("!document.getElementById('view-explore').hidden && document.getElementById('notice').hidden")
        shot("explorer-desktop.png")
        if args.assistant or args.local_assistant:
            # Deep-link startup used to call initAssistant before its state existed;
            # model loading also used to make the workspace visible under chat.
            call("Page.navigate", {"url": args.base_url + "/#assistant"})
            wait("!document.getElementById('assistant-input').disabled")
            wait("document.querySelectorAll('#resource-rows tr').length > 0")
            assert js("document.getElementById('workspace').hidden")
            js("document.getElementById('assistant-input').value='How many cases and resources are in the whole selected log? Answer in one sentence.'; document.getElementById('assistant-composer').requestSubmit()")
            wait("!document.getElementById('assistant-input').disabled", 90)
            assert js("document.querySelector('.msg.assistant').textContent.includes('31,500')")
            assert js("document.querySelector('.msg.assistant').textContent.includes('149')")
            assert js("document.querySelectorAll('.confirm-card').length === 0")
            call("Page.reload")
            wait("!document.getElementById('assistant-input').disabled")
            assert js("document.querySelectorAll('.msg.assistant').length === 1")
            js("document.getElementById('assistant-input').value='Run the research simulator on BPIC_2017_W with 1 simulation.'; document.getElementById('assistant-composer').requestSubmit()")
            wait("document.querySelectorAll('.confirm-card').length === 1", 90)
            assert js("document.getElementById('assistant-input').disabled")
            shot("assistant-confirmation.png")
            js("document.querySelector('.confirm-card button.secondary').click()")
            wait("!document.getElementById('assistant-input').disabled")
            assert js("document.querySelectorAll('.confirm-card').length === 0")
            # A failed POST must stop the spinner, retain the unsent message, and
            # allow checking server state before another message can be submitted.
            js("window.savedFetch=window.fetch; window.fetch=(url,opts)=> { if(String(url).endsWith('/messages')) { window.fetch=window.savedFetch; return Promise.resolve(new Response(JSON.stringify({detail:'Injected connection failure'}),{status:503})); } return window.savedFetch(url,opts); }; document.getElementById('assistant-input').value='Message retained after failure'; document.getElementById('assistant-composer').requestSubmit()")
            wait("!document.getElementById('assistant-retry').hidden")
            assert js("document.getElementById('assistant-input').value === 'Message retained after failure'")
            assert js("!document.getElementById('assistant-typing')")
            js("document.getElementById('assistant-retry').click()")
            wait("!document.getElementById('assistant-input').disabled")
            shot("assistant-recovery.png")
        js("document.querySelector('[data-view=resources]').click()")
        js("document.querySelector('[data-select=\"r0\"]').click(); document.getElementById('clone-name').value='UI smoke resource'; document.getElementById('clone-form').requestSubmit()")
        wait("document.getElementById('change-count').textContent==='1'")
        js("document.querySelector('[data-view=scenario]').click(); document.getElementById('horizon').value='7'; document.getElementById('repetitions').value='2'; document.getElementById('scenario-name').value='BPI 2017 - UI verified scenario';")
        shot("scenario-desktop.png")
        js("document.getElementById('scenario-form').requestSubmit()")
        wait("!document.getElementById('view-results').hidden && !document.getElementById('results-content').hidden", 90)
        assert js("document.querySelectorAll('.result-card').length") == 6
        assert js("document.getElementById('notice').hidden")
        download = js("document.getElementById('scenario-download').href")
        response = httpx.get(download, timeout=10)
        assert response.status_code == 200 and "queue_seconds" in response.text.splitlines()[0]
        shot("results-desktop.png")
        js("document.querySelector('[data-view=history]').click()")
        wait("document.querySelectorAll('[data-result]').length>0")
        js("document.querySelector('[data-view=resources]').click()")
        call("Emulation.setDeviceMetricsOverride", dict(width=390, height=844, deviceScaleFactor=1, mobile=True))
        assert js("document.documentElement.scrollWidth <= window.innerWidth+1")
        shot("resources-mobile.png")
        js("document.querySelector('[data-view=explore]').click()")
        assert js("document.documentElement.scrollWidth <= window.innerWidth+1")
        shot("explorer-mobile.png")
        js("document.querySelector('[data-view=resources]').click()")
        call("Page.reload")
        wait("document.querySelectorAll('#resource-rows tr').length>0")
        assert js("document.getElementById('change-count').textContent") == '1'
        assert not errors, errors
        print(json.dumps(dict(status="passed", screenshots=str(OUTPUT), csv_bytes=len(response.content), browser_errors=errors)))
finally:
    try:
        process = psutil.Process(browser.pid)
        for child in process.children(recursive=True):
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        process.terminate()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    browser_log.close()
