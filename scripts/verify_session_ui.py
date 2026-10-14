"""Check session navigation/playback in isolated Chrome with mock data, without Supabase."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

import websocket

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    browser_path = shutil.which("google-chrome") or "C:/Program Files/Google/Chrome/Application/chrome.exe"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="catchupai-ui-") as folder:
        folder = Path(folder)
        app = folder / "preview.py"
        app.write_text('''import sys
from pathlib import Path
sys.path.insert(0, ''' + repr(str(ROOT)) + ''')
sys.path.insert(0, ''' + repr(str(ROOT / "tests")) + ''')
import streamlit as st
from catchupai import config, database, templates, persistence, history
from test_session_navigation import FakeSessionStore, SESSION_ID
import base64, io, wave
config.SUPABASE_ENABLED = False
store = FakeSessionStore()
with io.BytesIO() as buffer:
    with wave.open(buffer,'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000); audio.writeframes(b'\\x00\\x00'*48000)
    audio_url = 'data:audio/wav;base64,'+base64.b64encode(buffer.getvalue()).decode()
store.signed_audio_url = lambda *args, **kwargs: audio_url
database.enabled = lambda: True
database.SupabaseStore = lambda: store
history.SupabaseStore = lambda: store
templates.enabled = lambda: False
persistence.resume_pending = lambda: None
if not st.session_state.get('preview_initialized'):
    st.session_state.update(saved_session_id=SESSION_ID, saved_session_data=history.load_session(SESSION_ID),
        saved_report_id='final', report_navigation='section:important_facts', report_section_choice='section:important_facts',
        page='Conversation Studio', preview_initialized=True)
namespace={'__name__':'__main__','__file__':''' + repr(str(ROOT / "app.py")) + '''}
exec(compile(Path(namespace['__file__']).read_text(encoding='utf-8'),namespace['__file__'],'exec'),namespace)
''', encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        import sys
        log = (folder / "server.log").open("w")
        server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(app),
            "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true",
            "--browser.gatherUsageStats=false"], cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
        browser = connection = None
        try:
            for _ in range(80):
                try:
                    with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1):
                        break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError("Preview server unavailable.")
            browser = subprocess.Popen([browser_path, "--headless=new", "--disable-gpu", "--disable-background-networking",
                "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
                f"--user-data-dir={folder / 'chrome'}", "about:blank"], stdout=log, stderr=log, creationflags=flags)
            active = folder / "chrome/DevToolsActivePort"
            for _ in range(80):
                if active.exists():
                    break
                time.sleep(.25)
            debug_port = active.read_text().splitlines()[0]
            with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=2) as response:
                target = next(item for item in json.load(response) if item["type"] == "page")
            connection = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
            sequence = 0
            def call(method, params=None):
                nonlocal sequence
                sequence += 1
                connection.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
                while True:
                    result = json.loads(connection.recv())
                    if result.get("id") == sequence:
                        if "error" in result:
                            raise RuntimeError(str(result["error"]))
                        return result.get("result", {})
            def js(expression):
                result = call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                    "awaitPromise": True, "userGesture": True})
                if result.get("exceptionDetails"):
                    raise RuntimeError(str(result["exceptionDetails"]))
                return result.get("result", {}).get("value")
            def wait_for(expression):
                for _ in range(150):
                    if js(expression):
                        return
                    time.sleep(.2)
                raise RuntimeError("Browser preview did not reach expected state: " + expression)
            call("Emulation.setDeviceMetricsOverride", {"width":1440, "height":900, "deviceScaleFactor":1, "mobile":False})
            call("Page.navigate", {"url": f"http://127.0.0.1:{port}"})
            wait_for("document.body.innerText.includes('Report - Important Facts')")
            js("window.previewPlayer = () => { for(const frame of document.querySelectorAll('iframe')) { "
               "try { const doc=frame.contentDocument; if(doc && doc.getElementById('audio')) return {frame,doc}; } "
               "catch(_) {} } return null; }")
            wait_for("Boolean(window.previewPlayer())")
            geometry = js("""(() => {
                const heading=document.querySelector('.saved-session-heading').getBoundingClientRect();
                const frame=window.previewPlayer().frame.getBoundingClientRect();
                const recent=document.querySelector('.recent-sessions-heading').getBoundingClientRect();
                const first=document.querySelector('[class*=st-key-open_session_]').getBoundingClientRect();
                return {audioBelowHeading:frame.top>=heading.bottom,recentRowsBelowLabel:first.top>=recent.bottom};
            })()""")
            assert all(geometry.values()), geometry
            menu_selector = ".st-key-session_options_00000000-0000-0000-0000-000000000001 button"
            assert js(f"!document.querySelector('{menu_selector}').innerText.includes('expand_more')")
            js(f"document.querySelector('{menu_selector}').click()")
            wait_for("document.querySelectorAll('[role=menuitem]').length === 1")
            assert js("document.querySelector('[role=menuitem]').innerText.trim() === 'Rename'")
            js("document.querySelector('[role=menuitem]').click()")
            wait_for("document.querySelector('.st-key-rename_session_dialog input')?.value === 'Client interview'")
            dialog_width = js("document.querySelector('[role=dialog]').getBoundingClientRect().width")
            assert dialog_width <= 500, dialog_width
            js("document.querySelector('.st-key-cancel_session_rename button').click()")
            wait_for("!document.querySelector('.st-key-rename_session_dialog')")
            wait_for("Boolean(window.previewPlayer())")
            if args.screenshot:
                args.screenshot.write_bytes(base64.b64decode(call("Page.captureScreenshot", {"format":"png"})["data"]))
            playback = js("""(async () => {
                const {frame,doc}=window.previewPlayer();
                doc.getElementById('play').click();
                await new Promise(resolve=>setTimeout(resolve,450));
                const audio=doc.getElementById('audio');
                const playing=!audio.paused && audio.currentTime>0;
                const pauseIconVisible=frame.contentWindow.getComputedStyle(doc.getElementById('pause-icon')).display !== 'none';
                const playIconHidden=frame.contentWindow.getComputedStyle(doc.getElementById('play-icon')).display === 'none';
                audio.pause();
                await new Promise(resolve=>setTimeout(resolve,100));
                const playIconVisible=frame.contentWindow.getComputedStyle(doc.getElementById('play-icon')).display !== 'none';
                const pauseIconHidden=frame.contentWindow.getComputedStyle(doc.getElementById('pause-icon')).display === 'none';
                const seek=doc.getElementById('seek'); seek.value='1';
                seek.dispatchEvent(new frame.contentWindow.Event('input'));
                seek.dispatchEvent(new frame.contentWindow.Event('change'));
                return {playing,pauseIconVisible,playIconHidden,playIconVisible,pauseIconHidden,seekSeconds:audio.currentTime};
            })()""")
            assert playback["playing"] and playback["seekSeconds"] == 1, playback
            assert all(playback[key] for key in ("pauseIconVisible", "playIconHidden", "playIconVisible", "pauseIconHidden")), playback
            js("document.querySelector('.st-key-nav_payments button').click()")
            wait_for("document.body.innerText.includes('Final estimated cost')")
            assert js("Boolean(document.querySelector('.saved-session-heading')) && !document.body.innerText.includes('Record or Upload a Conversation')")
            js("document.querySelector('.st-key-new_session button').click()")
            wait_for("document.body.innerText.includes('Record or Upload a Conversation')")
            assert js("!document.querySelector('.saved-session-heading')")
            js("document.querySelector('.st-key-nav_payments button').click()")
            wait_for("document.body.innerText.includes('Your audio costs will appear here')")
            assert js("document.body.innerText.includes('Record or Upload a Conversation')")
            print("Browser checks passed: rename-only menu without chevron, styled dialog, playback and session navigation.")
        finally:
            if connection:
                try:
                    connection.send(json.dumps({"id":99999,"method":"Browser.close"}))
                except Exception:
                    pass
                connection.close()
            if browser:
                try:
                    browser.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    browser.terminate()
                    browser.wait(timeout=10)
            server.terminate()
            server.wait(timeout=10)
            log.close()


if __name__ == "__main__":
    main()
