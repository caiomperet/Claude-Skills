#!/usr/bin/env python3
"""
Interface gráfica do netmon (Tkinter, sem dependências externas).

Ao abrir, inicia o monitor em segundo plano se ele não estiver rodando, mostra
o estado atual, o resumo dos últimos 7 dias e permite abrir o relatório,
ajustar as poucas configurações relevantes e ligar o início automático com o
Windows. A aba Histórico traz um gráfico navegável de todas as variáveis
medidas. Fechar a janela não para o monitor.

  python netmon_gui.py            abre a interface
  python netmon_gui.py --install  cria atalhos, liga o início automático,
                                  inicia o monitor e abre a interface
  python netmon_gui.py <comando>  qualquer outro argumento é repassado ao
                                  netmon.py (usado pelo executável instalado)
"""
from __future__ import annotations

import datetime as dt
import os
import statistics
import subprocess
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netmon  # noqa: E402

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # pragma: no cover
    tk = None

SIZE_PRESETS = [
    ("Leve (5 MB + 2 MB)", 5_000_000, 2_000_000),
    ("Padrão (10 MB + 4 MB)", 10_000_000, 4_000_000),
    ("Completo (25 MB + 10 MB)", 25_000_000, 10_000_000),
]
INTERVAL_PRESETS = [("15 min", 900), ("30 min", 1800), ("1 hora", 3600), ("2 horas", 7200)]


def fmt(v, digits=1, suffix=""):
    return "-" if v is None else f"{v:.{digits}f}{suffix}"


def open_path(path):
    if netmon.IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
    elif netmon.IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


SERIES = [
    # chave, rótulo, cor, unidade, tipo de painel
    ("down", "Download (Mbps)", "#1f77b4", "Mbps", "line"),
    ("up", "Upload (Mbps)", "#ff7f0e", "Mbps", "line"),
    ("rtt", "Latência (ms)", "#2ca02c", "ms", "line"),
    ("jitter", "Jitter (ms)", "#17becf", "ms", "line"),
    ("loss", "Perda de pacotes (%)", "#d62728", "%", "area"),
    ("gw_loss", "Perda no 1º salto (%)", "#9467bd", "%", "area"),
    ("hop2_loss", "Perda no 2º salto (%)", "#c026d3", "%", "area"),
    ("dns", "DNS (ms)", "#8c564b", "ms", "line"),
    ("avail", "Disponibilidade por dia (%)", "#16a34a", "%", "bars"),
    ("call_rtt", "Latência 1/s (ms)", "#0d9488", "ms", "line"),
    ("bursts", "Travamentos (s)", "#b91c1c", "s", "spikes"),
    ("marks", "Marcações (espaço)", "#7c3aed", "", "overlay"),
]
DEFAULT_SERIES = {"down", "up", "rtt", "loss", "gw_loss", "hop2_loss", "bursts", "marks"}
PERIODS = [("1 dia", 1), ("3 dias", 3), ("7 dias", 7), ("15 dias", 15)]
DAY = 86400.0


def nice_max(v):
    if not v or v <= 0:
        return 1.0
    mag = 10 ** (len(str(int(v))) - 1) if v >= 1 else 0.1
    for mult in (1, 2, 2.5, 5, 10):
        if v <= mag * mult:
            return mag * mult
    return v


class HistoryChart(ttk.Frame):
    """Gráfico de histórico em Canvas: painéis empilhados, um por variável,
    escala vertical automática por painel, eixo de tempo compartilhado,
    navegação por botões, arrasto do mouse e roda (zoom)."""

    def __init__(self, parent, store, cfg):
        super().__init__(parent)
        self.store = store
        self.cfg = cfg
        self.days = 7
        self.t1 = time.time()
        self.follow = True          # janela termina em "agora" e acompanha
        self.data = None
        self.loading = False
        self.dirty = True
        self.enabled = {k: tk.BooleanVar(value=k in DEFAULT_SERIES) for k, *_ in SERIES}
        self._drag_x = None
        self._panels = []
        self._build()
        self.after(200, self.reload)
        self.after(60000, self._auto_refresh)

    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Button(bar, text="◀", width=3, command=lambda: self.shift(-0.5)).pack(side="left")
        ttk.Button(bar, text="▶", width=3, command=lambda: self.shift(0.5)).pack(side="left", padx=(2, 6))
        ttk.Button(bar, text="Agora", command=self.go_now).pack(side="left")
        ttk.Label(bar, text="   Janela:").pack(side="left")
        self.period = ttk.Combobox(bar, values=[n for n, _ in PERIODS], state="readonly", width=8)
        self.period.set("7 dias")
        self.period.bind("<<ComboboxSelected>>", lambda e: self.set_days(dict(PERIODS)[self.period.get()]))
        self.period.pack(side="left", padx=4)
        self.range_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.range_var, foreground="#6b7280").pack(side="left", padx=10)
        ttk.Label(bar, text="arraste para navegar, roda do mouse para aproximar", foreground="#9ca3af",
                  font=("", 8)).pack(side="right")

        toggles = ttk.Frame(self)
        toggles.pack(fill="x", padx=8, pady=(0, 4))
        for i, (key, label, color, _, _) in enumerate(SERIES):
            cb = tk.Checkbutton(toggles, text=label, variable=self.enabled[key], fg=color,
                                activeforeground=color, command=self.redraw, anchor="w")
            cb.grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 10))

        self.canvas = tk.Canvas(self, background="#ffffff", highlightthickness=1, highlightbackground="#e5e7eb")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.canvas.bind("<Configure>", lambda e: self._schedule_redraw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("hover"))
        self.canvas.bind("<MouseWheel>", self._wheel)           # Windows e macOS
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 1))   # Linux
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1))

    # ---- navegação -------------------------------------------------------

    @property
    def t0(self):
        return self.t1 - self.days * DAY

    def set_days(self, days):
        self.days = float(days)
        if self.follow:
            self.t1 = time.time()
        self.reload()

    def shift(self, fraction):
        self.follow = False
        self.t1 = min(time.time(), self.t1 + fraction * self.days * DAY)
        if abs(self.t1 - time.time()) < 60:
            self.follow = True
        self.reload()

    def go_now(self):
        self.follow = True
        self.t1 = time.time()
        self.reload()

    def _press(self, event):
        self._drag_x = event.x
        self._drag_t1 = self.t1

    def _drag(self, event):
        if self._drag_x is None:
            return
        w = max(1, self.canvas.winfo_width() - self._ml - self._mr)
        dt_s = -(event.x - self._drag_x) / w * self.days * DAY
        self.t1 = min(time.time(), self._drag_t1 + dt_s)
        self.follow = abs(self.t1 - time.time()) < 60
        self._schedule_redraw()

    def _release(self, event):
        self._drag_x = None
        self.reload()

    def _wheel(self, event, direction=None):
        if direction is None:
            direction = 1 if event.delta > 0 else -1
        factor = 0.8 if direction > 0 else 1.25
        w = max(1, self.canvas.winfo_width() - self._ml - self._mr)
        frac = min(1, max(0, (event.x - self._ml) / w))
        anchor = self.t0 + frac * self.days * DAY
        new_days = min(30.0, max(1 / 24, self.days * factor))
        self.days = new_days
        self.t1 = min(time.time(), anchor + (1 - frac) * new_days * DAY)
        self.follow = abs(self.t1 - time.time()) < 60
        self.period.set(next((n for n, d in PERIODS if d == round(new_days, 3)), f"{new_days:.1f} d"))
        self.reload()

    # ---- dados -----------------------------------------------------------

    def reload(self):
        if self.loading:
            self.dirty = True
            return
        self.loading = True
        t0, t1 = self.t0, self.t1
        width = max(200, self.canvas.winfo_width())

        def work():
            try:
                data = self._load(t0, t1, width)
                err = None
            except Exception as exc:  # noqa: BLE001
                data, err = None, exc
            self.after(0, lambda: self._loaded(data, err))

        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, data, err):
        self.loading = False
        if err:
            self.range_var.set(f"erro ao carregar: {err}")
        else:
            self.data = data
            self.redraw()
        if self.dirty:
            self.dirty = False
            self.reload()

    def _auto_refresh(self):
        if self.follow and self.winfo_ismapped():
            self.t1 = time.time()
            self.reload()
        self.after(60000, self._auto_refresh)

    def _load(self, t0, t1, width):
        q = self.store.query
        buckets = max(60, int(width / 3))
        bucket_s = (t1 - t0) / buckets
        out = {"t0": t0, "t1": t1, "bucket_s": bucket_s}

        for key, direction in (("down", "download"), ("up", "upload")):
            rows = q("SELECT ts, mbps FROM speed WHERE ok=1 AND direction=? AND ts BETWEEN ? AND ? ORDER BY ts",
                     (direction, t0, t1))
            out[key] = [(r["ts"], r["mbps"]) for r in rows]

        # pings: agrega por ciclo e depois por balde (mediana de latência, máximo de perda)
        day0 = dt.datetime.fromtimestamp(t0).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day1 = dt.datetime.fromtimestamp(t1).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + DAY
        pings = q("SELECT cycle, host, kind, loss_pct, rtt_avg, jitter, received, during_speed FROM ping "
                  "WHERE ts BETWEEN ? AND ? ORDER BY cycle", (day0, day1))
        cycles = netmon.aggregate_cycles(pings)
        acc = {}
        for c in cycles:
            if not (t0 <= c["ts"] <= t1):
                continue
            b = int((c["ts"] - t0) / bucket_s)
            a = acc.setdefault(b, {"rtt": [], "jitter": [], "loss": [], "gw_loss": [], "hop2_loss": []})
            if not c["during_speed"]:
                if c["rtt"] is not None:
                    a["rtt"].append(c["rtt"])
                if c["jitter"] is not None:
                    a["jitter"].append(c["jitter"])
                a["loss"].append(c["loss"])
            if c["gw_loss"] is not None:
                a["gw_loss"].append(c["gw_loss"])
            if c["hop2_loss"] is not None:
                a["hop2_loss"].append(c["hop2_loss"])
        for key, agg in (("rtt", statistics.median), ("jitter", statistics.median), ("loss", max),
                         ("gw_loss", max), ("hop2_loss", max)):
            out[key] = [(t0 + (b + 0.5) * bucket_s, agg(a[key])) for b, a in sorted(acc.items()) if a[key]]

        # disponibilidade por dia (dia local inteiro, mesmo que a janela o corte)
        by_day = {}
        for c in cycles:
            d = dt.datetime.fromtimestamp(c["ts"]).date()
            tot, down = by_day.get(d, (0, 0))
            by_day[d] = (tot + 1, down + (1 if c["down"] else 0))
        out["avail"] = [(dt.datetime.combine(d, dt.time.min).timestamp(), 100.0 * (1 - dn / tot), tot)
                        for d, (tot, dn) in sorted(by_day.items()) if tot]

        cm = q("SELECT ts, rtt_avg FROM call_minute WHERE kind='internet' AND rtt_avg IS NOT NULL "
               "AND COALESCE(suspect,0)=0 AND ts BETWEEN ? AND ? ORDER BY ts", (t0, t1))
        acc = {}
        for r in cm:
            acc.setdefault(int((r["ts"] - t0) / bucket_s), []).append(r["rtt_avg"])
        out["call_rtt"] = [(t0 + (b + 0.5) * bucket_s, max(v)) for b, v in sorted(acc.items())]
        out["bursts"] = [(r["ts"], r["duration_s"] or 0, r["host"], r["kind"]) for r in
                         q("SELECT ts, duration_s, host, kind FROM call_burst WHERE COALESCE(suspect,0)=0 "
                           "AND ts BETWEEN ? AND ? ORDER BY ts", (t0, t1))]

        out["marks"] = [(r["ts"], r["note"]) for r in
                        q("SELECT ts, note FROM marks WHERE ts BETWEEN ? AND ? ORDER BY ts", (t0, t1))]

        dns = q("SELECT ts, ms FROM dns WHERE ok=1 AND ts BETWEEN ? AND ? ORDER BY ts", (t0, t1))
        acc = {}
        for r in dns:
            acc.setdefault(int((r["ts"] - t0) / bucket_s), []).append(r["ms"])
        out["dns"] = [(t0 + (b + 0.5) * bucket_s, statistics.median(v)) for b, v in sorted(acc.items())]
        return out

    # ---- desenho ---------------------------------------------------------

    _ml, _mr, _mt, _mb = 62, 14, 6, 26

    def _schedule_redraw(self):
        if getattr(self, "_redraw_job", None):
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(40, self.redraw)

    def _fmt_range(self):
        a, b = dt.datetime.fromtimestamp(self.t0), dt.datetime.fromtimestamp(self.t1)
        return f"{a.strftime('%d/%m %H:%M')} a {b.strftime('%d/%m %H:%M')}" + ("  (ao vivo)" if self.follow else "")

    def redraw(self):
        self._redraw_job = None
        cv = self.canvas
        cv.delete("all")
        self._panels = []
        self._marks = []
        self.range_var.set(self._fmt_range())
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 50 or H < 50:
            return
        active = [s for s in SERIES if self.enabled[s[0]].get() and s[4] != "overlay"]
        show_marks = self.enabled["marks"].get()
        if not active:
            cv.create_text(W / 2, H / 2, text="Selecione ao menos uma variável acima.", fill="#6b7280")
            return
        if self.data is None:
            cv.create_text(W / 2, H / 2, text="Carregando...", fill="#6b7280")
            return
        t0, t1 = self.t0, self.t1
        span = max(1.0, t1 - t0)
        ml, mr, mt, mb = self._ml, self._mr, self._mt, self._mb
        pw = W - ml - mr
        title_h = 16
        gap = 8
        ph = (H - mt - mb - len(active) * (title_h + gap)) / len(active)
        if ph < 30:
            ph = 30

        def X(ts):
            return ml + (ts - t0) / span * pw

        # eixo de tempo (compartilhado)
        step, fmt_t = (3 * 3600, "%Hh") if self.days <= 1.5 else \
                      (12 * 3600, "%d/%m %Hh") if self.days <= 4 else (DAY, "%d/%m")
        first = dt.datetime.fromtimestamp(t0).replace(minute=0, second=0, microsecond=0)
        if step >= DAY:
            first = first.replace(hour=0)
        elif step >= 3 * 3600:
            first = first.replace(hour=first.hour - first.hour % (step // 3600))
        ts = first.timestamp()
        ticks = []
        while ts <= t1:
            if ts >= t0:
                ticks.append(ts)
            ts += step
        y_bottom = mt + len(active) * (title_h + gap + ph) - gap
        for ts in ticks:
            x = X(ts)
            cv.create_line(x, mt, x, y_bottom, fill="#eef0f3")
            cv.create_text(x, y_bottom + 12, text=dt.datetime.fromtimestamp(ts).strftime(fmt_t),
                           fill="#6b7280", font=("", 8))

        y = mt
        for key, label, color, unit, kind in active:
            pts = self.data.get(key, [])
            vals = [p[1] for p in pts]
            top = y + title_h
            bottom = top + ph
            if kind == "spikes":
                y_min, y_max = 0.0, nice_max(max(vals) if vals else 5)
                if y_max < 5:
                    y_max = 5.0
            elif kind == "bars":
                lo = min(vals) if vals else 90
                y_min = 0 if lo < 50 else min(90.0, float(int(lo)))
                y_max = 100.0
            else:
                y_min = 0.0
                y_max = nice_max(max(vals) if vals else 1)
                if unit == "%":
                    y_max = min(100.0, y_max)
            rng = max(y_max - y_min, 1e-9)

            def Y(v, top=top, bottom=bottom, y_min=y_min, rng=rng):
                return bottom - (min(max(v, y_min), y_min + rng) - y_min) / rng * (bottom - top)

            stats = ""
            if vals and kind != "spikes":
                stats = (f"   mín {min(vals):.1f}   mediana {statistics.median(vals):.1f}   máx {max(vals):.1f} {unit}"
                         if kind != "bars" else f"   média {statistics.fmean(vals):.2f}%")
            cv.create_text(ml, y + 3, text=label + stats, anchor="nw", fill=color, font=("", 9, "bold"))
            cv.create_rectangle(ml, top, ml + pw, bottom, outline="#e5e7eb")
            for i in range(3):
                v = y_min + rng * i / 2
                yy = Y(v)
                cv.create_line(ml, yy, ml + pw, yy, fill="#eef0f3")
                cv.create_text(ml - 6, yy, text=f"{v:g}", anchor="e", fill="#6b7280", font=("", 8))
            cv.create_text(ml - 6, top - 1, text=f"{y_max:g}", anchor="e", fill="#6b7280", font=("", 8))

            if kind == "spikes":
                inet = [p for p in pts if p[3] == "internet"]
                if not inet and pts:
                    inet = pts
                stats_txt = (f"   {len(inet)} travamentos, total {sum(p[1] for p in inet):.0f} s, "
                             f"mais longo {max((p[1] for p in inet), default=0):.0f} s") if pts else "   nenhum no período"
                cv.create_rectangle(ml, y, ml + 420, y + title_h, fill="#ffffff", outline="")
                cv.create_text(ml, y + 3, text=label + stats_txt, anchor="nw", fill=color, font=("", 9, "bold"))
                for ts, v, host, k in pts:
                    x = X(ts)
                    col = color if k == "internet" else "#9467bd"
                    cv.create_line(x, bottom, x, Y(v), fill=col, width=2)
                    cv.create_oval(x - 2.5, Y(v) - 2.5, x + 2.5, Y(v) + 2.5, fill=col, outline="")
                pts = [(ts, v) for ts, v, _, _ in pts]
            elif kind == "bars":
                for ts, v, n in pts:
                    x1, x2 = max(ml, X(ts)), min(ml + pw, X(ts + DAY))
                    if x2 <= x1:
                        continue
                    fill = color if v >= 99.5 else ("#f59e0b" if v >= 97 else "#dc2626")
                    cv.create_rectangle(x1 + 1, Y(v), x2 - 1, bottom, fill=fill, outline="")
                    if x2 - x1 > 34:
                        cv.create_text((x1 + x2) / 2, max(top + 8, Y(v) - 8), text=f"{v:.2f}%",
                                       fill="#374151", font=("", 8))
            else:
                gap_s = 4 * max(self.data["bucket_s"], self.cfg["speed"]["interval_s"]
                                if key in ("down", "up") else self.data["bucket_s"])
                seg, prev = [], None
                segments = []
                for ts, v in pts:
                    if prev is not None and ts - prev > gap_s and seg:
                        segments.append(seg)
                        seg = []
                    seg.append((ts, v))
                    prev = ts
                if seg:
                    segments.append(seg)
                for sg in segments:
                    coords = [c for ts, v in sg for c in (X(ts), Y(v))]
                    if len(sg) == 1:
                        x, yy = coords
                        cv.create_oval(x - 2, yy - 2, x + 2, yy + 2, fill=color, outline="")
                        continue
                    if kind == "area":
                        cv.create_polygon([X(sg[0][0]), bottom] + coords + [X(sg[-1][0]), bottom],
                                          fill=color, outline="", stipple="gray25")
                    cv.create_line(*coords, fill=color, width=1.5)
                if key in ("down", "up"):
                    plan = self.cfg["plan"].get(f"{'download' if key == 'down' else 'upload'}_mbps") or 0
                    if plan and y_min <= plan <= y_max:
                        cv.create_line(ml, Y(plan), ml + pw, Y(plan), fill="#6b7280", dash=(6, 4))
                        cv.create_text(ml + pw - 4, Y(plan) - 7, text=f"plano {plan:g}", anchor="e",
                                       fill="#6b7280", font=("", 8))
            self._panels.append({"key": key, "label": label, "unit": unit, "top": top, "bottom": bottom,
                                 "pts": pts, "color": color, "kind": kind})
            y = bottom + gap

        self._marks = self.data.get("marks", []) if show_marks else []
        if self._marks and self._panels:
            top_y, bot_y = self._panels[0]["top"], self._panels[-1]["bottom"]
            for ts, note in self._marks:
                x = X(ts)
                cv.create_line(x, top_y, x, bot_y, fill="#7c3aed", dash=(4, 3), width=1.5)
                cv.create_polygon(x - 5, top_y - 8, x + 5, top_y - 8, x, top_y, fill="#7c3aed", outline="")

    def _hover(self, event):
        cv = self.canvas
        cv.delete("hover")
        if not self._panels or self._drag_x is not None:
            return
        W = cv.winfo_width()
        ml, pw = self._ml, W - self._ml - self._mr
        if not (ml <= event.x <= ml + pw):
            return
        t = self.t0 + (event.x - ml) / pw * (self.t1 - self.t0)
        cv.create_line(event.x, self._panels[0]["top"], event.x, self._panels[-1]["bottom"], fill="#9ca3af",
                       dash=(2, 2), tags="hover")
        if self._marks:
            near = min(self._marks, key=lambda m: abs(m[0] - t))
            if abs(near[0] - t) < (self.t1 - self.t0) / 100:
                txt = "Marcação " + dt.datetime.fromtimestamp(near[0]).strftime("%d/%m %H:%M:%S") + \
                      (f": {near[1]}" if near[1] else "")
                tid = cv.create_text(event.x + 10 if event.x < W - 260 else event.x - 10, self._panels[0]["top"] - 4,
                                     text=txt, anchor="sw" if event.x < W - 260 else "se", fill="#7c3aed",
                                     font=("", 9, "bold"), tags="hover")
                bbox = cv.bbox(tid)
                cv.create_rectangle(bbox[0] - 3, bbox[1] - 2, bbox[2] + 3, bbox[3] + 2, fill="#ffffff",
                                    outline="#e5e7eb", tags="hover")
                cv.tag_raise(tid)
        for p in self._panels:
            if not p["pts"]:
                continue
            if p["kind"] == "bars":
                near = next((q for q in p["pts"] if q[0] <= t < q[0] + DAY), None)
                if near:
                    txt = f"{dt.datetime.fromtimestamp(near[0]).strftime('%d/%m')}: {near[1]:.2f}% ({near[2]} ciclos)"
                else:
                    continue
            else:
                near = min(p["pts"], key=lambda q: abs(q[0] - t))
                if abs(near[0] - t) > (self.t1 - self.t0) / 20:
                    continue
                txt = f"{dt.datetime.fromtimestamp(near[0]).strftime('%d/%m %H:%M')}: {near[1]:.1f} {p['unit']}"
            x = event.x + 10 if event.x < W - 200 else event.x - 10
            anchor = "w" if event.x < W - 200 else "e"
            tid = cv.create_text(x, p["top"] + 10, text=txt, anchor=anchor, fill=p["color"], font=("", 9, "bold"),
                                 tags="hover")
            bbox = cv.bbox(tid)
            cv.create_rectangle(bbox[0] - 3, bbox[1] - 2, bbox[2] + 3, bbox[3] + 2, fill="#ffffff", outline="#e5e7eb",
                                tags="hover")
            cv.tag_raise(tid)


class App:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.store = netmon.Store(cfg["db_path"])
        self.busy = False
        root.title(f"netmon {netmon.VERSION}: qualidade da internet")
        root.minsize(900, 680)
        self.update_info = None
        try:
            ttk.Style().theme_use("vista" if netmon.IS_WINDOWS else "clam")
        except tk.TclError:
            pass
        self._build()
        # Botões e caixas de seleção não podem receber foco de teclado, senão a
        # barra de espaço os aciona em vez de marcar. option_add não vale para
        # widgets ttk, então ajustamos widget a widget.
        self._disable_focus(root)
        root.bind_all("<space>", self._on_space)
        root.after(200, lambda: root.focus_set())
        self._ensure_running(auto=True)
        self._tick_status()
        self._tick_data()
        self._tick_summary()
        if cfg.get("update", {}).get("check_on_start", True):
            threading.Thread(target=self._check_update_bg, daemon=True).start()

    # ---- construção ------------------------------------------------------

    def _build(self):
        pad = {"padx": 12, "pady": 6}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        panel = ttk.Frame(self.nb)
        self.nb.add(panel, text="  Painel  ")
        self.chart = HistoryChart(self.nb, self.store, self.cfg)
        self.nb.add(self.chart, text="  Histórico  ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.chart.reload() if self.nb.index("current") == 1 else None)
        self.btn_update = ttk.Button(top, text="Atualizar", command=self.do_update)
        self.btn_update.pack(side="right", padx=(6, 0))
        self.btn_update.pack_forget()
        self.dot = tk.Canvas(top, width=16, height=16, highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 14, 14, fill="#9ca3af", outline="")
        self.dot.pack(side="left")
        self.status_var = tk.StringVar(value="Verificando...")
        ttk.Label(top, textvariable=self.status_var, font=("", 11, "bold")).pack(side="left", padx=8)
        self.btn_toggle = ttk.Button(top, text="Parar", command=self.toggle)
        self.btn_toggle.pack(side="right")
        self.btn_call = ttk.Button(top, text="Ligar modo chamada", command=self.toggle_call)
        self.btn_call.pack(side="right", padx=6)
        ttk.Button(top, text="Testar velocidade", command=self.test_now).pack(side="right", padx=6)

        tiles = ttk.LabelFrame(panel, text="Agora")
        tiles.pack(fill="x", **pad)
        self.tiles = {}
        for i, (key, title) in enumerate([("ping", "Internet"), ("gw", "Rede local"), ("today", "Últimas 24 h"),
                                          ("down", "Download"), ("up", "Upload"), ("call", "Modo chamada")]):
            f = ttk.Frame(tiles, padding=(10, 4))
            f.grid(row=i // 3, column=i % 3, sticky="nsew")
            tiles.columnconfigure(i % 3, weight=1)
            ttk.Label(f, text=title.upper(), foreground="#6b7280", font=("", 8)).pack(anchor="w")
            v = ttk.Label(f, text="-", font=("", 14, "bold"))
            v.pack(anchor="w")
            s = ttk.Label(f, text="", foreground="#6b7280", font=("", 8))
            s.pack(anchor="w")
            self.tiles[key] = (v, s)

        marksf = ttk.LabelFrame(panel, text="Travamentos que você marcou")
        marksf.pack(fill="x", **pad)
        mrow = ttk.Frame(marksf)
        mrow.pack(fill="x", padx=10, pady=(8, 2))
        self.btn_mark = ttk.Button(mrow, text="Marcar travamento agora  (barra de espaço)", command=self.mark_now)
        self.btn_mark.pack(side="left")
        self.mark_var = tk.StringVar(value="nenhuma marcação hoje")
        ttk.Label(mrow, textvariable=self.mark_var, foreground="#6b7280").pack(side="left", padx=12)
        self.marks_tree = ttk.Treeview(marksf, columns=("hora", "o"), show="headings", height=4)
        self.marks_tree.heading("hora", text="Quando")
        self.marks_tree.heading("o", text="O que o monitor viu nesse instante")
        self.marks_tree.column("hora", width=120, stretch=False, anchor="w")
        self.marks_tree.column("o", width=700, anchor="w")
        for tag, color in (("critico", "#b91c1c"), ("atencao", "#b45309"), ("ok", "#166534")):
            self.marks_tree.tag_configure(tag, foreground=color)
        self.marks_tree.pack(fill="x", padx=10, pady=(2, 10))

        summ = ttk.LabelFrame(panel, text="Últimos 7 dias")
        summ.pack(fill="x", **pad)
        self.summary_var = tk.StringVar(value="Calculando...")
        ttk.Label(summ, textvariable=self.summary_var, justify="left", font=("Consolas" if netmon.IS_WINDOWS
                                                                              else "TkFixedFont", 9)).pack(anchor="w", padx=10, pady=(6, 2))
        self.conclusion_var = tk.StringVar(value="")
        self.conclusion = ttk.Label(summ, textvariable=self.conclusion_var, wraplength=700, justify="left")
        self.conclusion.pack(anchor="w", padx=10, pady=(2, 8))
        row = ttk.Frame(summ)
        row.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(row, text="Ver histórico", command=lambda: self.nb.select(1)).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Abrir relatório (7 dias)", command=lambda: self.report(7)).pack(side="left")
        ttk.Button(row, text="Relatório (30 dias)", command=lambda: self.report(30)).pack(side="left", padx=6)
        ttk.Button(row, text="Exportar CSV", command=self.export_csv).pack(side="left")
        ttk.Button(row, text="Abrir pasta de dados", command=lambda: open_path(self.cfg["_dir"])).pack(side="left", padx=6)

        conf = ttk.LabelFrame(panel, text="Configurações")
        conf.pack(fill="x", **pad)
        g = ttk.Frame(conf, padding=(10, 6))
        g.pack(fill="x")
        ttk.Label(g, text="Plano contratado: download").grid(row=0, column=0, sticky="w")
        self.plan_down = ttk.Spinbox(g, from_=0, to=10000, increment=10, width=7)
        self.plan_down.grid(row=0, column=1, sticky="w", padx=(6, 2))
        ttk.Label(g, text="Mbps    upload").grid(row=0, column=2, sticky="w")
        self.plan_up = ttk.Spinbox(g, from_=0, to=10000, increment=10, width=7)
        self.plan_up.grid(row=0, column=3, sticky="w", padx=(6, 2))
        ttk.Label(g, text="Mbps").grid(row=0, column=4, sticky="w")

        ttk.Label(g, text="Teste de velocidade a cada").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.interval = ttk.Combobox(g, values=[n for n, _ in INTERVAL_PRESETS], state="readonly", width=10)
        self.interval.grid(row=1, column=1, sticky="w", padx=(6, 2), pady=(6, 0))
        ttk.Label(g, text="tamanho").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.size = ttk.Combobox(g, values=[n for n, _, _ in SIZE_PRESETS], state="readonly", width=24)
        self.size.grid(row=1, column=3, columnspan=2, sticky="w", padx=(6, 2), pady=(6, 0))

        ttk.Label(g, text="Sem teste de velocidade entre").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.quiet = ttk.Entry(g, width=34)
        self.quiet.grid(row=2, column=1, columnspan=4, sticky="w", padx=(6, 2), pady=(6, 0))
        ttk.Label(g, text="ex.: 09:00-12:00, 14:00-18:00 (horários de reunião; pings continuam)",
                  foreground="#6b7280", font=("", 8)).grid(row=3, column=1, columnspan=4, sticky="w", padx=(6, 2))

        ttk.Label(g, text="Saltos da rede local").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.hops_entry = ttk.Entry(g, width=34)
        self.hops_entry.grid(row=4, column=1, columnspan=4, sticky="w", padx=(6, 2), pady=(6, 0))
        ttk.Label(g, text="vazio = descobre sozinho; ex.: 192.168.68.1, 10.0.0.1 (roteador do mesh e da operadora)",
                  foreground="#6b7280", font=("", 8)).grid(row=5, column=1, columnspan=4, sticky="w", padx=(6, 2))
        ttk.Label(g, text="Modo chamada automático").grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.call_sched = ttk.Entry(g, width=34)
        self.call_sched.grid(row=6, column=1, columnspan=4, sticky="w", padx=(6, 2), pady=(6, 0))
        ttk.Label(g, text="ex.: seg-sex 08:00-18:00 (ping 1/s e sem teste de velocidade nesses horários)",
                  foreground="#6b7280", font=("", 8)).grid(row=7, column=1, columnspan=4, sticky="w", padx=(6, 2))
        self.call_skip_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="Suspender testes de velocidade durante o modo chamada",
                        variable=self.call_skip_var).grid(row=8, column=0, columnspan=4, sticky="w", pady=(6, 0))

        self.autostart_var = tk.BooleanVar(value=netmon.autostart_enabled())
        cb = ttk.Checkbutton(g, text="Iniciar o monitor junto com o Windows", variable=self.autostart_var)
        cb.grid(row=9, column=0, columnspan=3, sticky="w", pady=(6, 0))
        if not netmon.IS_WINDOWS:
            cb.state(["disabled"])
        ttk.Button(g, text="Salvar", command=self.save).grid(row=9, column=3, columnspan=2, sticky="e", pady=(6, 0))
        self.budget_var = tk.StringVar()
        ttk.Label(g, textvariable=self.budget_var, foreground="#6b7280", font=("", 8)).grid(
            row=10, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self._load_settings()

        logf = ttk.LabelFrame(panel, text="Registro")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=6, font=("Consolas" if netmon.IS_WINDOWS else "TkFixedFont", 8),
                           state="disabled", wrap="none", relief="flat", background="#f9fafb")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

        self.msg_var = tk.StringVar(value=f"Dados em {self.cfg['_dir']}")
        ttk.Label(self.root, textvariable=self.msg_var, foreground="#6b7280", font=("", 8)).pack(
            anchor="w", padx=12, pady=(0, 8))

    def _load_settings(self):
        c = self.cfg
        self.plan_down.set(int(c["plan"]["download_mbps"] or 0))
        self.plan_up.set(int(c["plan"]["upload_mbps"] or 0))
        iv = c["speed"]["interval_s"]
        names = [n for n, s in INTERVAL_PRESETS if s == iv]
        self.interval.set(names[0] if names else f"{iv // 60} min")
        db = c["speed"]["download_bytes"]
        names = [n for n, d, _ in SIZE_PRESETS if d == db]
        self.size.set(names[0] if names else SIZE_PRESETS[1][0])
        self.quiet.delete(0, "end")
        self.quiet.insert(0, ", ".join(c["speed"].get("quiet_hours", [])))
        self.call_sched.delete(0, "end")
        self.call_sched.insert(0, ", ".join(c["call_mode"].get("schedule", [])))
        hops = c["ping"].get("local_hops")
        self.hops_entry.delete(0, "end")
        self.hops_entry.insert(0, ", ".join(hops) if isinstance(hops, list) else "")
        self.call_skip_var.set(bool(c["call_mode"].get("skip_speed", True)))
        self._update_budget_label()

    def _update_budget_label(self):
        try:
            iv = dict(INTERVAL_PRESETS).get(self.interval.get(), self.cfg["speed"]["interval_s"])
            sz = {n: (d, u) for n, d, u in SIZE_PRESETS}.get(self.size.get(),
                                                             (self.cfg["speed"]["download_bytes"],
                                                              self.cfg["speed"]["upload_bytes"]))
            state = netmon.load_state(self.cfg)
            real_down = int(state.get("download_bytes") or sz[0])
            budget = (self.cfg["speed"].get("daily_budget_mb") or 0) * 1e6
            per_test = real_down + sz[1]
            if budget:
                iv = max(iv, per_test / (budget / 86400.0))
            per_day = per_test * (86400 / iv) / 1e6
            cresceu = ("  O teste de download cresceu para "
                       f"{real_down / 1e6:.0f} MB para medir a sua velocidade." if real_down > sz[0] else "")
            self.budget_var.set(f"Consumo máximo dos testes: {per_day:.0f} MB por dia "
                                f"(~{per_day * 30 / 1000:.1f} GB por mês), a cada {iv / 60:.0f} min. "
                                f"Os testes são adiados quando a conexão está em uso.{cresceu}")
        except Exception:  # noqa: BLE001
            self.budget_var.set("")

    # ---- ações -----------------------------------------------------------

    def _run_bg(self, fn, done=None):
        if self.busy:
            return
        self.busy = True

        def work():
            try:
                result = fn()
                err = None
            except Exception as exc:  # noqa: BLE001
                result, err = None, exc
            self.root.after(0, lambda: self._finish(done, result, err))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, done, result, err):
        self.busy = False
        if err:
            messagebox.showerror("netmon", str(err))
        elif done:
            done(result)
        self._tick_status(reschedule=False)

    def _ensure_running(self, auto=False):
        st = netmon.monitor_status(self.cfg)
        if not st["running"]:
            netmon.spawn_monitor(self.cfg)
            if auto:
                self.msg_var.set("Monitor iniciado em segundo plano. Fechar esta janela não interrompe a coleta.")

    def toggle(self):
        st = netmon.monitor_status(self.cfg)
        if st["running"]:
            self.msg_var.set("Parando o monitor...")
            self._run_bg(lambda: netmon.request_stop(self.cfg),
                         lambda ok: self.msg_var.set("Monitor parado. Ele volta ao abrir esta janela ou ao "
                                                     "reiniciar o Windows, se o início automático estiver ligado."))
        else:
            netmon.spawn_monitor(self.cfg)
            self.msg_var.set("Monitor iniciado.")
        self.root.after(1500, lambda: self._tick_status(reschedule=False))

    def _disable_focus(self, widget):
        for child in widget.winfo_children():
            if child.winfo_class() in ("TButton", "Button", "TCheckbutton", "Checkbutton", "TNotebook",
                                       "Treeview", "TRadiobutton", "Canvas"):
                try:
                    child.configure(takefocus=False)
                except tk.TclError:
                    pass
            self._disable_focus(child)

    def _on_space(self, event):
        w = self.root.focus_get()
        cls = w.winfo_class() if w is not None else ""
        if cls in ("Entry", "TEntry", "Spinbox", "TSpinbox", "TCombobox"):
            return None
        if cls == "Text":
            try:
                if str(w.cget("state")) == "normal":
                    return None
            except tk.TclError:
                pass
        self.mark_now()
        return "break"

    def mark_now(self):
        try:
            ts = netmon.add_mark(self.store)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("netmon", f"Não consegui gravar a marcação: {exc}\n\n"
                                           f"Banco: {self.cfg['db_path']}")
            return
        when = dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        st = netmon.monitor_status(self.cfg)
        hint = "" if st.get("call_mode") else "  Ligue o modo chamada para medir a duração."
        self.msg_var.set(f"Travamento marcado às {when}.{hint}")
        self.btn_mark.config(text=f"Marcado às {when}")
        self.root.after(4000, lambda: self.btn_mark.config(
            text="Marcar travamento agora  (barra de espaço)"))
        self._flash()
        self._refresh_marks()
        if self.nb.index("current") == 1:
            self.chart.reload()

    def _refresh_marks(self):
        rows = self.store.query("SELECT ts, note FROM marks ORDER BY ts DESC LIMIT 8")
        self.marks_tree.delete(*self.marks_tree.get_children())
        for r in rows:
            level, text = netmon.explain_mark(self.store, r["ts"])
            when = dt.datetime.fromtimestamp(r["ts"]).strftime("%d/%m %H:%M:%S")
            self.marks_tree.insert("", "end", values=(when, text), tags=(level,))
        today0 = dt.datetime.combine(dt.date.today(), dt.time.min).timestamp()
        today = [r for r in rows if r["ts"] >= today0]
        total = self.store.query("SELECT COUNT(*) AS n FROM marks WHERE ts >= ?", (today0,))[0]["n"]
        if total:
            last = dt.datetime.fromtimestamp(today[0]["ts"]).strftime("%H:%M:%S") if today else ""
            self.mark_var.set(f"{total} hoje, a última às {last}")
        else:
            self.mark_var.set("nenhuma marcação hoje")

    def _flash(self, times=2):
        orig = self.dot.itemcget(self.dot_id, "fill")

        def blink(n):
            self.dot.itemconfig(self.dot_id, fill="#7c3aed" if n % 2 == 0 else orig)
            if n < times * 2:
                self.root.after(150, lambda: blink(n + 1))
        blink(0)

    def toggle_call(self):
        st = netmon.monitor_status(self.cfg)
        on = not st.get("call_mode")
        self._ensure_running()
        netmon.request_call_mode(self.cfg, on)
        self.msg_var.set("Modo chamada ligado: 1 pacote por segundo, testes de velocidade suspensos." if on
                         else "Modo chamada desligado.")
        self.root.after(1500, lambda: self._tick_status(reschedule=False))

    def test_now(self):
        self._ensure_running()
        netmon.request_speed_test(self.cfg)
        self.msg_var.set("Teste de velocidade solicitado; o resultado aparece em até 30 segundos.")

    def report(self, days):
        out = os.path.join(self.cfg["_dir"], f"relatorio_{days}d.html")

        def work():
            st = netmon.compute_stats(self.cfg, self.store, days)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(netmon.render_html(self.cfg, st))
            return out

        self.msg_var.set("Gerando relatório...")
        self._run_bg(work, lambda path: (webbrowser.open("file://" + os.path.abspath(path)),
                                         self.msg_var.set(f"Relatório salvo em {path}")))

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="netmon_velocidade.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        import argparse
        ns = argparse.Namespace(table="speed", days=90, out=path)
        self._run_bg(lambda: netmon.cmd_export(ns, self.cfg), lambda _: self.msg_var.set(f"Exportado para {path}"))

    def save(self):
        try:
            plan_d = float(self.plan_down.get() or 0)
            plan_u = float(self.plan_up.get() or 0)
        except ValueError:
            messagebox.showerror("netmon", "Velocidade do plano deve ser um número.")
            return
        quiet = [q.strip() for q in self.quiet.get().replace(";", ",").split(",") if q.strip()]
        for q in quiet:
            if not netmon.re.fullmatch(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", q):
                messagebox.showerror("netmon", f"Horário inválido: {q}. Use o formato 09:00-12:00.")
                return
        sched = [q.strip() for q in self.call_sched.get().replace(";", ",").split(",") if q.strip()]
        for entry in sched:
            try:
                netmon.parse_schedule_entry(entry)
            except ValueError as exc:
                messagebox.showerror("netmon", f"Agenda do modo chamada: {exc}. Use o formato seg-sex 08:00-18:00.")
                return
        hops = [h.strip() for h in self.hops_entry.get().replace(";", ",").split(",") if h.strip()]
        for h in hops:
            if not netmon.re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", h):
                messagebox.showerror("netmon", f"Endereço inválido: {h}. Use o formato 192.168.68.1.")
                return
        size = {n: (d, u) for n, d, u in SIZE_PRESETS}[self.size.get()]
        changes = {
            "ping": {"local_hops": hops if hops else "auto"},
            "call_mode": {"schedule": sched, "skip_speed": self.call_skip_var.get()},
            "plan": {"download_mbps": plan_d, "upload_mbps": plan_u},
            "speed": {"interval_s": dict(INTERVAL_PRESETS)[self.interval.get()],
                      "download_bytes": size[0], "upload_bytes": size[1],
                      "quiet_hours": [q.replace(" ", "") for q in quiet]},
        }
        self.cfg = netmon.save_config(self.cfg, changes)
        self.chart.cfg = self.cfg
        self._update_budget_label()
        if netmon.IS_WINDOWS:
            try:
                netmon.set_autostart(self.cfg, self.autostart_var.get())
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning("netmon", f"Não foi possível alterar o início automático: {exc}")
        was_running = netmon.monitor_status(self.cfg)["running"]

        def restart():
            if was_running:
                netmon.request_stop(self.cfg)
            netmon.spawn_monitor(self.cfg)

        self.msg_var.set("Configuração salva; reiniciando o monitor para aplicar...")
        self._run_bg(restart, lambda _: self.msg_var.set("Configuração salva e aplicada."))

    # ---- atualização periódica ------------------------------------------

    def _tick_status(self, reschedule=True):
        st = netmon.monitor_status(self.cfg)
        if st["running"]:
            since = dt.datetime.fromtimestamp(st["started"]).strftime("%d/%m às %H:%M")
            self.status_var.set(f"Monitorando desde {since}")
            self.dot.itemconfig(self.dot_id, fill="#16a34a")
            self.btn_toggle.config(text="Parar")
            if st.get("call_mode"):
                self.btn_call.config(text="Desligar modo chamada" if st.get("call_reason") == "manual"
                                     else "Pausar modo chamada (agenda)")
            else:
                self.btn_call.config(text="Ligar modo chamada")
        else:
            self.status_var.set("Monitor parado")
            self.dot.itemconfig(self.dot_id, fill="#dc2626")
            self.btn_toggle.config(text="Iniciar")
        self._refresh_log()
        if reschedule:
            self.root.after(5000, self._tick_status)

    def _refresh_log(self):
        try:
            with open(self.cfg["log_path"], "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - 6000))
                lines = fh.read().decode("utf-8", errors="replace").splitlines()[-40:]
        except OSError:
            lines = ["(sem registro ainda)"]
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(lines))
        self.log.see("end")
        self.log.config(state="disabled")

    def _tick_data(self):
        try:
            self._refresh_tiles()
            self._refresh_marks()
        except Exception as exc:  # noqa: BLE001
            self.msg_var.set(f"Erro ao ler dados: {exc}")
        self.root.after(15000, self._tick_data)

    def _refresh_tiles(self):
        q = self.store.query
        last = q("SELECT * FROM ping WHERE cycle = (SELECT MAX(cycle) FROM ping)")
        inet = [r for r in last if r["kind"] == "internet"]
        gw = [r for r in last if r["kind"] == "gateway"]
        v, s = self.tiles["ping"]
        if inet:
            loss = netmon.statistics.median(r["loss_pct"] for r in inet)
            rtts = [r["rtt_avg"] for r in inet if r["rtt_avg"] is not None]
            rtt = netmon.statistics.median(rtts) if rtts else None
            v.config(text=f"{fmt(rtt, 0, ' ms')}  perda {loss:.0f}%",
                     foreground="#dc2626" if loss >= 2 else "#111827")
            s.config(text=f"medido às {netmon.fmt_ts(inet[0]['ts'], with_date=False)}")
        loc = [r for r in last if r["kind"] == "local"]
        v, s = self.tiles["gw"]
        if gw:
            r = gw[0]
            v.config(text=f"{fmt(r['rtt_avg'], 0, ' ms')}  perda {r['loss_pct']:.0f}%",
                     foreground="#dc2626" if r["loss_pct"] >= 1 else "#111827")
            extra = ""
            if loc:
                l = loc[0]
                extra = f"  |  2º {l['host']}: {fmt(l['rtt_avg'], 0, ' ms')}, perda {l['loss_pct']:.0f}%"
            s.config(text=f"1º {r['host']}{extra}")
        else:
            v.config(text="-")
            s.config(text="nenhum salto local detectado")
        for key, direction in (("down", "download"), ("up", "upload")):
            v, s = self.tiles[key]
            rows = q("SELECT mbps, ts FROM speed WHERE ok=1 AND direction=? ORDER BY ts DESC LIMIT 1", (direction,))
            if rows:
                plan = self.cfg["plan"].get(f"{direction}_mbps") or 0
                color = "#dc2626" if plan and rows[0]["mbps"] < plan * 0.5 else "#111827"
                v.config(text=f"{rows[0]['mbps']:.1f} Mbps", foreground=color)
                s.config(text=f"às {netmon.fmt_ts(rows[0]['ts'])}" + (f", plano {plan:.0f}" if plan else ""))
        v, s = self.tiles["call"]
        st = netmon.monitor_status(self.cfg)
        today0 = dt.datetime.combine(dt.date.today(), dt.time.min).timestamp()
        nb = q("SELECT COUNT(*) AS n, COALESCE(MAX(duration_s),0) AS mx FROM call_burst WHERE kind='internet' "
               "AND COALESCE(suspect,0)=0 AND ts >= ?", (today0,))[0]
        mins = q("SELECT COUNT(*) AS n FROM call_minute WHERE kind='internet' AND COALESCE(suspect,0)=0 "
                 "AND ts >= ?", (today0,))[0]["n"]
        if st.get("call_mode"):
            v.config(text="ligado", foreground="#16a34a")
        else:
            v.config(text="desligado", foreground="#6b7280")
        s.config(text=f"hoje: {mins} min, {nb['n']} travamentos" + (f", máx {nb['mx']:.0f} s" if nb["n"] else ""))
        v, s = self.tiles["today"]
        since = time.time() - 86400
        cyc = q("SELECT cycle, MAX(received) AS m FROM ping WHERE kind='internet' AND ts >= ? GROUP BY cycle", (since,))
        if cyc:
            down = sum(1 for c in cyc if (c["m"] or 0) == 0)
            avail = 100.0 * (1 - down / len(cyc))
            outs = q("SELECT COUNT(*) AS n FROM events WHERE kind='outage_start' AND ts >= ?", (since,))[0]["n"]
            marks = q("SELECT COUNT(*) AS n FROM marks WHERE ts >= ?", (since,))[0]["n"]
            v.config(text=f"{avail:.1f}% no ar", foreground="#dc2626" if avail < 99 else "#111827")
            s.config(text=f"{outs} queda(s), {marks} marcação(ões), {len(cyc)} ciclos")

    def _tick_summary(self):
        def work():
            return netmon.compute_stats(self.cfg, self.store, 7)

        def done(st):
            d, u = st["speed"]["download"], st["speed"]["upload"]
            self.summary_var.set(
                f"Disponibilidade {fmt(st['availability_pct'], 2, '%'):>8}   quedas {len(st['outages'])} "
                f"({netmon.fmt_minutes(st['outage_minutes'])})\n"
                f"Perda média     {fmt(st['loss_avg'], 2, '%'):>8}   ciclos com perda >= 2%: "
                f"{fmt(st['loss_lossy_pct'], 1, '%')}\n"
                f"Latência        p50 {fmt(st['rtt_p50'], 0)} ms   p95 {fmt(st['rtt_p95'], 0)} ms   "
                f"jitter p95 {fmt(st['jitter_p95'], 0)} ms\n"
                f"Download        p5 {fmt(d['p5'])}   mediana {fmt(d['p50'])}   p95 {fmt(d['p95'])} Mbps  "
                f"({d['n']} testes)\n"
                f"Upload          p5 {fmt(u['p5'])}   mediana {fmt(u['p50'])}   p95 {fmt(u['p95'])} Mbps  "
                f"({u['n']} testes)")
            self.conclusion_var.set(st["verdict"]["conclusion"])

        threading.Thread(target=lambda: self._safe_summary(work, done), daemon=True).start()
        self.root.after(5 * 60 * 1000, self._tick_summary)

    def _safe_summary(self, work, done):
        try:
            st = work()
            self.root.after(0, lambda: done(st))
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, lambda: self.summary_var.set(f"Sem dados suficientes ainda ({exc})."))


    # ---- atualização -----------------------------------------------------

    def _check_update_bg(self):
        info = netmon.check_update(self.cfg)
        if info and info["newer"]:
            self.update_info = info
            self.root.after(0, self._show_update)

    def _show_update(self):
        v = self.update_info["version"]
        self.btn_update.config(text=f"Atualizar para {v}")
        self.btn_update.pack(side="right", padx=(6, 0))
        failed = netmon.last_update_failure(self.cfg)
        if failed:
            self.msg_var.set(f"A última tentativa de atualizar não concluiu ({failed[-1]}). Tente de novo ou "
                             f"execute o netmon-setup.exe da release; detalhes em netmon-update.log na pasta de dados.")
        else:
            self.msg_var.set(f"Nova versão {v} disponível. Clique em \"Atualizar para {v}\" para instalar.")

    def do_update(self):
        info = self.update_info
        if not info:
            return
        if not messagebox.askyesno("netmon", f"Instalar a versão {info['version']} agora? O monitor será reiniciado "
                                            "e esta janela vai fechar e reabrir sozinha."):
            return
        self.msg_var.set("Baixando e instalando a atualização...")
        self.btn_update.state(["disabled"])

        def done(_):
            self.root.destroy()

        self._run_bg(lambda: netmon.apply_update(self.cfg, info), done)


def do_install(cfg):
    """Usado pelo Instalar.bat e pelo instalador: atalhos, autostart, monitor."""
    created = []
    if netmon.IS_WINDOWS:
        try:
            created = netmon.install_shortcuts(cfg)
            netmon.set_autostart(cfg, True)
        except Exception as exc:  # noqa: BLE001
            print(f"aviso: não foi possível criar atalhos: {exc}")
    netmon.spawn_monitor(cfg)
    return created


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough = [a for a in argv if a not in ("--install",)]
    if passthrough:
        # Modo linha de comando (o executável instalado usa isto para rodar o monitor)
        return netmon.main(passthrough)
    cfg = netmon.load_config()
    if "--install" in argv:
        do_install(cfg)
    if tk is None:
        sys.exit("Tkinter não está disponível neste Python. Instale o pacote python3-tk ou use "
                 "python netmon.py (linha de comando).")
    root = tk.Tk()
    App(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
