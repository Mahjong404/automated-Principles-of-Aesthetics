from contextlib import contextmanager

from playwright.sync_api import Error

from course_browser import STUDY, site_path


@contextmanager
def background_playback(page):
    session = None
    on_load = None
    bundle_scripts = set()
    remove_current = None
    scanning = False
    try:
        session = page.context.new_cdp_session(page)

        def record_script(event):
            if site_path(event.get("url", "")).endswith("/all-classes.js"):
                bundle_scripts.add(event["scriptId"])
            else:
                bundle_scripts.discard(event["scriptId"])

        session.on("Debugger.scriptParsed", record_script)

        def remove_current():
            nonlocal scanning
            if scanning or not site_path(page.url).endswith(STUDY):
                return
            active = page.locator("li[id^=dct].active").filter(has_text="视频")
            if active.count() != 1:
                return
            scanning = True
            group = {"objectGroup": "course-mouseout"}
            try:
                bundle_scripts.clear()
                session.send("Debugger.enable")
                session.send("Debugger.setSkipAllPauses", {"skip": True})
                window = session.send("Runtime.evaluate", {"expression": "window", **group})["result"]["objectId"]
                listeners = session.send("DOMDebugger.getEventListeners", {"objectId": window})["listeners"]
                for item in listeners:
                    if item["type"] != "mouseout" or item["scriptId"] not in bundle_scripts:
                        continue
                    handler = item.get("handler", {}).get("objectId")
                    if handler:
                        session.send("Runtime.callFunctionOn", {
                            "objectId": window,
                            "functionDeclaration": """function(callback, capture) {
                                this.removeEventListener('mouseout', callback, capture);
                                if (this.onmouseout === callback) this.onmouseout = null;
                            }""",
                            "arguments": [{"objectId": handler}, {"value": item["useCapture"]}],
                        })
            finally:
                try:
                    # The work iframe can pause page execution while a debugger is enabled.
                    session.send("Debugger.disable")
                finally:
                    scanning = False
                    session.send("Runtime.releaseObjectGroup", group)

        def on_load(_):
            try:
                remove_current()
            except Error:
                # Navigation may have replaced the document again. Recheck before playback.
                pass

        page.on("load", on_load)
        session.send("Emulation.setFocusEmulationEnabled", {"enabled": True})

    except Error:
        print("当前 Edge 未能启用完整后台播放保护")

    try:
        yield remove_current
    finally:
        if session:
            if on_load:
                page.remove_listener("load", on_load)
            try:
                session.send("Emulation.setFocusEmulationEnabled", {"enabled": False})
            except Error:
                pass
            try:
                session.detach()
            except Error:
                pass
