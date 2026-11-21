"""Onward.exe's own window: the app shows in a normal Windows window (Edge WebView2, through pywebview) instead
of a browser tab next to a console window. Closing the window stops Onward.

If the window can't be made (no WebView2 on an old PC, or pywebview missing), Onward opens in the browser as
before and keeps running. Running from source (start.bat) always uses the browser.

Onward.exe also puts an icon in the system tray (by the clock) with Open and Quit, so it can always be closed,
even when it ended up in the browser or a window got lost behind the sim."""
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


_icon = None


def _quit(log):
    log("Onward stopped.")
    try:
        if _icon:
            _icon.stop()  # take the icon out of the tray instead of leaving a ghost there
    except Exception:
        pass
    os._exit(0)


def tray(url, log=print, show=None):
    """The tray icon: Open Onward, Open in my browser, Quit Onward. show: brings the app window back (None when
    Onward runs in the browser). Never stops Onward from starting: without a tray, it simply has no icon."""
    global _icon
    if _icon:
        return _icon
    try:
        import pystray
        from PIL import Image
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        img = Image.open(os.path.join(base, "web", "favicon.png"))

        def open_app(icon=None, item=None):
            if show:
                try:
                    show()
                    return
                except Exception:
                    pass
            webbrowser.open(url)

        menu = pystray.Menu(
            pystray.MenuItem("Open Onward", open_app, default=True),
            pystray.MenuItem("Open in my browser", lambda icon, item: webbrowser.open(url)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Onward", lambda icon, item: _quit(log)))
        _icon = pystray.Icon("Onward", img, "Onward", menu)
        _icon.run_detached()
        return _icon
    except Exception as e:
        log(f"No tray icon ({e}). Close the Onward window, or use Quit Onward in Settings, to stop it.")
        return None


def run(url, log=print):
    """Show Onward in its own window until it's closed. Returns True when a window was shown and has now been
    closed (time to quit), False when Onward fell back to the browser (keep the server running)."""
    try:
        import webview
    except Exception as e:
        log(f"No app window ({e}). Opening Onward in your browser instead.")
        webbrowser.open(url)
        tray(url, log)
        return False
    for key in ("OPEN_EXTERNAL_LINKS_IN_BROWSER", "ALLOW_DOWNLOADS"):  # links to the web open in the browser; .pln downloads work
        try:
            webview.settings[key] = True
        except Exception:
            pass
    try:
        win = webview.create_window("Onward", url, width=1440, height=920, min_size=(1000, 680), text_select=True)
        # closing the main window stops Onward right away, even if something else (a cockpit window) is still open
        win.events.closed += lambda: _quit(log)

        def show():
            win.restore()
            win.show()
        tray(url, log, show)
        # Edge WebView2 only: without it, pywebview would fall back to the old Internet Explorer engine, which can't
        # run Onward's page. Better to open the browser then.
        # private_mode off and a storage folder, so the page remembers your map layers and choices between runs
        webview.start(gui="edgechromium", private_mode=False, storage_path=os.path.join(data.DATA, "window"))
        return True
    except Exception as e:
        log(f"Couldn't open the app window ({e}). Opening Onward in your browser instead.")
        webbrowser.open(url)
        tray(url, log)
        return False
