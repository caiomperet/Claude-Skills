"""
Toastmasters Timer Cam
======================
A Windows desktop app that publishes a 1920x1080 @ 30fps virtual webcam feed
showing a large MM:SS timer over a Toastmasters-style colored background
(black -> green -> yellow -> red -> flashing red), with a small speech-type
label in the bottom-left corner. Run alongside Zoom (or any conferencing app)
and select "OBS Virtual Camera" as the camera input.

Setup
-----
1. Install OBS Studio from https://obsproject.com/ . Open OBS once and click
   "Start Virtual Camera" (Tools menu, or the Controls dock). This registers
   the OBS Virtual Camera DirectShow device on Windows. You can stop OBS
   afterwards; the device stays installed.
2. Install Python 3.9+ and the dependencies:
       pip install -r requirements.txt
3. Run:
       python toastmasters_timer.py
4. In Zoom -> Settings -> Video, set Camera to "OBS Virtual Camera".

Usage
-----
- Pick a speech type (Prepared Speech / Evaluation / Table Topics / Custom).
- For Custom, fill in green/yellow/red thresholds in seconds (flash starts
  automatically 30 s after red).
- Click Start. Pause/Resume freezes the timer. Reset zeroes it (background
  goes back to black, the speech-type label stays). Stop Broadcast closes
  the virtual camera and the app.
"""

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk
import pyvirtualcam


WIDTH, HEIGHT = 1920, 1080
FPS = 30
FLASH_OFFSET = 30  # seconds after red threshold

PRESETS = {
    "Prepared Speech": (300, 360, 420),
    "Evaluation":      (120, 150, 180),
    "Table Topics":    (60,  90,  120),
}

COLOR_BLACK  = (0,   0,   0)
COLOR_GREEN  = (0,   170, 0)
COLOR_YELLOW = (204, 170, 0)
COLOR_RED    = (204, 0,   0)


class SharedState:
    """Thread-safe state shared between the Tk UI thread and camera thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.start_monotonic = None   # time.monotonic() at last resume, or None
        self.accumulated = 0.0        # seconds counted before current resume
        self.thresholds = None        # (green, yellow, red) seconds
        self.speech_label = ""
        self.label_visible = False
        self.max_color = 0            # 0 black, 1 green, 2 yellow, 3 red, 4 flash
        self.broadcast = True
        self.latest_frame = None      # PIL.Image, latest rendered frame (preview)
        self.cam_status = "starting"  # "starting" | "ok" | "error: <msg>"

    def elapsed_locked(self):
        """Caller must hold self.lock."""
        if self.running and self.start_monotonic is not None:
            return self.accumulated + (time.monotonic() - self.start_monotonic)
        return self.accumulated

    def elapsed(self):
        with self.lock:
            return self.elapsed_locked()


def load_font(size):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "arialbd.ttf",
        "Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def compute_level(elapsed, thresholds):
    """Return color level 0..4 from elapsed seconds and (g, y, r) thresholds."""
    if thresholds is None:
        return 0
    g, y, r = thresholds
    if elapsed >= r + FLASH_OFFSET:
        return 4
    if elapsed >= r:
        return 3
    if elapsed >= y:
        return 2
    if elapsed >= g:
        return 1
    return 0


def background_for_level(level):
    if level == 1:
        return COLOR_GREEN
    if level == 2:
        return COLOR_YELLOW
    if level == 3:
        return COLOR_RED
    return COLOR_BLACK


def render_frame(state, big_font, small_font):
    with state.lock:
        elapsed = state.elapsed_locked()
        thresholds = state.thresholds
        label = state.speech_label if state.label_visible else ""
        level = compute_level(elapsed, thresholds)
        if level > state.max_color:
            state.max_color = level
        level = state.max_color

    if level == 4:
        # 1-second alternating red / black
        bg = COLOR_RED if int(time.monotonic()) % 2 == 0 else COLOR_BLACK
    else:
        bg = background_for_level(level)

    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)

    minutes, seconds = divmod(int(elapsed), 60)
    timer_text = f"{minutes:02d}:{seconds:02d}"

    bbox = draw.textbbox((0, 0), timer_text, font=big_font, stroke_width=6)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (WIDTH - tw) // 2 - bbox[0]
    ty = (HEIGHT - th) // 2 - bbox[1]
    # White fill with a dark stroke keeps the timer readable on every background.
    draw.text(
        (tx, ty),
        timer_text,
        font=big_font,
        fill=(255, 255, 255),
        stroke_width=6,
        stroke_fill=(0, 0, 0),
    )

    if label:
        draw.text(
            (40, HEIGHT - 80),
            label,
            font=small_font,
            fill=(255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )

    return img


def camera_loop(state):
    big_font = load_font(300)
    small_font = load_font(40)
    try:
        with pyvirtualcam.Camera(width=WIDTH, height=HEIGHT, fps=FPS) as cam:
            with state.lock:
                state.cam_status = "ok"
            print(f"[camera] running on {cam.device} @ {WIDTH}x{HEIGHT} {FPS}fps")
            while True:
                with state.lock:
                    if not state.broadcast:
                        break
                img = render_frame(state, big_font, small_font)
                cam.send(np.asarray(img))
                state.latest_frame = img
                cam.sleep_until_next_frame()
    except Exception as exc:
        msg = f"error: {exc}"
        print(f"[camera] {msg}")
        with state.lock:
            state.cam_status = msg


class App:
    def __init__(self, root, state):
        self.root = root
        self.state = state
        self._preview_imgtk = None
        root.title("Toastmasters Timer Cam")
        root.protocol("WM_DELETE_WINDOW", self.on_stop_broadcast)

        self.speech_var = tk.StringVar(value="")

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Speech type:").grid(row=0, column=0, sticky="w")
        types_frame = ttk.Frame(outer)
        types_frame.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 6))
        for i, t in enumerate(list(PRESETS.keys()) + ["Custom"]):
            ttk.Radiobutton(
                types_frame, text=t, value=t, variable=self.speech_var,
                command=self.on_type_change,
            ).grid(row=0, column=i, sticky="w", padx=(0, 10))

        self.custom_frame = ttk.Frame(outer)
        self.custom_frame.grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 6))
        self.green_var = tk.StringVar(value="60")
        self.yellow_var = tk.StringVar(value="90")
        self.red_var = tk.StringVar(value="120")
        ttk.Label(self.custom_frame, text="Green (s):").grid(row=0, column=0)
        ttk.Entry(self.custom_frame, textvariable=self.green_var, width=6).grid(row=0, column=1, padx=(2, 10))
        ttk.Label(self.custom_frame, text="Yellow (s):").grid(row=0, column=2)
        ttk.Entry(self.custom_frame, textvariable=self.yellow_var, width=6).grid(row=0, column=3, padx=(2, 10))
        ttk.Label(self.custom_frame, text="Red (s):").grid(row=0, column=4)
        ttk.Entry(self.custom_frame, textvariable=self.red_var, width=6).grid(row=0, column=5, padx=(2, 10))
        ttk.Label(self.custom_frame, text="(Flash = Red + 30 s)").grid(row=0, column=6, padx=(4, 0))
        self.custom_frame.grid_remove()

        btn_frame = ttk.Frame(outer)
        btn_frame.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 8))
        self.start_btn = ttk.Button(btn_frame, text="Start", command=self.on_start, state="disabled")
        self.start_btn.grid(row=0, column=0, padx=2)
        self.pause_btn = ttk.Button(btn_frame, text="Pause", command=self.on_pause_resume, state="disabled")
        self.pause_btn.grid(row=0, column=1, padx=2)
        self.reset_btn = ttk.Button(btn_frame, text="Reset", command=self.on_reset, state="disabled")
        self.reset_btn.grid(row=0, column=2, padx=2)
        self.stop_btn = ttk.Button(btn_frame, text="Stop Broadcast", command=self.on_stop_broadcast)
        self.stop_btn.grid(row=0, column=3, padx=2)

        self.timer_label = ttk.Label(outer, text="00:00", font=("Arial", 36, "bold"))
        self.timer_label.grid(row=4, column=0, columnspan=4, pady=(2, 6))

        self.status_label = ttk.Label(outer, text="Camera: starting...")
        self.status_label.grid(row=5, column=0, columnspan=4, sticky="w")

        self.preview_label = ttk.Label(outer, relief="sunken")
        self.preview_label.grid(row=6, column=0, columnspan=4, pady=(6, 0))

        self.tick_timer()
        self.tick_preview()

    def on_type_change(self):
        t = self.speech_var.get()
        if t == "Custom":
            self.custom_frame.grid()
        else:
            self.custom_frame.grid_remove()
        if t:
            self.start_btn.config(state="normal")
            with self.state.lock:
                self.state.speech_label = t
                self.state.label_visible = True

    def _read_thresholds(self):
        t = self.speech_var.get()
        if t in PRESETS:
            return PRESETS[t]
        if t == "Custom":
            try:
                g = int(self.green_var.get())
                y = int(self.yellow_var.get())
                r = int(self.red_var.get())
            except ValueError:
                messagebox.showerror("Invalid input", "Custom thresholds must be whole numbers.")
                return None
            if not (0 <= g < y < r):
                messagebox.showerror("Invalid input", "Need 0 <= green < yellow < red.")
                return None
            return (g, y, r)
        return None

    def on_start(self):
        thresholds = self._read_thresholds()
        if thresholds is None:
            return
        with self.state.lock:
            self.state.thresholds = thresholds
            if not self.state.running:
                self.state.start_monotonic = time.monotonic()
                self.state.running = True
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal", text="Pause")
        self.reset_btn.config(state="normal")

    def on_pause_resume(self):
        with self.state.lock:
            if self.state.running:
                self.state.accumulated += time.monotonic() - self.state.start_monotonic
                self.state.start_monotonic = None
                self.state.running = False
                paused = True
            else:
                self.state.start_monotonic = time.monotonic()
                self.state.running = True
                paused = False
        self.pause_btn.config(text="Resume" if paused else "Pause")

    def on_reset(self):
        with self.state.lock:
            self.state.running = False
            self.state.start_monotonic = None
            self.state.accumulated = 0.0
            self.state.max_color = 0
            # speech_label / label_visible / thresholds preserved
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="Pause")

    def on_stop_broadcast(self):
        with self.state.lock:
            self.state.broadcast = False
        self.root.after(250, self.root.destroy)

    def tick_timer(self):
        elapsed = self.state.elapsed()
        m, s = divmod(int(elapsed), 60)
        self.timer_label.config(text=f"{m:02d}:{s:02d}")
        with self.state.lock:
            status = self.state.cam_status
        if status == "ok":
            self.status_label.config(text="Camera: live (OBS Virtual Camera)")
        elif status == "starting":
            self.status_label.config(text="Camera: starting...")
        else:
            self.status_label.config(text=f"Camera: {status}")
        self.root.after(200, self.tick_timer)

    def tick_preview(self):
        img = self.state.latest_frame
        if img is not None:
            thumb = img.copy()
            thumb.thumbnail((480, 270))
            self._preview_imgtk = ImageTk.PhotoImage(thumb)
            self.preview_label.config(image=self._preview_imgtk)
        self.root.after(500, self.tick_preview)


def main():
    state = SharedState()
    cam_thread = threading.Thread(target=camera_loop, args=(state,), daemon=True)
    cam_thread.start()

    root = tk.Tk()
    App(root, state)
    root.mainloop()

    with state.lock:
        state.broadcast = False
    cam_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
