"""Onward.exe's own window: the app shows in a normal Windows window (Edge WebView2, through pywebview) instead
of a browser tab next to a console window. Closing the window stops Onward.

If the window can't be made (no WebView2 on an old PC, or pywebview missing), Onward opens in the browser as
before and keeps running. Running from source (start.bat) always uses the browser."""
import os
import sys
import webbrowser

import data


def message(text, title="Onward", error=False):
    """A Windows message box: the packaged app has no console window to print to."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(text), title, 0x10 if error else 0x40)
            return
        except Exception:
            pass
    print(text, flush=True)


def run(url, log=print):
    """Show Onward in its own window until it's closed. Returns True when a window was shown and has now been
    closed (time to quit), False when Onward fell back to the browser (keep the server running)."""
    try:
        import webview
    except Exception as e:
        log(f"No app window ({e}). Opening Onward in your browser instead.")
        webbrowser.open(url)
        return False
    for key in ("OPEN_EXTERNAL_LINKS_IN_BROWSER", "ALLOW_DOWNLOADS"):  # links to the web open in the browser; .pln downloads work
        try:
            webview.settings[key] = True
        except Exception:
            pass
    try:
        webview.create_window("Onward", url, width=1440, height=920, min_size=(1000, 680), text_select=True)
        # private_mode off and a storage folder, so the page remembers your map layers and choices between runs
        webview.start(private_mode=False, storage_path=os.path.join(data.DATA, "window"))
        return True
    except Exception as e:
        log(f"Couldn't open the app window ({e}). Opening Onward in your browser instead.")
        webbrowser.open(url)
        return False
