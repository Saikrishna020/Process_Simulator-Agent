"""Local Edge/CDP smoke test. Start the app on 127.0.0.1:8000 first.

Uses installed httpx/websockets/psutil. Screenshots and browser profile remain
under runs/ui-checks. Exercises the real model, forms, job polling and results.
"""
import base64
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
        call("Page.navigate", {"url":"http://127.0.0.1:8000"})
        wait("document.querySelectorAll('#resource-rows tr').length > 0")
        assert js("document.getElementById('resource-count').textContent") == "137"
        shot("resources-desktop.png")
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
