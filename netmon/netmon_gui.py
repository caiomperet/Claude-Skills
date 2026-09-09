#!/usr/bin/env python3
"""
Interface gráfica do netmon (Tkinter, sem dependências externas).

Ao abrir, inicia o monitor em segundo plano se ele não estiver rodando, mostra
o estado atual, o resumo dos últimos 7 dias e permite abrir o relatório,
ajustar as poucas configurações relevantes e ligar o início automático com o
Windows. Fechar a janela não para o monitor.

  python netmon_gui.py            abre a interface
  python netmon_gui.py --install  cria atalhos, liga o início automático,
                                  inicia o monitor e abre a interface
  python netmon_gui.py <comando>  qualquer outro argumento é repassado ao
                                  netmon.py (usado pelo executável instalado)
"""
from __future__ import annotations

import datetime as dt
import os
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


class App:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.store = netmon.Store(cfg["db_path"])
        self.busy = False
        root.title("netmon: qualidade da internet")
        root.minsize(760, 640)
        try:
            ttk.Style().theme_use("vista" if netmon.IS_WINDOWS else "clam")
        except tk.TclError:
            pass
        self._build()
        self._ensure_running(auto=True)
        self._tick_status()
        self._tick_data()
        self._tick_summary()

    # ---- construção ------------------------------------------------------

    def _build(self):
        pad = {"padx": 12, "pady": 6}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        self.dot = tk.Canvas(top, width=16, height=16, highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 14, 14, fill="#9ca3af", outline="")
        self.dot.pack(side="left")
        self.status_var = tk.StringVar(value="Verificando...")
        ttk.Label(top, textvariable=self.status_var, font=("", 11, "bold")).pack(side="left", padx=8)
        self.btn_toggle = ttk.Button(top, text="Parar", command=self.toggle)
        self.btn_toggle.pack(side="right")
        ttk.Button(top, text="Testar velocidade agora", command=self.test_now).pack(side="right", padx=6)

        tiles = ttk.LabelFrame(self.root, text="Agora")
        tiles.pack(fill="x", **pad)
        self.tiles = {}
        for col, (key, title) in enumerate([("ping", "Internet"), ("gw", "Roteador"), ("down", "Download"),
                                            ("up", "Upload"), ("today", "Últimas 24 h")]):
            f = ttk.Frame(tiles, padding=(10, 6))
            f.grid(row=0, column=col, sticky="nsew")
            tiles.columnconfigure(col, weight=1)
            ttk.Label(f, text=title.upper(), foreground="#6b7280", font=("", 8)).pack(anchor="w")
            v = ttk.Label(f, text="-", font=("", 14, "bold"))
            v.pack(anchor="w")
            s = ttk.Label(f, text="", foreground="#6b7280", font=("", 8))
            s.pack(anchor="w")
            self.tiles[key] = (v, s)

        summ = ttk.LabelFrame(self.root, text="Últimos 7 dias")
        summ.pack(fill="x", **pad)
        self.summary_var = tk.StringVar(value="Calculando...")
        ttk.Label(summ, textvariable=self.summary_var, justify="left", font=("Consolas" if netmon.IS_WINDOWS
                                                                              else "TkFixedFont", 9)).pack(anchor="w", padx=10, pady=(6, 2))
        self.conclusion_var = tk.StringVar(value="")
        self.conclusion = ttk.Label(summ, textvariable=self.conclusion_var, wraplength=700, justify="left")
        self.conclusion.pack(anchor="w", padx=10, pady=(2, 8))
        row = ttk.Frame(summ)
        row.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(row, text="Abrir relatório (7 dias)", command=lambda: self.report(7)).pack(side="left")
        ttk.Button(row, text="Relatório (30 dias)", command=lambda: self.report(30)).pack(side="left", padx=6)
        ttk.Button(row, text="Exportar CSV", command=self.export_csv).pack(side="left")
        ttk.Button(row, text="Abrir pasta de dados", command=lambda: open_path(self.cfg["_dir"])).pack(side="left", padx=6)

        conf = ttk.LabelFrame(self.root, text="Configurações")
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

        self.autostart_var = tk.BooleanVar(value=netmon.autostart_enabled())
        cb = ttk.Checkbutton(g, text="Iniciar o monitor junto com o Windows", variable=self.autostart_var)
        cb.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        if not netmon.IS_WINDOWS:
            cb.state(["disabled"])
        ttk.Button(g, text="Salvar", command=self.save).grid(row=4, column=3, columnspan=2, sticky="e", pady=(8, 0))
        self.budget_var = tk.StringVar()
        ttk.Label(g, textvariable=self.budget_var, foreground="#6b7280", font=("", 8)).grid(
            row=5, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self._load_settings()

        logf = ttk.LabelFrame(self.root, text="Registro")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=7, font=("Consolas" if netmon.IS_WINDOWS else "TkFixedFont", 8),
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
        self._update_budget_label()

    def _update_budget_label(self):
        try:
            iv = dict(INTERVAL_PRESETS).get(self.interval.get(), self.cfg["speed"]["interval_s"])
            sz = {n: (d, u) for n, d, u in SIZE_PRESETS}.get(self.size.get(),
                                                             (self.cfg["speed"]["download_bytes"],
                                                              self.cfg["speed"]["upload_bytes"]))
            per_day = (sz[0] + sz[1]) * (86400 / iv) / 1e6
            self.budget_var.set(f"Consumo máximo dos testes de velocidade: {per_day:.0f} MB por dia "
                                f"(~{per_day * 30 / 1000:.1f} GB por mês). Os testes são adiados quando a conexão "
                                f"está em uso.")
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
        size = {n: (d, u) for n, d, u in SIZE_PRESETS}[self.size.get()]
        changes = {
            "plan": {"download_mbps": plan_d, "upload_mbps": plan_u},
            "speed": {"interval_s": dict(INTERVAL_PRESETS)[self.interval.get()],
                      "download_bytes": size[0], "upload_bytes": size[1],
                      "quiet_hours": [q.replace(" ", "") for q in quiet]},
        }
        self.cfg = netmon.save_config(self.cfg, changes)
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
        v, s = self.tiles["gw"]
        if gw:
            r = gw[0]
            v.config(text=f"{fmt(r['rtt_avg'], 0, ' ms')}  perda {r['loss_pct']:.0f}%",
                     foreground="#dc2626" if r["loss_pct"] >= 1 else "#111827")
            s.config(text=r["host"])
        else:
            v.config(text="-")
            s.config(text="gateway não detectado")
        for key, direction in (("down", "download"), ("up", "upload")):
            v, s = self.tiles[key]
            rows = q("SELECT mbps, ts FROM speed WHERE ok=1 AND direction=? ORDER BY ts DESC LIMIT 1", (direction,))
            if rows:
                plan = self.cfg["plan"].get(f"{direction}_mbps") or 0
                color = "#dc2626" if plan and rows[0]["mbps"] < plan * 0.5 else "#111827"
                v.config(text=f"{rows[0]['mbps']:.1f} Mbps", foreground=color)
                s.config(text=f"às {netmon.fmt_ts(rows[0]['ts'])}" + (f", plano {plan:.0f}" if plan else ""))
        v, s = self.tiles["today"]
        since = time.time() - 86400
        cyc = q("SELECT cycle, MAX(received) AS m FROM ping WHERE kind='internet' AND ts >= ? GROUP BY cycle", (since,))
        if cyc:
            down = sum(1 for c in cyc if (c["m"] or 0) == 0)
            avail = 100.0 * (1 - down / len(cyc))
            outs = q("SELECT COUNT(*) AS n FROM events WHERE kind='outage_start' AND ts >= ?", (since,))[0]["n"]
            v.config(text=f"{avail:.1f}% no ar", foreground="#dc2626" if avail < 99 else "#111827")
            s.config(text=f"{outs} queda(s), {len(cyc)} ciclos")

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
