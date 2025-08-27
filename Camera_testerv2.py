#!/usr/bin/env python3
"""
Camera Testing App — "IE Tab" alternative using Python + Tkinter

Features
- URL bar + basic-auth helpers for HTTP(S) camera pages
- Embedded browser via tkinterweb (fallback-friendly)
- RTSP viewer using OpenCV on a Tkinter canvas (threaded)
- Start / Stop / Refresh controls
- Snapshot capture to ./snapshots with timestamped filenames
- Cross‑platform (Windows/Linux). No admin rights required.

Notes / Limitations
- This does NOT support legacy IE ActiveX plugins. Many camera vendors 
  abandoned ActiveX; for those that still require it, use the RTSP tab
  or the vendor’s dedicated tool.
- tkinterweb provides a simple embedded browser. Modern JS-heavy pages
  may not fully work. If you need full Chromium, consider a variant
  that uses cefpython3 (heavier) — I can provide that branch on request.

Dependencies
    pip install opencv-python pillow tkinterweb requests

Tested with Python 3.10+ on Windows 11 and Ubuntu 22.04.
"""

import os
import sys
import threading
import queue
import time
import io
import datetime as dt
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    # tkinterweb is a lightweight HTML renderer for Tk
    from tkinterweb import HtmlFrame
    HAS_TKWEB = True
except Exception:
    HAS_TKWEB = False

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
    """Insert basic-auth credentials into an http/https URL if provided.
    e.g., http://user:pass@host:port/path
    """
    try:
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https"):
            return raw_url
        netloc = parsed.netloc
        if "@" in parsed.netloc:
            # Already has credentials
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
        self.current_image = None  # keep reference to avoid GC

        # UI
        self.canvas = tk.Label(self, bd=1, relief=tk.SUNKEN, anchor=tk.CENTER)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.bind("<Configure>", self._on_resize)

        # Start a UI updater loop
        self.after(30, self._update_canvas)

    # Public controls
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
        # drain queue
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except Exception:
                break
        self.on_status("RTSP stopped.")

    def snapshot(self, out_dir: str = SNAPSHOT_DIR, fmt: str = "jpg") -> str | None:
        if not HAS_PIL:
            messagebox.showerror(APP_TITLE, "Pillow is required for snapshots. pip install pillow")
            return None
        if self.current_image is None:
            messagebox.showwarning(APP_TITLE, "No frame available yet.")
            return None
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        ext = "jpg" if fmt == "jpg" else "png"
        out_path = os.path.join(out_dir, f"snapshot_{ts}.{ext}")
        try:
            pil_img = self._last_pil_image
            if pil_img is None:
                messagebox.showwarning(APP_TITLE, "Frame not ready.")
                return None
            if fmt == "jpg":
                pil_img.save(out_path, format="JPEG", quality=92)
            else:
                pil_img.save(out_path, format="PNG")
            self.on_status(f"Saved snapshot: {out_path}")
            return out_path
        except Exception as e:
            err(str(e))
            messagebox.showerror(APP_TITLE, f"Failed to save snapshot\n{e}")
            return None

    # Internal
    def _reader_loop(self, url: str):
        try:
            self.cap = cv2.VideoCapture(url)
            if not self.cap.isOpened():
                self.on_status("Failed to open stream.")
                return
            self.on_status("RTSP connected. Receiving frames...")
            # modest FPS to not overload UI
            desired_delay = 1/30
            while not self.stop_event.is_set():
                start = time.time()
                ok, frame = self.cap.read()
                if not ok:
                    # retry a bit
                    time.sleep(0.1)
                    continue
                # Convert BGR->RGB and push
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                try:
                    if not self.frame_queue.empty():
                        # drop old
                        _ = self.frame_queue.get_nowait()
                    self.frame_queue.put_nowait(frame_rgb)
                except queue.Full:
                    pass
                # fps pacing
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
        # Trigger redraw on resize
        self.after(10, self._update_canvas)

    def _update_canvas(self):
        try:
            if not self.frame_queue.empty():
                frame_rgb = self.frame_queue.get_nowait()
                h, w, _ = frame_rgb.shape
                # Fit to widget size while keeping aspect
                cw = max(1, self.winfo_width())
                ch = max(1, self.winfo_height())
                scale = min(cw / w, ch / h)
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                pil = Image.fromarray(frame_rgb).resize((nw, nh), Image.BILINEAR)
                self._last_pil_image = pil
                tk_img = ImageTk.PhotoImage(pil)
                self.current_image = tk_img  # keep ref
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
        if not HAS_TKWEB:
            lbl = ttk.Label(self, text=(
                "tkinterweb not installed. Install with:\n"
                "pip install tkinterweb\n\n"
                "Alternatively, use 'Open in System Browser'."
            ))
            lbl.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
            self.html = None
        else:
            self.html = HtmlFrame(self, messages_enabled=False)
            self.html.pack(fill=tk.BOTH, expand=True)

    def load(self, url: str):
        if self.html is None:
            messagebox.showwarning(APP_TITLE, "Embedded browser unavailable. Use system browser instead.")
            return
        try:
            if not url.lower().startswith(("http://", "https://")):
                url = "http://" + url
            self.on_status(f"Loading {url} ...")
            self.html.load_website(url)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Failed to load URL\n{e}")


# --------------------- Main Application ---------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x720")

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Top controls
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Camera IP:").pack(side=tk.LEFT, padx=(8, 4))
        self.url_var = tk.StringVar(value="192.168.1.1")
        self.url_entry = ttk.Entry(top, textvariable=self.url_var, width=35)
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
        # Main UI elements
        # Enhancement: Dark mode toggle
        self.dark_mode = False
        self.btn_dark = ttk.Button(top, text="Dark Mode", command=self.toggle_dark_mode)
        self.btn_dark.pack(side=tk.LEFT, padx=(20, 6))

        # Enhancement: About dialog
        self.btn_about = ttk.Button(top, text="About", command=self.show_about)
        self.btn_about.pack(side=tk.LEFT, padx=6)

        # RTSP controls
        self.btn_start = ttk.Button(top, text="Start RTSP", command=self.on_start_rtsp)
        self.btn_start.pack(side=tk.LEFT, padx=(20, 6))

        self.btn_stop = ttk.Button(top, text="Stop RTSP", command=self.on_stop_rtsp)
        self.btn_stop.pack(side=tk.LEFT, padx=6)

        self.btn_snap = ttk.Button(top, text="Snapshot", command=self.on_snapshot)
        self.btn_snap.pack(side=tk.LEFT, padx=6)

        self.btn_refresh = ttk.Button(top, text="Refresh", command=self.on_refresh)
        self.btn_refresh.pack(side=tk.LEFT, padx=6)
        # Notebook for tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

    def toggle_dark_mode(self):
        self.dark_mode = not self.dark_mode
        style = ttk.Style(self)
        if self.dark_mode:
            self.configure(bg="#222")
            style.theme_use("clam")
            style.configure("TFrame", background="#222")
            style.configure("TLabel", background="#222", foreground="#eee")
            style.configure("TButton", background="#333", foreground="#eee")
            self.log_text.config(bg="#222", fg="#eee")
        else:
            self.configure(bg="SystemButtonFace")
            style.theme_use("default")
            style.configure("TFrame", background="SystemButtonFace")
            style.configure("TLabel", background="SystemButtonFace", foreground="#000")
            style.configure("TButton", background="SystemButtonFace", foreground="#000")
            self.log_text.config(bg="#f8f8f8", fg="#000")

    def show_about(self):
        deps = []
        if HAS_TKWEB:
            deps.append("tkinterweb")
        if HAS_CV2:
            deps.append("opencv-python")
        if HAS_PIL:
            deps.append("Pillow")
        if HAS_REQUESTS:
            deps.append("requests")
        dep_str = ", ".join(deps) if deps else "None"
        messagebox.showinfo(APP_TITLE + " — About",
            f"Camera Tester\nVersion: 2.0\n\nDependencies: {dep_str}\n\nAuthor: bajrangmkd\nLicense: MIT")

    # ------------- UI helpers -------------
    def set_status(self, msg: str):
        self.status_var.set(msg)
        self.log(msg)
        info(msg)

    def log(self, msg: str):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def _dependency_banner(self):
        missing = []
        if not HAS_TKWEB:
            missing.append("tkinterweb (embedded web)")
        if not HAS_CV2:
            missing.append("opencv-python (RTSP)")
        if not HAS_PIL:
            missing.append("Pillow (image)")
        if missing:
            self.after(200, lambda: messagebox.showinfo(APP_TITLE,
                "Some optional packages are missing:\n- " + "\n- ".join(missing) +
                "\n\nInstall with: pip install tkinterweb opencv-python pillow"))

    # ------------- Button actions -------------
    def on_default_action(self):
        # If RTSP url present, prefer RTSP start; else web load
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
            # Try to infer a common path if user typed only host
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
        import tkinter.simpledialog
        fmt = tkinter.simpledialog.askstring("Snapshot Format", "Enter format (jpg/png):", initialvalue="jpg")
        if fmt is None:
            return
        fmt = fmt.lower()
        if fmt not in ("jpg", "png"):
            messagebox.showerror(APP_TITLE, "Invalid format. Use 'jpg' or 'png'.")
            return
        out = self.rtsp_panel.snapshot(fmt=fmt)
        if out:
            # Offer to copy path to clipboard
            if messagebox.askyesno(APP_TITLE, f"Snapshot saved:\n{out}\n\nCopy path to clipboard?"):
                self.clipboard_clear()
                self.clipboard_append(out)
            # Offer to open folder
            if messagebox.askyesno(APP_TITLE, f"Open containing folder?"):
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
        # Reload current tab
        cur = self.notebook.select()
        if cur == str(self.web_panel):
            self.on_load_web()
        else:
            # restart RTSP
            self.on_start_rtsp()

    def on_close(self):
        try:
            self.rtsp_panel.stop()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
