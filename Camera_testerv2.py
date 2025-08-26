#!/usr/bin/env python3
"""
Camera Testing App — "IE Tab" alternative using Python + Tkinter + CEF

Features
- URL bar + basic-auth helpers for HTTP(S) camera pages
- Embedded browser via cefpython3 (modern Chromium rendering)
- RTSP viewer using OpenCV on a Tkinter canvas (threaded)
- Start / Stop / Refresh controls
- Snapshot capture to ./snapshots with timestamped filenames
- Cross‑platform (Windows/Linux). No admin rights required.

Notes / Limitations
- This does NOT support legacy IE ActiveX plugins. Use RTSP or vendor tools for ActiveX-dependent cameras.
- cefpython3 is heavier than tkinterweb but supports modern web pages.
- Install cefpython3 with: pip install cefpython3

Dependencies
    pip install cefpython3 opencv-python pillow requests

Tested with Python 3.10+ on Windows 11 and Ubuntu 22.04.
"""

import os
import sys
import threading
import queue
import time
import datetime as dt
from urllib.parse import urlparse, urlunparse

import tkinter as tk
from tkinter import ttk, messagebox

try:
    from cefpython3 import cefpython as cef
    HAS_CEF = True
except Exception:
    HAS_CEF = False

try:
    import cv2
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    import requests
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

APP_TITLE = "Camera Tester (IE Tab alternative)"
SNAPSHOT_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)


# --------------------- Helper Utilities ---------------------

def info(msg: str):
    print(f"[INFO] {msg}")

def warn(msg: str):
    print(f"[WARN] {msg}")

def err(msg: str):
    print(f"[ERROR] {msg}", file=sys.stderr)

def build_basic_auth_url(raw_url: str, username: str, password: str) -> str:
    """Insert basic-auth credentials into an http/https URL if provided."""
    try:
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https"):
            return raw_url
        netloc = parsed.netloc
        if "@" in parsed.netloc:
            return raw_url
        creds = ""
        if username:
            creds = username
            if password:
                creds += f":{password}"
            netloc = f"{creds}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            new_url = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
            return new_url
        return raw_url
    except Exception:
        return raw_url

def build_rtsp_url(raw_url: str, username: str, password: str) -> str:
    """Ensure RTSP URL includes credentials if provided and not already present."""
    try:
        parsed = urlparse(raw_url)
        if parsed.scheme != "rtsp":
            return raw_url
        if parsed.username or parsed.password:
            return raw_url
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        userinfo = ""
        if username:
            userinfo = username
            if password:
                userinfo += f":{password}"
            userinfo += "@"
        path = parsed.path or "/"
        query = f"?{parsed.query}" if parsed.query else ""
        frag = f"#{parsed.fragment}" if parsed.fragment else ""
        return f"rtsp://{userinfo}{host}{port}{path}{query}{frag}"
    except Exception:
        return raw_url


# --------------------- RTSP Player ---------------------

class RTSPPlayer(ttk.Frame):
    def __init__(self, master, on_status=None):
        super().__init__(master)
        self.on_status = on_status or (lambda m: None)
        self.cap = None
        self.thread = None
        self.stop_event = threading.Event()
        self.frame_queue = queue.Queue(maxsize=2)
        self.current_image = None
        self._last_pil_image = None

        self.canvas = tk.Label(self, bd=1, relief=tk.SUNKEN, anchor=tk.CENTER)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.bind("<Configure>", self._on_resize)
        self.after(30, self._update_canvas)

    def start(self, url: str):
        if not HAS_CV2 or not HAS_PIL:
            messagebox.showerror(APP_TITLE, "OpenCV and Pillow are required for RTSP playback.\nInstall with: pip install opencv-python pillow")
            return
        self.stop()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._reader_loop, args=(url,), daemon=True)
        self.thread.start()
        self.on_status(f"Connecting to {url} ...")

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            try:
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None
            except Exception:
                pass
            self.thread.join(timeout=2)
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except Exception:
                break
        self.on_status("RTSP stopped.")

    def snapshot(self, out_dir: str = SNAPSHOT_DIR) -> str | None:
        if not HAS_PIL:
            messagebox.showerror(APP_TITLE, "Pillow is required for snapshots. pip install pillow")
            return None
        if self.current_image is None:
            messagebox.showwarning(APP_TITLE, "No frame available yet.")
            return None
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = os.path.join(out_dir, f"snapshot_{ts}.jpg")
        try:
            pil_img = self._last_pil_image
            if pil_img is None:
                messagebox.showwarning(APP_TITLE, "Frame not ready.")
                return None
            pil_img.save(out_path, format="JPEG", quality=92)
            self.on_status(f"Saved snapshot: {out_path}")
            return out_path
        except Exception as e:
            err(str(e))
            messagebox.showerror(APP_TITLE, f"Failed to save snapshot\n{e}")
            return None

    def _reader_loop(self, url: str):
        try:
            self.cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not self.cap.isOpened():
                self.on_status("Failed to open stream.")
                return
            self.on_status("RTSP connected. Receiving frames...")
            desired_delay = 1/30
            while not self.stop_event.is_set():
                start = time.time()
                ok, frame = self.cap.read()
                if not ok:
                    time.sleep(0.1)
                    continue
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                try:
                    if not self.frame_queue.empty():
                        _ = self.frame_queue.get_nowait()
                    self.frame_queue.put_nowait(frame_rgb)
                except queue.Full:
                    pass
                elapsed = time.time() - start
                if elapsed < desired_delay:
                    time.sleep(desired_delay - elapsed)
        except Exception as e:
            self.on_status(f"Reader error: {e}")
        finally:
            try:
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None
            except Exception:
                pass

    def _on_resize(self, event):
        self.after(10, self._update_canvas)

    def _update_canvas(self):
        try:
            if not self.frame_queue.empty():
                frame_rgb = self.frame_queue.get_nowait()
                h, w, _ = frame_rgb.shape
                cw = max(1, self.winfo_width())
                ch = max(1, self.winfo_height())
                scale = min(cw / w, ch / h)
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                pil = Image.fromarray(frame_rgb).resize((nw, nh), Image.BILINEAR)
                self._last_pil_image = pil
                tk_img = ImageTk.PhotoImage(pil)
                self.current_image = tk_img
                self.canvas.configure(image=tk_img)
        except Exception as e:
            err(f"Canvas update error: {e}")
        finally:
            self.after(30, self._update_canvas)


# --------------------- Web Panel ---------------------

class WebPanel(ttk.Frame):
    def __init__(self, master, on_status=None):
        super().__init__(master)
        self.on_status = on_status or (lambda m: None)
        self.browser = None
        self.cef_initialized = False

        if not HAS_CEF:
            lbl = ttk.Label(self, text=(
                "cefpython3 not installed. Install with:\n"
                "pip install cefpython3\n\n"
                "Alternatively, use 'Open in System Browser'."
            ))
            lbl.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        else:
            self._init_cef()

        self.pack(fill=tk.BOTH, expand=True)

    def _init_cef(self):
        if self.cef_initialized:
            return
        try:
            sys.excepthook = cef.ExceptHook
            cef.Initialize(settings={"windowless_rendering_enabled": False})
            self.cef_initialized = True
            window_info = cef.WindowInfo()
            window_info.SetAsChild(self.winfo_id())
            self.browser = cef.CreateBrowserSync(
                window_info,
                url="about:blank",
                settings={"javascript_enabled": True}
            )
            self.bind("<Configure>", self._on_resize)
        except Exception as e:
            err(f"CEF initialization failed: {e}")
            messagebox.showerror(APP_TITLE, f"Failed to initialize CEF browser\n{e}")

    def load(self, url: str):
        if not HAS_CEF:
            messagebox.showwarning(APP_TITLE, "Embedded browser unavailable. Use system browser instead.")
            return
        if not self.cef_initialized:
            self._init_cef()
        if self.browser:
            try:
                if not url.lower().startswith(("http://", "https://")):
                    url = "https://" + url
                self.on_status(f"Loading {url} ...")
                self.browser.Navigate(url)
            except Exception as e:
                messagebox.showerror(APP_TITLE, f"Failed to load URL\n{e}")

    def _on_resize(self, event):
        if self.browser:
            try:
                self.browser.NotifyMoveOrResize()
            except Exception:
                pass

    def destroy(self):
        if self.browser:
            try:
                self.browser.CloseBrowser(True)
                self.browser = None
            except Exception:
                pass
        if self.cef_initialized:
            try:
                cef.Shutdown()
            except Exception:
                pass
        super().destroy()


# --------------------- Main Application ---------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x720")

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Camera IP:").pack(side=tk.LEFT, padx=(8, 4))
        self.url_var = tk.StringVar(value="192.168.1.1")
        self.url_entry = ttk.Entry(top, textvariable=self.url_var, width=60)
        self.url_entry.pack(side=tk.LEFT, padx=4, pady=8)

        ttk.Label(top, text="User:").pack(side=tk.LEFT, padx=(12, 4))
        self.user_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.user_var, width=12).pack(side=tk.LEFT)

        ttk.Label(top, text="Pass:").pack(side=tk.LEFT, padx=(6, 4))
        self.pass_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.pass_var, width=12, show="*").pack(side=tk.LEFT)

        self.btn_load = ttk.Button(top, text="Load (Web)", command=self.on_load_web)
        self.btn_load.pack(side=tk.LEFT, padx=6)

        self.btn_open_default = ttk.Button(top, text="Open in System Browser", command=self.on_open_external)
        self.btn_open_default.pack(side=tk.LEFT, padx=6)

        self.btn_start = ttk.Button(top, text="Start RTSP", command=self.on_start_rtsp)
        self.btn_start.pack(side=tk.LEFT, padx=(20, 6))

        self.btn_stop = ttk.Button(top, text="Stop RTSP", command=self.on_stop_rtsp)
        self.btn_stop.pack(side=tk.LEFT, padx=6)

        self.btn_snap = ttk.Button(top, text="Snapshot", command=self.on_snapshot)
        self.btn_snap.pack(side=tk.LEFT, padx=6)

        self.btn_refresh = ttk.Button(top, text="Refresh", command=self.on_refresh)
        self.btn_refresh.pack(side=tk.LEFT, padx=6)

        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(self, textvariable=self.status_var, anchor=tk.W, relief=tk.SUNKEN)
        status.pack(side=tk.BOTTOM, fill=tk.X)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.web_panel = WebPanel(self.notebook, on_status=self.set_status)
        self.rtsp_panel = RTSPPlayer(self.notebook, on_status=self.set_status)

        self.notebook.add(self.web_panel, text="Web (HTTP/HTTPS)")
        self.notebook.add(self.rtsp_panel, text="RTSP Player")

        self.bind("<Return>", lambda e: self.on_default_action())
        self.bind("<Control-r>", lambda e: self.on_refresh())

        self._dependency_banner()

    def set_status(self, msg: str):
        self.status_var.set(msg)
        info(msg)

    def _dependency_banner(self):
        missing = []
        if not HAS_CEF:
            missing.append("cefpython3 (embedded web)")
        if not HAS_CV2:
            missing.append("opencv-python (RTSP)")
        if not HAS_PIL:
            missing.append("Pillow (image)")
        if missing:
            self.after(200, lambda: messagebox.showinfo(APP_TITLE,
                "Some optional packages are missing:\n- " + "\n- ".join(missing) +
                "\n\nInstall with: pip install cefpython3 opencv-python pillow"))

    def on_default_action(self):
        url = self.url_var.get().strip()
        if url.lower().startswith("rtsp://"):
            self.on_start_rtsp()
        else:
            self.on_load_web()

    def on_load_web(self):
        url = self.url_var.get().strip()
        user = self.user_var.get().strip()
        pwd = self.pass_var.get().strip()
        url2 = build_basic_auth_url(url, user, pwd)
        self.notebook.select(self.web_panel)
        self.web_panel.load(url2)

    def on_open_external(self):
        import webbrowser
        url = self.url_var.get().strip()
        user = self.user_var.get().strip()
        pwd = self.pass_var.get().strip()
        url2 = build_basic_auth_url(url, user, pwd)
        self.set_status(f"Opening in system browser: {url2}")
        webbrowser.open(url2)

    def on_start_rtsp(self):
        url = self.url_var.get().strip()
        user = self.user_var.get().strip()
        pwd = self.pass_var.get().strip()
        if not url.lower().startswith("rtsp://"):
            if "://" not in url:
                url = f"rtsp://{url}:554/"
            else:
                messagebox.showwarning(APP_TITLE, "URL does not look like RTSP. It should start with rtsp://")
        rtsp_url = build_rtsp_url(url, user, pwd)
        self.notebook.select(self.rtsp_panel)
        self.rtsp_panel.start(rtsp_url)

    def on_stop_rtsp(self):
        self.rtsp_panel.stop()

    def on_snapshot(self):
        out = self.rtsp_panel.snapshot()
        if out:
            if messagebox.askyesno(APP_TITLE, f"Snapshot saved:\n{out}\n\nOpen folder?"):
                folder = os.path.dirname(out)
                try:
                    if sys.platform.startswith("win"):
                        os.startfile(folder)
                    elif sys.platform == "darwin":
                        os.system(f"open '{folder}'")
                    else:
                        os.system(f"xdg-open '{folder}'")
                except Exception:
                    pass

    def on_refresh(self):
        cur = self.notebook.select()
        if cur == str(self.web_panel):
            self.on_load_web()
        else:
            self.on_start_rtsp()

    def on_close(self):
        try:
            self.rtsp_panel.stop()
            self.web_panel.destroy()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()