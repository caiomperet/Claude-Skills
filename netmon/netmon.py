#!/usr/bin/env python3
"""
netmon: monitor leve de qualidade da conexão de internet.

Mede periodicamente, em segundo plano e com consumo mínimo de banda:
  - latência, jitter e perda de pacotes (ICMP ou, na falta dele, TCP)
    para hosts na internet e para o gateway local (roteador);
  - tempo de resolução DNS;
  - velocidade de download e upload com transferências pequenas e limitadas
    em tempo, pulando o teste quando a conexão está em uso ou dentro de
    horários de silêncio.

Tudo é gravado em um banco SQLite local. O relatório HTML e o resumo em
texto consolidam os dados para responder: o problema é local, do provedor,
ou o plano é simplesmente pequeno para o uso?

Uso:
  python netmon.py run                # monitora continuamente
  python netmon.py once               # roda todas as medições uma vez
  python netmon.py summary --days 7   # resumo em texto
  python netmon.py report --days 7 --open
  python netmon.py export --table speed --out speed.csv

Somente biblioteca padrão. psutil é opcional (melhora a detecção de
"conexão ocupada" no Windows e no macOS).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import logging
import logging.handlers
import os
import platform
import re
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

APP = "netmon"
VERSION = "1.1.0"
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"
IS_LINUX = SYSTEM == "Linux"


def default_data_dir():
    """Onde ficam config, banco e log.

    Rodando a partir do código-fonte, ao lado do script (comportamento simples
    e portátil). Instalado como executável, na pasta de dados do usuário, para
    não gravar dentro de Program Files nem em pastas temporárias.
    """
    env = os.environ.get("NETMON_HOME")
    if env:
        return env
    if not FROZEN:
        return BASE_DIR
    if IS_WINDOWS:
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP)
    if IS_MAC:
        return os.path.expanduser(f"~/Library/Application Support/{APP}")
    return os.path.expanduser(f"~/.local/share/{APP}")


DATA_DIR = default_data_dir()
USER_AGENT = f"{APP}/{VERSION} (+https://github.com/caiomperet/Claude-Skills)"

DEFAULT_CONFIG = {
    "db_path": "netmon.db",
    "log_path": "netmon.log",
    "log_level": "INFO",
    "plan": {
        "download_mbps": 0,
        "upload_mbps": 0,
    },
    "ping": {
        "interval_s": 60,
        "hosts": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
        "gateway": "auto",
        "count": 10,
        "packet_interval_ms": 500,
        "timeout_s": 2,
        "method": "auto",
        "tcp_port": 443,
    },
    "dns": {
        "interval_s": 300,
        "names": ["www.google.com", "www.cloudflare.com", "www.microsoft.com"],
        "timeout_s": 5,
    },
    "speed": {
        "interval_s": 1800,
        "download_bytes": 10000000,
        "upload_bytes": 4000000,
        "max_seconds": 12,
        "timeout_s": 25,
        "download_urls": ["https://speed.cloudflare.com/__down?bytes={bytes}"],
        "upload_urls": ["https://speed.cloudflare.com/__up"],
        "skip_if_busy_mbps": 2.0,
        "busy_retry_s": 300,
        "daily_budget_mb": 1500,
        "quiet_hours": [],
    },
}

# Limiares usados no diagnóstico do relatório. Ajuste se o seu uso for
# mais ou menos sensível (chamadas de vídeo pedem perda ~0 e jitter baixo).
THRESHOLDS = {
    "loss_cycle_pct": 2.0,        # um ciclo com perda >= 2% conta como "com perda"
    "loss_cycles_share_pct": 5.0, # mais de 5% dos ciclos com perda: instável
    "rtt_p95_ms": 100.0,
    "jitter_p95_ms": 30.0,
    "outages_per_week": 3,
    "outage_minutes_per_week": 30,
    "speed_p5_plan_ratio": 0.5,   # p5 abaixo de 50% do plano: provedor não entrega
    "speed_p50_plan_ratio": 0.7,
    "gateway_loss_pct": 1.0,
    "dns_fail_pct": 2.0,
}

log = logging.getLogger(APP)


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

def deep_merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None):
    path = path or os.path.join(DATA_DIR, "config.json")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    user_cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            user_cfg = json.load(fh)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_CONFIG, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    cfg = deep_merge(DEFAULT_CONFIG, user_cfg)
    cfg_dir = os.path.dirname(os.path.abspath(path))
    for key in ("db_path", "log_path"):
        if cfg[key] and not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(cfg_dir, cfg[key])
    cfg["_path"] = path
    cfg["_dir"] = cfg_dir
    return cfg


def save_config(cfg, changes):
    """Grava só o que difere do padrão, preservando o que o usuário já tinha."""
    path = cfg["_path"]
    current = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            current = json.load(fh)
    merged = deep_merge(current, changes)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return load_config(path)


def setup_logging(cfg, to_console=True):
    level = getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    log.setLevel(level)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    if cfg.get("log_path"):
        fh = logging.handlers.RotatingFileHandler(
            cfg["log_path"], maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    if to_console and sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        log.addHandler(sh)


# --------------------------------------------------------------------------
# Armazenamento
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS ping (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    cycle REAL NOT NULL,
    host TEXT NOT NULL,
    kind TEXT NOT NULL,
    method TEXT,
    sent INTEGER,
    received INTEGER,
    loss_pct REAL,
    rtt_min REAL,
    rtt_avg REAL,
    rtt_max REAL,
    jitter REAL,
    during_speed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ping_ts ON ping(ts);
CREATE TABLE IF NOT EXISTS dns (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    name TEXT NOT NULL,
    ms REAL,
    ok INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS dns_ts ON dns(ts);
CREATE TABLE IF NOT EXISTS speed (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    direction TEXT NOT NULL,
    url TEXT,
    bytes INTEGER,
    seconds REAL,
    mbps REAL,
    ttfb_ms REAL,
    ok INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS speed_ts ON speed(ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def insert(self, table, row):
        cols = ",".join(row.keys())
        marks = ",".join("?" for _ in row)
        with self.lock:
            self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
            self.conn.commit()

    def query(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def event(self, kind, detail=""):
        self.insert("events", {"ts": time.time(), "kind": kind, "detail": detail})

    def close(self):
        with self.lock:
            self.conn.close()


# --------------------------------------------------------------------------
# Sondas: ping ICMP, ping TCP, DNS, gateway
# --------------------------------------------------------------------------

class PingUnavailable(Exception):
    pass


class ProbeResult:
    __slots__ = ("sent", "received", "rtts", "method")

    def __init__(self, sent, received, rtts, method):
        self.sent = sent
        self.received = min(received, sent)
        self.rtts = rtts
        self.method = method

    @property
    def loss_pct(self):
        return 100.0 * (self.sent - self.received) / self.sent if self.sent else 100.0

    @property
    def rtt_min(self):
        return min(self.rtts) if self.rtts else None

    @property
    def rtt_max(self):
        return max(self.rtts) if self.rtts else None

    @property
    def rtt_avg(self):
        return statistics.fmean(self.rtts) if self.rtts else None

    @property
    def jitter(self):
        if len(self.rtts) < 2:
            return None
        diffs = [abs(b - a) for a, b in zip(self.rtts, self.rtts[1:])]
        return statistics.fmean(diffs)


RTT_RE = re.compile(r"[=<]\s*(\d+(?:[.,]\d+)?)\s*ms", re.IGNORECASE)


def _subprocess_flags():
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def build_ping_cmd(host, count, timeout_s, interval_ms):
    if IS_WINDOWS:
        return ["ping", "-n", str(count), "-w", str(int(timeout_s * 1000)), host]
    interval = max(0.2, interval_ms / 1000.0)
    cmd = ["ping", "-c", str(count), "-i", f"{interval:.1f}"]
    if IS_MAC:
        cmd += ["-W", str(int(timeout_s * 1000))]
    else:
        cmd += ["-W", str(max(1, int(timeout_s)))]
    return cmd + [host]


def icmp_ping(host, count, timeout_s, interval_ms):
    cmd = build_ping_cmd(host, count, timeout_s, interval_ms)
    budget = count * (interval_ms / 1000.0 + timeout_s) + 10
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              timeout=budget, **_subprocess_flags())
    except FileNotFoundError as exc:
        raise PingUnavailable("comando ping não encontrado") from exc
    except subprocess.TimeoutExpired:
        return ProbeResult(count, 0, [], "icmp")
    rtts = []
    for line in proc.stdout.splitlines():
        if "ttl" not in line.lower():
            continue
        m = RTT_RE.search(line)
        if m:
            rtts.append(float(m.group(1).replace(",", ".")))
    if not rtts and proc.returncode != 0 and proc.stderr and "not permitted" in proc.stderr.lower():
        raise PingUnavailable(proc.stderr.strip())
    return ProbeResult(count, len(rtts), rtts, "icmp")


def split_host_port(target, default_port):
    if target.count(":") == 1 and target.rsplit(":", 1)[1].isdigit():
        host, port = target.rsplit(":", 1)
        return host, int(port)
    return target, default_port


def tcp_ping(host, port, count, timeout_s, interval_ms):
    rtts = []
    for i in range(count):
        t0 = time.perf_counter()
        try:
            sock = socket.create_connection((host, port), timeout=timeout_s)
            rtts.append((time.perf_counter() - t0) * 1000.0)
            sock.close()
        except OSError:
            pass
        if i < count - 1:
            time.sleep(interval_ms / 1000.0)
    return ProbeResult(count, len(rtts), rtts, "tcp")


def dns_probe(name, timeout_s):
    t0 = time.perf_counter()
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_s)
    try:
        socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
        return (time.perf_counter() - t0) * 1000.0, True, None
    except OSError as exc:
        return (time.perf_counter() - t0) * 1000.0, False, str(exc)
    finally:
        socket.setdefaulttimeout(old)


def detect_gateway():
    """Melhor esforço, independente de idioma, para achar o roteador."""
    try:
        if IS_WINDOWS:
            out = subprocess.run(["route", "print", "0.0.0.0"], capture_output=True, text=True,
                                 errors="replace", timeout=10, **_subprocess_flags()).stdout
            m = re.search(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)", out, re.MULTILINE)
            return m.group(1) if m else None
        if IS_MAC:
            out = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True,
                                 errors="replace", timeout=10).stdout
            m = re.search(r"gateway:\s*(\S+)", out)
            return m.group(1) if m else None
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True,
                             errors="replace", timeout=10).stdout
        m = re.search(r"via\s+(\S+)", out)
        return m.group(1) if m else None
    except Exception as exc:  # noqa: BLE001
        log.debug("gateway não detectado: %s", exc)
        return None


def choose_ping_method(pcfg, sample_host):
    method = pcfg.get("method", "auto")
    if method in ("icmp", "tcp"):
        return method
    host, port = split_host_port(sample_host, pcfg["tcp_port"])
    try:
        res = icmp_ping(host, 2, pcfg["timeout_s"], pcfg["packet_interval_ms"])
    except PingUnavailable as exc:
        log.warning("ICMP indisponível (%s); usando TCP connect na porta %s", exc, pcfg["tcp_port"])
        return "tcp"
    if res.received > 0:
        return "icmp"
    tcp = tcp_ping(host, port, 2, pcfg["timeout_s"], pcfg["packet_interval_ms"])
    if tcp.received > 0:
        log.warning("ICMP sem resposta mas TCP responde; usando TCP connect")
        return "tcp"
    return "icmp"


# --------------------------------------------------------------------------
# Sondas: velocidade e tráfego atual
# --------------------------------------------------------------------------

def measure_download(url_tpl, max_bytes, max_seconds, timeout_s):
    url = url_tpl.replace("{bytes}", str(max_bytes))
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Cache-Control": "no-cache", "Pragma": "no-cache"})
    total = 0
    first_t = None
    first_n = 0
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        while True:
            chunk = resp.read(256 * 1024)
            now = time.perf_counter()
            if not chunk:
                break
            if first_t is None:
                first_t, first_n = now, len(chunk)
            total += len(chunk)
            if total >= max_bytes or now - t0 >= max_seconds:
                break
    end = time.perf_counter()
    if first_t is None or total == 0:
        raise RuntimeError("resposta vazia")
    span = end - first_t
    measured = total - first_n
    if span >= 0.25 and measured > 0:
        mbps = measured * 8 / span / 1e6
    else:
        mbps = total * 8 / (end - t0) / 1e6
    return {"url": url, "bytes": total, "seconds": end - t0, "mbps": mbps,
            "ttfb_ms": (first_t - t0) * 1000.0}


def measure_upload(url, n_bytes, timeout_s):
    payload = os.urandom(n_bytes)
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "User-Agent": USER_AGENT, "Content-Type": "application/octet-stream"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        resp.read()
    end = time.perf_counter()
    secs = end - t0
    return {"url": url, "bytes": n_bytes, "seconds": secs, "mbps": n_bytes * 8 / secs / 1e6,
            "ttfb_ms": None}


def net_counters():
    """Total de bytes (rx+tx) das interfaces, ou None se não for possível."""
    try:
        import psutil  # type: ignore
        c = psutil.net_io_counters()
        return c.bytes_recv + c.bytes_sent
    except Exception:  # noqa: BLE001
        pass
    try:
        if IS_LINUX:
            total = 0
            with open("/proc/net/dev", encoding="utf-8") as fh:
                for line in fh.readlines()[2:]:
                    name, data = line.split(":", 1)
                    if name.strip() == "lo":
                        continue
                    f = data.split()
                    total += int(f[0]) + int(f[8])
            return total
        if IS_WINDOWS:
            out = subprocess.run(["netstat", "-e"], capture_output=True, text=True, errors="replace",
                                 timeout=10, **_subprocess_flags()).stdout
            for line in out.splitlines():
                nums = re.findall(r"\d+", line)
                if len(nums) == 2 and int(nums[0]) + int(nums[1]) > 0 and "bytes" in line.lower():
                    return int(nums[0]) + int(nums[1])
    except Exception:  # noqa: BLE001
        return None
    return None


def current_traffic_mbps(sample_s=2.0):
    a = net_counters()
    if a is None:
        return None
    time.sleep(sample_s)
    b = net_counters()
    if b is None:
        return None
    return max(0, b - a) * 8 / sample_s / 1e6


def in_quiet_hours(ranges, now=None):
    now = now or dt.datetime.now()
    minute = now.hour * 60 + now.minute
    for rng in ranges:
        try:
            a, b = rng.split("-")
            h1, m1 = (int(x) for x in a.strip().split(":"))
            h2, m2 = (int(x) for x in b.strip().split(":"))
        except ValueError:
            log.warning("quiet_hours inválido: %r", rng)
            continue
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if start <= end:
            if start <= minute < end:
                return True
        elif minute >= start or minute < end:
            return True
    return False


# --------------------------------------------------------------------------
# Monitor (laços periódicos)
# --------------------------------------------------------------------------

def control_paths(cfg):
    d = cfg["_dir"]
    return {"pid": os.path.join(d, "netmon.pid"), "stop": os.path.join(d, "netmon.stop"),
            "testnow": os.path.join(d, "netmon.testnow")}


def monitor_status(cfg, stale_s=90):
    """Lê o arquivo de heartbeat. Retorna dict com running, pid, started, heartbeat."""
    paths = control_paths(cfg)
    try:
        with open(paths["pid"], encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return {"running": False, "pid": None, "started": None, "heartbeat": None}
    age = time.time() - float(info.get("heartbeat", 0))
    info["running"] = age < stale_s
    info["age_s"] = age
    return info


def request_stop(cfg, wait_s=15):
    paths = control_paths(cfg)
    if not monitor_status(cfg)["running"]:
        return True
    with open(paths["stop"], "w", encoding="utf-8") as fh:
        fh.write(str(time.time()))
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not os.path.exists(paths["pid"]):
            return True
        time.sleep(0.5)
    return not monitor_status(cfg)["running"]


def request_speed_test(cfg):
    with open(control_paths(cfg)["testnow"], "w", encoding="utf-8") as fh:
        fh.write(str(time.time()))


def monitor_command(cfg):
    """Linha de comando que inicia o monitor em segundo plano."""
    if FROZEN:
        cmd = [sys.executable]
    else:
        exe = sys.executable
        if IS_WINDOWS and exe.lower().endswith("python.exe"):
            candidate = exe[:-10] + "pythonw.exe"
            if os.path.exists(candidate):
                exe = candidate
        cmd = [exe, os.path.join(BASE_DIR, "netmon.py")]
    return cmd + ["--config", cfg["_path"], "run", "--quiet"]


def spawn_monitor(cfg):
    """Inicia o monitor como processo independente e sem janela."""
    if monitor_status(cfg)["running"]:
        return monitor_status(cfg)["pid"]
    for name in ("stop",):
        try:
            os.remove(control_paths(cfg)[name])
        except OSError:
            pass
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "cwd": cfg["_dir"]}
    if IS_WINDOWS:
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(monitor_command(cfg), **kwargs)
    return proc.pid


# ---- Atalhos e início automático (Windows) ---------------------------------

def _windows_special_folder(name):
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"[Environment]::GetFolderPath('{name}')"],
                         capture_output=True, text=True, errors="replace", timeout=20,
                         **_subprocess_flags()).stdout.strip()
    return out or None


def _windows_shortcut(path, target, args, workdir, description):
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{path}'); "
        "$s.TargetPath = '{target}'; $s.Arguments = '{args}'; $s.WorkingDirectory = '{wd}'; "
        "$s.Description = '{desc}'; $s.WindowStyle = 7; $s.Save()"
    ).format(path=path.replace("'", "''"), target=target.replace("'", "''"),
             args=args.replace("'", "''"), wd=workdir.replace("'", "''"), desc=description)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=30,
                   capture_output=True, **_subprocess_flags())


def autostart_shortcut_path():
    if not IS_WINDOWS:
        return None
    folder = _windows_special_folder("Startup")
    return os.path.join(folder, "netmon monitor.lnk") if folder else None


def autostart_enabled():
    p = autostart_shortcut_path()
    return bool(p and os.path.exists(p))


def set_autostart(cfg, enabled):
    """Cria ou remove o atalho na pasta Inicializar do usuário (sem admin)."""
    if not IS_WINDOWS:
        raise RuntimeError("Início automático pela interface só está disponível no Windows; "
                           "veja a pasta deploy para macOS e Linux.")
    p = autostart_shortcut_path()
    if not p:
        raise RuntimeError("Pasta Inicializar não encontrada")
    if enabled:
        cmd = monitor_command(cfg)
        _windows_shortcut(p, cmd[0], " ".join(f'"{a}"' for a in cmd[1:]), cfg["_dir"],
                          "Monitor de qualidade da internet (segundo plano)")
    elif os.path.exists(p):
        os.remove(p)


def install_shortcuts(cfg):
    """Atalhos da interface no Menu Iniciar e na Área de Trabalho (Windows)."""
    if not IS_WINDOWS:
        return []
    if FROZEN:
        target, args = sys.executable, ""
    else:
        cmd = monitor_command(cfg)
        target = cmd[0]
        args = f'"{os.path.join(BASE_DIR, "netmon_gui.py")}"'
    created = []
    for folder_name, file_name in (("Programs", "netmon.lnk"), ("Desktop", "netmon.lnk")):
        folder = _windows_special_folder(folder_name)
        if folder:
            path = os.path.join(folder, file_name)
            _windows_shortcut(path, target, args, cfg["_dir"], "Monitor de qualidade da internet")
            created.append(path)
    return created


class Monitor:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.stop = threading.Event()
        self.speed_running = threading.Event()
        self.test_now = threading.Event()
        self.ping_method = None
        self.gateway = None
        self.down = False
        self.started = time.time()
        self.paths = control_paths(cfg)

    def setup(self):
        pcfg = self.cfg["ping"]
        gw = pcfg.get("gateway", "auto")
        self.gateway = detect_gateway() if gw == "auto" else (gw or None)
        self.ping_method = choose_ping_method(pcfg, pcfg["hosts"][0])
        log.info("netmon %s em %s | gateway=%s | método ping=%s | banco=%s",
                 VERSION, SYSTEM, self.gateway or "desconhecido", self.ping_method, self.store.path)

    # ---- ping -------------------------------------------------------------

    def probe(self, target):
        pcfg = self.cfg["ping"]
        host, port = split_host_port(target, pcfg["tcp_port"])
        if self.ping_method == "icmp":
            try:
                return icmp_ping(host, pcfg["count"], pcfg["timeout_s"], pcfg["packet_interval_ms"])
            except PingUnavailable as exc:
                log.warning("ICMP falhou (%s); mudando para TCP", exc)
                self.ping_method = "tcp"
        return tcp_ping(host, port, pcfg["count"], pcfg["timeout_s"], pcfg["packet_interval_ms"])

    def ping_cycle(self):
        pcfg = self.cfg["ping"]
        cycle = time.time()
        targets = [(h, "internet") for h in pcfg["hosts"]]
        if self.gateway:
            targets.append((self.gateway, "gateway"))
        results = {}

        def work(target):
            try:
                results[target] = self.probe(target)
            except Exception as exc:  # noqa: BLE001
                log.error("sonda %s falhou: %s", target, exc)
                results[target] = None

        threads = [threading.Thread(target=work, args=(t,), daemon=True) for t, _ in targets]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        during = 1 if self.speed_running.is_set() else 0
        summary = []
        for target, kind in targets:
            res = results.get(target)
            if res is None:
                res = ProbeResult(pcfg["count"], 0, [], "error")
            self.store.insert("ping", {
                "ts": time.time(), "cycle": cycle, "host": target, "kind": kind,
                "method": res.method, "sent": res.sent, "received": res.received,
                "loss_pct": res.loss_pct, "rtt_min": res.rtt_min, "rtt_avg": res.rtt_avg,
                "rtt_max": res.rtt_max, "jitter": res.jitter, "during_speed": during})
            rtt = f"{res.rtt_avg:.0f}ms" if res.rtt_avg is not None else "-"
            summary.append(f"{target}[{kind[0]}] perda={res.loss_pct:.0f}% rtt={rtt}")
        log.debug("ping: %s", " | ".join(summary))

        internet = [results.get(t) for t, k in targets if k == "internet"]
        internet = [r for r in internet if r is not None]
        all_down = bool(internet) and all(r.received == 0 for r in internet)
        if all_down and not self.down:
            self.down = True
            self.store.event("outage_start", "nenhum host respondeu")
            log.warning("QUEDA: nenhum host da internet respondeu")
        elif not all_down and self.down:
            self.down = False
            self.store.event("outage_end", "")
            log.warning("conexão voltou")
        return None

    # ---- dns --------------------------------------------------------------

    def dns_cycle(self):
        dcfg = self.cfg["dns"]
        for name in dcfg["names"]:
            ms, ok, err = dns_probe(name, dcfg["timeout_s"])
            self.store.insert("dns", {"ts": time.time(), "name": name, "ms": ms,
                                      "ok": 1 if ok else 0, "error": err})
            log.debug("dns %s: %.0f ms ok=%s", name, ms, ok)
        return None

    # ---- velocidade -------------------------------------------------------

    def bytes_used_today(self):
        start = dt.datetime.combine(dt.date.today(), dt.time.min).timestamp()
        rows = self.store.query("SELECT COALESCE(SUM(bytes),0) AS b FROM speed WHERE ts >= ?", (start,))
        return rows[0]["b"] or 0

    def speed_skip_reason(self):
        scfg = self.cfg["speed"]
        if in_quiet_hours(scfg.get("quiet_hours", [])):
            return "quiet_hours"
        budget = scfg.get("daily_budget_mb", 0)
        if budget and self.bytes_used_today() >= budget * 1_000_000:
            return "daily_budget"
        limit = scfg.get("skip_if_busy_mbps", 0)
        if limit:
            traffic = current_traffic_mbps()
            if traffic is not None and traffic > limit:
                return f"busy:{traffic:.1f}mbps"
        return None

    def speed_cycle(self, force=False):
        scfg = self.cfg["speed"]
        if not force:
            reason = self.speed_skip_reason()
            if reason:
                self.store.event("speed_skipped", reason)
                log.info("teste de velocidade adiado: %s", reason)
                return scfg["busy_retry_s"] if reason.startswith("busy") else None
        self.speed_running.set()
        try:
            self._run_direction("download", scfg["download_urls"],
                                lambda u: measure_download(u, scfg["download_bytes"],
                                                           scfg["max_seconds"], scfg["timeout_s"]))
            self._run_direction("upload", scfg["upload_urls"],
                                lambda u: measure_upload(u, scfg["upload_bytes"], scfg["timeout_s"]))
        finally:
            self.speed_running.clear()
        return None

    def _run_direction(self, direction, urls, fn):
        last_err = None
        for url in urls:
            try:
                r = fn(url)
            except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
                last_err = f"{url}: {exc}"
                log.warning("%s falhou em %s: %s", direction, url, exc)
                continue
            self.store.insert("speed", {"ts": time.time(), "direction": direction, "url": r["url"],
                                        "bytes": r["bytes"], "seconds": r["seconds"], "mbps": r["mbps"],
                                        "ttfb_ms": r["ttfb_ms"], "ok": 1, "error": None})
            log.info("%s: %.1f Mbps (%.1f MB em %.1fs)", direction, r["mbps"],
                     r["bytes"] / 1e6, r["seconds"])
            return r
        self.store.insert("speed", {"ts": time.time(), "direction": direction, "url": None, "bytes": 0,
                                    "seconds": None, "mbps": None, "ttfb_ms": None, "ok": 0,
                                    "error": last_err})
        return None

    # ---- laço -------------------------------------------------------------

    def _loop(self, name, interval_key, fn, trigger=None):
        next_run = time.time()
        while not self.stop.is_set():
            if trigger is not None and trigger.is_set():
                trigger.clear()
                try:
                    fn(force=True)
                except Exception:  # noqa: BLE001
                    log.exception("erro no teste manual de %s", name)
            if time.time() >= next_run:
                started = time.time()
                retry = None
                try:
                    retry = fn()
                except Exception:  # noqa: BLE001
                    log.exception("erro no laço %s", name)
                interval = self.cfg[name][interval_key]
                if retry:
                    interval = min(interval, retry)
                next_run = started + interval
                if next_run <= time.time():
                    next_run = time.time() + 1
            self.stop.wait(min(1.0, max(0.05, next_run - time.time())))

    def run(self):
        self.setup()
        self.store.event("start", f"v{VERSION} {SYSTEM} gateway={self.gateway} ping={self.ping_method}")
        threads = [
            threading.Thread(target=self._loop, args=("ping", "interval_s", self.ping_cycle), daemon=True),
            threading.Thread(target=self._loop, args=("dns", "interval_s", self.dns_cycle), daemon=True),
            threading.Thread(target=self._loop, args=("speed", "interval_s", self.speed_cycle, self.test_now),
                             daemon=True),
        ]
        for th in threads:
            th.start()
        try:
            self._write_heartbeat()
            while not self.stop.is_set():
                self.stop.wait(1.0)
                self._check_signals()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            for th in threads:
                th.join(timeout=5)
            self.store.event("stop", "")
            for key in ("pid", "stop"):
                try:
                    os.remove(self.paths[key])
                except OSError:
                    pass
            log.info("netmon encerrado")

    def _write_heartbeat(self):
        tmp = self.paths["pid"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": self.started, "heartbeat": time.time(),
                       "gateway": self.gateway, "ping_method": self.ping_method, "version": VERSION}, fh)
        os.replace(tmp, self.paths["pid"])
        self._last_beat = time.time()

    def _check_signals(self):
        if time.time() - getattr(self, "_last_beat", 0) >= 10:
            self._write_heartbeat()
        if os.path.exists(self.paths["stop"]):
            log.info("parada solicitada pela interface")
            self.stop.set()
        if os.path.exists(self.paths["testnow"]):
            try:
                os.remove(self.paths["testnow"])
            except OSError:
                pass
            log.info("teste de velocidade solicitado pela interface")
            self.test_now.set()


# --------------------------------------------------------------------------
# Estatística e relatório
# --------------------------------------------------------------------------

def percentile(values, p):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def fmt_num(v, digits=1, suffix=""):
    if v is None:
        return "-"
    return f"{v:.{digits}f}{suffix}"


def fmt_ts(ts, with_date=True):
    d = dt.datetime.fromtimestamp(ts)
    return d.strftime("%d/%m %H:%M") if with_date else d.strftime("%H:%M")


def fmt_minutes(m):
    if m is None:
        return "-"
    if m < 60:
        return f"{m:.0f} min"
    return f"{m / 60:.1f} h"


def aggregate_cycles(pings):
    by_cycle = {}
    for p in pings:
        by_cycle.setdefault(p["cycle"], []).append(p)
    cycles = []
    for cyc, rows in sorted(by_cycle.items()):
        inet = [r for r in rows if r["kind"] == "internet"]
        gws = [r for r in rows if r["kind"] == "gateway"]
        if not inet:
            continue
        rtts = [r["rtt_avg"] for r in inet if r["rtt_avg"] is not None]
        jits = [r["jitter"] for r in inet if r["jitter"] is not None]
        gw = gws[0] if gws else None
        cycles.append({
            "ts": cyc,
            "loss": statistics.median(r["loss_pct"] for r in inet),
            "rtt": statistics.median(rtts) if rtts else None,
            "jitter": statistics.median(jits) if jits else None,
            "down": all((r["received"] or 0) == 0 for r in inet),
            "during_speed": any(r["during_speed"] for r in rows),
            "gw_loss": gw["loss_pct"] if gw else None,
            "gw_rtt": gw["rtt_avg"] if gw else None,
        })
    return cycles


def find_outages(cycles, interval_s):
    outages = []
    current = None
    for c in cycles:
        if c["down"]:
            if current is None:
                current = {"start": c["ts"], "end": c["ts"], "cycles": 1}
            else:
                current["end"] = c["ts"]
                current["cycles"] += 1
        elif current is not None:
            outages.append(current)
            current = None
    if current is not None:
        outages.append(current)
    for o in outages:
        o["minutes"] = (o["end"] - o["start"] + interval_s) / 60.0
    return outages


def by_hour(items, key_ts, key_val, agg):
    buckets = {h: [] for h in range(24)}
    for it in items:
        v = it.get(key_val)
        if v is not None:
            buckets[dt.datetime.fromtimestamp(it[key_ts]).hour].append(v)
    return [agg(buckets[h]) if buckets[h] else None for h in range(24)]


def compute_stats(cfg, store, days):
    end = time.time()
    start = end - days * 86400
    pings = store.query("SELECT * FROM ping WHERE ts >= ? ORDER BY ts", (start,))
    speeds = store.query("SELECT * FROM speed WHERE ts >= ? ORDER BY ts", (start,))
    dns = store.query("SELECT * FROM dns WHERE ts >= ? ORDER BY ts", (start,))
    events = store.query("SELECT * FROM events WHERE ts >= ? ORDER BY ts", (start,))

    interval = cfg["ping"]["interval_s"]
    cycles = aggregate_cycles(pings)
    clean = [c for c in cycles if not c["during_speed"]]
    if len(cycles) >= 2:
        diffs = [b["ts"] - a["ts"] for a, b in zip(cycles, cycles[1:])]
        interval = max(interval, statistics.median(diffs)) if diffs else interval
    outages = find_outages(cycles, interval)
    down_cycles = sum(1 for c in cycles if c["down"])
    monitored_hours = len(cycles) * interval / 3600.0
    weeks = max(monitored_hours / 168.0, 1 / 168.0)

    T = THRESHOLDS
    lossy = [c for c in clean if c["loss"] >= T["loss_cycle_pct"]]
    st = {
        "days": days, "start": start, "end": end, "generated": end,
        "monitored_hours": monitored_hours,
        "cycles": len(cycles),
        "first_ts": cycles[0]["ts"] if cycles else None,
        "last_ts": cycles[-1]["ts"] if cycles else None,
        "availability_pct": 100.0 * (1 - down_cycles / len(cycles)) if cycles else None,
        "outages": outages,
        "outage_minutes": sum(o["minutes"] for o in outages),
        "outages_per_week": len(outages) / weeks if cycles else None,
        "outage_minutes_per_week": sum(o["minutes"] for o in outages) / weeks if cycles else None,
        "loss_avg": statistics.fmean(c["loss"] for c in clean) if clean else None,
        "loss_any_pct": 100.0 * sum(1 for c in clean if c["loss"] > 0) / len(clean) if clean else None,
        "loss_lossy_pct": 100.0 * len(lossy) / len(clean) if clean else None,
        "rtt_p50": percentile([c["rtt"] for c in clean], 50),
        "rtt_p95": percentile([c["rtt"] for c in clean], 95),
        "rtt_max": max((c["rtt"] for c in clean if c["rtt"] is not None), default=None),
        "jitter_p50": percentile([c["jitter"] for c in clean], 50),
        "jitter_p95": percentile([c["jitter"] for c in clean], 95),
        "loss_by_hour": by_hour(clean, "ts", "loss", statistics.fmean),
        "rtt_by_hour": by_hour(clean, "ts", "rtt", statistics.median),
        "cycles_series": cycles,
        "worst_cycles": sorted((c for c in clean if not c["down"]),
                               key=lambda c: (-c["loss"], -(c["rtt"] or 0)))[:10],
    }

    gw = [c for c in clean if c["gw_loss"] is not None]
    st["gateway"] = None
    if gw:
        st["gateway"] = {
            "n": len(gw),
            "loss_avg": statistics.fmean(c["gw_loss"] for c in gw),
            "loss_any_pct": 100.0 * sum(1 for c in gw if c["gw_loss"] > 0) / len(gw),
            "rtt_p50": percentile([c["gw_rtt"] for c in gw], 50),
            "rtt_p95": percentile([c["gw_rtt"] for c in gw], 95),
            "local_only_pct": 100.0 * sum(1 for c in gw if c["gw_loss"] >= T["loss_cycle_pct"]
                                          and c["loss"] >= T["loss_cycle_pct"]) / len(gw),
        }

    plan = cfg.get("plan", {})
    st["speed"] = {}
    for direction, plan_key in (("download", "download_mbps"), ("upload", "upload_mbps")):
        rows = [s for s in speeds if s["direction"] == direction]
        ok = [s for s in rows if s["ok"] and s["mbps"] is not None]
        vals = [s["mbps"] for s in ok]
        plan_mbps = float(plan.get(plan_key) or 0)
        d = {
            "n": len(ok), "failed": len(rows) - len(ok), "plan": plan_mbps,
            "min": min(vals) if vals else None, "max": max(vals) if vals else None,
            "p5": percentile(vals, 5), "p50": percentile(vals, 50), "p95": percentile(vals, 95),
            "series": [(s["ts"], s["mbps"]) for s in ok],
            "by_hour": by_hour(ok, "ts", "mbps", statistics.median),
            "below_half_plan_pct": None, "ttfb_p50": percentile([s["ttfb_ms"] for s in ok], 50),
        }
        if plan_mbps and vals:
            d["below_half_plan_pct"] = 100.0 * sum(1 for v in vals if v < plan_mbps * 0.5) / len(vals)
        st["speed"][direction] = d
    st["speed_skipped"] = sum(1 for e in events if e["kind"] == "speed_skipped")
    st["speed_skip_reasons"] = {}
    for e in events:
        if e["kind"] == "speed_skipped":
            key = (e["detail"] or "").split(":")[0]
            st["speed_skip_reasons"][key] = st["speed_skip_reasons"].get(key, 0) + 1

    dns_ok = [d for d in dns if d["ok"]]
    st["dns"] = {
        "n": len(dns),
        "fail_pct": 100.0 * (len(dns) - len(dns_ok)) / len(dns) if dns else None,
        "p50": percentile([d["ms"] for d in dns_ok], 50),
        "p95": percentile([d["ms"] for d in dns_ok], 95),
    }
    st["events"] = events
    st["verdict"] = build_verdict(st)
    return st


def build_verdict(st):
    """Lista de (nível, título, explicação) e uma conclusão. Nível: ok, atencao, critico."""
    T = THRESHOLDS
    items = []
    local_problem = False
    isp_unstable = False
    isp_slow = False

    if st["cycles"] < 60 or st["monitored_hours"] < 24:
        items.append(("atencao", "Dados ainda insuficientes",
                      f"Há {st['cycles']} ciclos de medição ({st['monitored_hours']:.1f} h). Deixe o monitor "
                      "rodar por pelo menos uma semana, cobrindo dias úteis e fins de semana, antes de decidir."))

    gw = st.get("gateway")
    if gw:
        if gw["loss_avg"] >= T["gateway_loss_pct"] or gw["loss_any_pct"] >= 10:
            local_problem = True
            items.append(("critico", "Perda de pacotes até o roteador",
                          f"Perda média de {gw['loss_avg']:.1f}% e {gw['loss_any_pct']:.0f}% dos ciclos com alguma "
                          "perda entre este computador e o gateway. Isso é problema da rede local (Wi-Fi, cabo, "
                          "roteador), e trocar de provedor não resolve. Teste por cabo ou reposicione o roteador."))
        else:
            items.append(("ok", "Rede local saudável",
                          f"Perda até o roteador de {gw['loss_avg']:.2f}% (p95 de latência "
                          f"{fmt_num(gw['rtt_p95'], 0, ' ms')}). Problemas observados adiante são do provedor."))
    else:
        items.append(("atencao", "Gateway não monitorado",
                      "Sem medição até o roteador não dá para separar problema local de problema do provedor. "
                      "Informe o IP do roteador em ping.gateway no config.json."))

    if st["outages_per_week"] is not None:
        opw, mpw = st["outages_per_week"], st["outage_minutes_per_week"]
        if opw > T["outages_per_week"] or mpw > T["outage_minutes_per_week"]:
            isp_unstable = True
            items.append(("critico", "Quedas frequentes",
                          f"{len(st['outages'])} quedas totais ({fmt_minutes(st['outage_minutes'])}), equivalente a "
                          f"{opw:.1f} quedas e {mpw:.0f} min por semana. Disponibilidade de "
                          f"{st['availability_pct']:.2f}%."))
        elif st["outages"]:
            items.append(("atencao", "Algumas quedas",
                          f"{len(st['outages'])} quedas ({fmt_minutes(st['outage_minutes'])}); disponibilidade "
                          f"{st['availability_pct']:.2f}%."))
        else:
            items.append(("ok", "Sem quedas totais", f"Disponibilidade de {st['availability_pct']:.2f}%."))

    if st["loss_lossy_pct"] is not None:
        if st["loss_lossy_pct"] > T["loss_cycles_share_pct"]:
            if not local_problem:
                isp_unstable = True
            items.append(("critico", "Perda de pacotes recorrente",
                          f"{st['loss_lossy_pct']:.1f}% dos ciclos tiveram perda >= {T['loss_cycle_pct']:.0f}% "
                          f"(média geral {st['loss_avg']:.2f}%). Perda acima de 1 a 2% já degrada chamadas de "
                          "vídeo e VPN, e mais velocidade não corrige isso."))
        elif st["loss_any_pct"] and st["loss_any_pct"] > 15:
            items.append(("atencao", "Perda de pacotes ocasional",
                          f"{st['loss_any_pct']:.0f}% dos ciclos tiveram alguma perda, mas só "
                          f"{st['loss_lossy_pct']:.1f}% passaram de {T['loss_cycle_pct']:.0f}%."))
        else:
            items.append(("ok", "Perda de pacotes baixa",
                          f"Média de {st['loss_avg']:.2f}%; {st['loss_lossy_pct']:.1f}% dos ciclos com perda >= "
                          f"{T['loss_cycle_pct']:.0f}%."))

    if st["rtt_p95"] is not None:
        bad = st["rtt_p95"] > T["rtt_p95_ms"] or (st["jitter_p95"] or 0) > T["jitter_p95_ms"]
        level = "atencao" if bad else "ok"
        if bad and not local_problem:
            isp_unstable = True
        items.append((level, "Latência e jitter " + ("elevados" if bad else "adequados"),
                      f"Latência mediana {fmt_num(st['rtt_p50'], 0, ' ms')}, p95 {fmt_num(st['rtt_p95'], 0, ' ms')}; "
                      f"jitter p95 {fmt_num(st['jitter_p95'], 0, ' ms')}. Referência: p95 até "
                      f"{T['rtt_p95_ms']:.0f} ms e jitter até {T['jitter_p95_ms']:.0f} ms para chamadas boas."))

    for direction, label in (("download", "Download"), ("upload", "Upload")):
        d = st["speed"][direction]
        if not d["n"]:
            items.append(("atencao", f"{label}: sem medições",
                          f"{d['failed']} testes falharam. Verifique speed.download_urls / upload_urls."))
            continue
        base = (f"mediana {d['p50']:.1f} Mbps, p5 {d['p5']:.1f} Mbps, p95 {d['p95']:.1f} Mbps em {d['n']} testes")
        if d["plan"]:
            ratio5, ratio50 = d["p5"] / d["plan"], d["p50"] / d["plan"]
            if ratio5 < T["speed_p5_plan_ratio"] or ratio50 < T["speed_p50_plan_ratio"]:
                isp_slow = True
                items.append(("critico", f"{label} abaixo do contratado",
                              f"{base}. Plano: {d['plan']:.0f} Mbps. Em {d['below_half_plan_pct']:.0f}% dos testes a "
                              "velocidade ficou abaixo da metade do plano. O provedor não entrega o que vende; "
                              "reclame com este relatório em mãos antes de pagar por um plano maior."))
            else:
                spread = (d["p95"] - d["p5"]) / d["p50"] if d["p50"] else 0
                level = "atencao" if spread > 0.6 else "ok"
                items.append((level, f"{label} " + ("oscila bastante" if level == "atencao" else "consistente"),
                              f"{base}. Plano: {d['plan']:.0f} Mbps ({100 * ratio50:.0f}% na mediana)."))
        else:
            spread = (d["p95"] - d["p5"]) / d["p50"] if d["p50"] else 0
            level = "atencao" if spread > 0.6 else "ok"
            items.append((level, f"{label} " + ("oscila bastante" if level == "atencao" else "consistente"),
                          f"{base}. Informe plan.{direction}_mbps no config.json para comparar com o contratado."))

    if st["dns"]["n"]:
        if st["dns"]["fail_pct"] > T["dns_fail_pct"]:
            items.append(("atencao", "Falhas de DNS",
                          f"{st['dns']['fail_pct']:.1f}% das resoluções falharam (p95 {fmt_num(st['dns']['p95'], 0, ' ms')}). "
                          "Trocar o DNS do roteador para 1.1.1.1 ou 8.8.8.8 costuma resolver sem trocar de provedor."))
        else:
            items.append(("ok", "DNS estável",
                          f"{st['dns']['fail_pct']:.1f}% de falhas, p95 {fmt_num(st['dns']['p95'], 0, ' ms')}."))

    if local_problem:
        conclusion = ("Antes de qualquer decisão comercial, resolva a rede local: há perda de pacotes entre o "
                      "computador e o roteador. Repita a análise usando cabo de rede ou após trocar/reposicionar "
                      "o roteador. Só o que sobrar depois disso pode ser atribuído ao provedor.")
    elif isp_unstable and isp_slow:
        conclusion = ("O provedor falha em estabilidade e em velocidade entregue. Abra reclamação formal com este "
                      "relatório; se não houver correção em algumas semanas, a troca de provedor é a saída. Um "
                      "upgrade de plano no mesmo provedor não resolve perda de pacotes nem quedas.")
    elif isp_unstable:
        conclusion = ("A velocidade medida está compatível com o plano, mas a conexão é instável (quedas, perda ou "
                      "latência). Upgrade não ajuda nesse cenário: o caminho é reclamação formal e, persistindo, "
                      "troca de provedor ou de tecnologia (por exemplo, de cabo/rádio para fibra).")
    elif isp_slow:
        conclusion = ("A conexão é estável, mas entrega bem menos do que o contratado. Exija do provedor a "
                      "velocidade do plano antes de considerar upgrade; se ele não entregar, troque.")
    else:
        conclusion = ("A conexão está estável e entrega o esperado. Se ainda assim falta desempenho no uso "
                      "profissional (vários dispositivos, chamadas simultâneas, upload de arquivos grandes), "
                      "aí sim o upgrade de plano faz sentido; não há evidência que justifique trocar de provedor.")
    return {"items": items, "conclusion": conclusion}


# ---- Gráficos SVG -----------------------------------------------------------

PALETTE = {"down": "#1f77b4", "up": "#ff7f0e", "rtt": "#2ca02c", "loss": "#d62728",
           "gw": "#9467bd", "grid": "#e5e7eb", "text": "#4b5563", "plan": "#6b7280"}


def _nice_max(v):
    if not v or v <= 0:
        return 1.0
    mag = 10 ** (len(str(int(v))) - 1)
    for mult in (1, 2, 2.5, 5, 10):
        if v <= mag * mult:
            return mag * mult
    return v


def svg_time_chart(series, t0, t1, title, unit, y_max=None, threshold=None, gap_s=None, height=230):
    """series: lista de dicts {label, color, points:[(ts, val)], kind:'line'|'area'}."""
    width = 960
    ml, mr, mt, mb = 56, 16, 28, 34
    pw, ph = width - ml - mr, height - mt - mb
    all_vals = [v for s in series for _, v in s["points"] if v is not None]
    if y_max is None:
        y_max = _nice_max(max(all_vals + ([threshold] if threshold else []), default=1))
    span = max(t1 - t0, 1)

    def X(ts):
        return ml + (ts - t0) / span * pw

    def Y(v):
        return mt + ph - min(max(v, 0), y_max) / y_max * ph

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="{html.escape(title)}">']
    parts.append(f'<text x="{ml}" y="18" class="ctitle">{html.escape(title)}</text>')
    for i in range(5):
        v = y_max * i / 4
        y = Y(v)
        parts.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" stroke="{PALETTE["grid"]}"/>')
        parts.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{v:g}</text>')
    parts.append(f'<text x="{ml - 6}" y="{mt - 8}" class="tick" text-anchor="end">{html.escape(unit)}</text>')
    # marcas de tempo: um rótulo por dia (ou por 6 h se o período for curto)
    step = 86400 if span > 3 * 86400 else (6 * 3600 if span > 86400 else 3600)
    first = dt.datetime.fromtimestamp(t0).replace(minute=0, second=0, microsecond=0)
    if step >= 86400:
        first = first.replace(hour=0)
    ts = first.timestamp()
    while ts <= t1:
        if ts >= t0:
            x = X(ts)
            parts.append(f'<line x1="{x:.1f}" y1="{mt}" x2="{x:.1f}" y2="{mt + ph}" stroke="{PALETTE["grid"]}"/>')
            label = dt.datetime.fromtimestamp(ts).strftime("%d/%m" if step >= 86400 else "%d/%m %Hh")
            parts.append(f'<text x="{x:.1f}" y="{height - 12}" class="tick" text-anchor="middle">{label}</text>')
        ts += step
    if threshold:
        y = Y(threshold)
        parts.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" stroke="{PALETTE["plan"]}" '
                     f'stroke-dasharray="6 4"/>')
    for s in series:
        pts = [(t, v) for t, v in s["points"] if v is not None]
        segments, cur, prev_t = [], [], None
        for t, v in pts:
            if prev_t is not None and gap_s and t - prev_t > gap_s and cur:
                segments.append(cur)
                cur = []
            cur.append((t, v))
            prev_t = t
        if cur:
            segments.append(cur)
        for seg in segments:
            if len(seg) == 1:
                t, v = seg[0]
                parts.append(f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="2.5" fill="{s["color"]}"/>')
                continue
            coords = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in seg)
            if s.get("kind") == "area":
                base = Y(0)
                parts.append(f'<polygon points="{X(seg[0][0]):.1f},{base:.1f} {coords} {X(seg[-1][0]):.1f},{base:.1f}" '
                             f'fill="{s["color"]}" fill-opacity="0.35" stroke="none"/>')
            parts.append(f'<polyline points="{coords}" fill="none" stroke="{s["color"]}" stroke-width="1.6"/>')
    lx = width - mr
    for s in reversed(series):
        parts.append(f'<text x="{lx}" y="18" class="legend" text-anchor="end" fill="{s["color"]}">'
                     f'{html.escape(s["label"])}</text>')
        lx -= 9 * len(s["label"]) + 18
    parts.append("</svg>")
    return "".join(parts)


def svg_hour_chart(values, title, unit, color, y_max=None, height=200):
    width = 960
    ml, mr, mt, mb = 56, 16, 28, 30
    pw, ph = width - ml - mr, height - mt - mb
    vals = [v for v in values if v is not None]
    if y_max is None:
        y_max = _nice_max(max(vals, default=1))
    bw = pw / 24
    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="{html.escape(title)}">']
    parts.append(f'<text x="{ml}" y="18" class="ctitle">{html.escape(title)}</text>')
    for i in range(5):
        v = y_max * i / 4
        y = mt + ph - v / y_max * ph
        parts.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" stroke="{PALETTE["grid"]}"/>')
        parts.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{v:g}</text>')
    parts.append(f'<text x="{ml - 6}" y="{mt - 8}" class="tick" text-anchor="end">{html.escape(unit)}</text>')
    for h in range(24):
        x = ml + h * bw
        v = values[h]
        if v is not None:
            hh = min(v, y_max) / y_max * ph
            parts.append(f'<rect x="{x + 2:.1f}" y="{mt + ph - hh:.1f}" width="{bw - 4:.1f}" height="{hh:.1f}" '
                         f'fill="{color}"><title>{h:02d}h: {v:.2f} {html.escape(unit)}</title></rect>')
        if h % 2 == 0:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{height - 10}" class="tick" text-anchor="middle">{h:02d}h</text>')
    parts.append("</svg>")
    return "".join(parts)


def downsample(points, max_points, agg):
    """Agrupa pontos (ts, val) em até max_points baldes com a função agg."""
    if len(points) <= max_points:
        return points
    bucket = len(points) / max_points
    out = []
    i = 0.0
    while int(i) < len(points):
        chunk = points[int(i):int(i + bucket)] or points[int(i):int(i) + 1]
        vals = [v for _, v in chunk if v is not None]
        out.append((chunk[0][0], agg(vals) if vals else None))
        i += bucket
    return out


CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f5f6f8;color:#111827}
main{max-width:1000px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:28px 0 10px;color:#374151}
.sub{color:#6b7280;font-size:.9rem;margin-bottom:18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px}
.kpi .l{font-size:.75rem;color:#6b7280;text-transform:uppercase;letter-spacing:.03em}
.kpi .v{font-size:1.35rem;font-weight:600;margin-top:2px}.kpi .s{font-size:.78rem;color:#6b7280}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;margin:10px 0}
.chart{width:100%;height:auto;display:block}.ctitle{font-size:13px;font-weight:600;fill:#374151}
.tick{font-size:11px;fill:#6b7280}.legend{font-size:11px;font-weight:600}
.verdict li{margin:8px 0;padding:8px 10px 8px 12px;border-left:4px solid #9ca3af;background:#fff;border-radius:4px;list-style:none}
.verdict{padding:0}.verdict .ok{border-color:#16a34a}.verdict .atencao{border-color:#f59e0b}.verdict .critico{border-color:#dc2626}
.verdict b{display:block}.conclusion{background:#111827;color:#f9fafb;padding:14px 16px;border-radius:8px;line-height:1.5}
table{width:100%;border-collapse:collapse;font-size:.88rem}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #e5e7eb}
th{color:#6b7280;font-weight:600}.mono{font-variant-numeric:tabular-nums}
.note{font-size:.82rem;color:#6b7280}
"""


def render_html(cfg, st):
    e = html.escape
    T = THRESHOLDS
    t0, t1 = st["start"], st["end"]
    down, up = st["speed"]["download"], st["speed"]["upload"]
    parts = [f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' "
             f"content='width=device-width,initial-scale=1'><title>netmon: relatório de conexão</title>"
             f"<style>{CSS}</style></head><body><main>"]
    parts.append(f"<h1>Relatório da conexão de internet</h1>"
                 f"<div class='sub'>Últimos {st['days']} dias, gerado em {fmt_ts(st['generated'])}. "
                 f"{st['cycles']} ciclos de medição cobrindo {st['monitored_hours']:.1f} h"
                 + (f", de {fmt_ts(st['first_ts'])} a {fmt_ts(st['last_ts'])}" if st["first_ts"] else "")
                 + ".</div>")

    def kpi(label, value, sub=""):
        return f"<div class='kpi'><div class='l'>{e(label)}</div><div class='v'>{e(value)}</div><div class='s'>{e(sub)}</div></div>"

    plan_d = f" | plano {down['plan']:.0f}" if down["plan"] else ""
    plan_u = f" | plano {up['plan']:.0f}" if up["plan"] else ""
    parts.append("<div class='kpis'>")
    parts.append(kpi("Disponibilidade", fmt_num(st["availability_pct"], 2, "%"),
                     f"{len(st['outages'])} quedas, {fmt_minutes(st['outage_minutes'])}"))
    parts.append(kpi("Perda de pacotes", fmt_num(st["loss_avg"], 2, "%"),
                     f"{fmt_num(st['loss_lossy_pct'], 1, '%')} dos ciclos com perda >= {T['loss_cycle_pct']:.0f}%"))
    parts.append(kpi("Latência p50 / p95", f"{fmt_num(st['rtt_p50'], 0)} / {fmt_num(st['rtt_p95'], 0)} ms",
                     f"jitter p95 {fmt_num(st['jitter_p95'], 0, ' ms')}"))
    parts.append(kpi("Download mediana", fmt_num(down["p50"], 1, " Mbps"),
                     f"p5 {fmt_num(down['p5'], 1)} | p95 {fmt_num(down['p95'], 1)}{plan_d} | {down['n']} testes"))
    parts.append(kpi("Upload mediana", fmt_num(up["p50"], 1, " Mbps"),
                     f"p5 {fmt_num(up['p5'], 1)} | p95 {fmt_num(up['p95'], 1)}{plan_u} | {up['n']} testes"))
    parts.append(kpi("DNS p50 / p95", f"{fmt_num(st['dns']['p50'], 0)} / {fmt_num(st['dns']['p95'], 0)} ms",
                     f"{fmt_num(st['dns']['fail_pct'], 1, '%')} de falhas"))
    if st["gateway"]:
        parts.append(kpi("Perda até o roteador", fmt_num(st["gateway"]["loss_avg"], 2, "%"),
                         f"latência p95 {fmt_num(st['gateway']['rtt_p95'], 0, ' ms')}"))
    parts.append("</div>")

    parts.append("<h2>Diagnóstico</h2><ul class='verdict'>")
    for level, title, text in st["verdict"]["items"]:
        parts.append(f"<li class='{level}'><b>{e(title)}</b>{e(text)}</li>")
    parts.append("</ul>")
    parts.append(f"<div class='conclusion'>{e(st['verdict']['conclusion'])}</div>")
    parts.append("<p class='note'>Heurísticas, não um veredito técnico. Perda de pacotes e quedas são problemas de "
                 "estabilidade; velocidade abaixo do plano é problema de entrega; velocidade no plano mas insuficiente "
                 "é caso de upgrade. Os testes de velocidade usam transferências curtas para não atrapalhar o uso, "
                 "então os valores absolutos tendem a ficar um pouco abaixo de um teste completo. A tendência e a "
                 "variação são o que importa.</p>")

    parts.append("<h2>Velocidade ao longo do tempo</h2><div class='card'>")
    plan_line = down["plan"] or None
    parts.append(svg_time_chart([
        {"label": "Download", "color": PALETTE["down"], "points": down["series"]},
        {"label": "Upload", "color": PALETTE["up"], "points": up["series"]},
    ], t0, t1, "Mbps por teste (linha tracejada: plano de download)", "Mbps", threshold=plan_line,
        gap_s=4 * cfg["speed"]["interval_s"]))
    parts.append("</div>")

    cyc = st["cycles_series"]
    loss_pts = downsample([(c["ts"], c["loss"]) for c in cyc], 1500, max)
    rtt_pts = downsample([(c["ts"], c["rtt"]) for c in cyc], 1500, statistics.median)
    bucket_s = (t1 - t0) / 1500 if len(cyc) > 1500 else 0
    gap = max(5 * cfg["ping"]["interval_s"], 4 * bucket_s)
    parts.append("<h2>Perda de pacotes e latência</h2><div class='card'>")
    parts.append(svg_time_chart([{"label": "Perda %", "color": PALETTE["loss"], "points": loss_pts, "kind": "area"}],
                                t0, t1, "Perda de pacotes por ciclo (%)", "%", y_max=100, gap_s=gap))
    parts.append("</div><div class='card'>")
    rtt_series = [{"label": "Internet", "color": PALETTE["rtt"], "points": rtt_pts}]
    if st["gateway"]:
        gw_pts = downsample([(c["ts"], c["gw_rtt"]) for c in cyc], 1500, statistics.median)
        rtt_series.append({"label": "Roteador", "color": PALETTE["gw"], "points": gw_pts})
    parts.append(svg_time_chart(rtt_series, t0, t1, "Latência média por ciclo (ms)", "ms", gap_s=gap))
    parts.append("</div>")

    parts.append("<h2>Por hora do dia</h2><div class='card'>")
    parts.append(svg_hour_chart(st["loss_by_hour"], "Perda média por hora (%)", "%", PALETTE["loss"]))
    parts.append("</div><div class='card'>")
    parts.append(svg_hour_chart(st["rtt_by_hour"], "Latência mediana por hora (ms)", "ms", PALETTE["rtt"]))
    parts.append("</div><div class='card'>")
    parts.append(svg_hour_chart(down["by_hour"], "Download mediano por hora (Mbps)", "Mbps", PALETTE["down"],
                                y_max=_nice_max(max([v for v in down["by_hour"] if v] + [down["plan"] or 0], default=1))))
    parts.append("</div><div class='card'>")
    parts.append(svg_hour_chart(up["by_hour"], "Upload mediano por hora (Mbps)", "Mbps", PALETTE["up"],
                                y_max=_nice_max(max([v for v in up["by_hour"] if v] + [up["plan"] or 0], default=1))))
    parts.append("</div>")

    if st["outages"]:
        parts.append("<h2>Quedas</h2><div class='card'><table><tr><th>Início</th><th>Fim</th><th>Duração</th></tr>")
        for o in st["outages"]:
            parts.append(f"<tr><td>{fmt_ts(o['start'])}</td><td>{fmt_ts(o['end'])}</td>"
                         f"<td class='mono'>{fmt_minutes(o['minutes'])}</td></tr>")
        parts.append("</table></div>")

    if st["worst_cycles"]:
        parts.append("<h2>Piores momentos (fora de quedas totais)</h2><div class='card'><table>"
                     "<tr><th>Quando</th><th>Perda</th><th>Latência</th><th>Jitter</th><th>Perda até o roteador</th></tr>")
        for c in st["worst_cycles"]:
            if c["loss"] <= 0 and (c["rtt"] or 0) < T["rtt_p95_ms"]:
                continue
            parts.append(f"<tr><td>{fmt_ts(c['ts'])}</td><td class='mono'>{c['loss']:.0f}%</td>"
                         f"<td class='mono'>{fmt_num(c['rtt'], 0, ' ms')}</td><td class='mono'>{fmt_num(c['jitter'], 0, ' ms')}</td>"
                         f"<td class='mono'>{fmt_num(c['gw_loss'], 0, '%')}</td></tr>")
        parts.append("</table></div>")

    skipped = st["speed_skip_reasons"]
    parts.append("<h2>Sobre a coleta</h2><div class='card'><table>")
    rows = [
        ("Ciclos de ping", f"{st['cycles']} (a cada {cfg['ping']['interval_s']} s, {cfg['ping']['count']} pacotes "
                           f"para {', '.join(cfg['ping']['hosts'])})"),
        ("Testes de velocidade", f"{down['n']} download, {up['n']} upload; {down['failed'] + up['failed']} falhas; "
                                 f"{st['speed_skipped']} adiados"
                                 + (f" ({', '.join(f'{k}: {v}' for k, v in skipped.items())})" if skipped else "")),
        ("Tamanho por teste", f"{cfg['speed']['download_bytes'] / 1e6:.0f} MB download, "
                              f"{cfg['speed']['upload_bytes'] / 1e6:.0f} MB upload, a cada "
                              f"{cfg['speed']['interval_s'] / 60:.0f} min"),
        ("Consultas DNS", f"{st['dns']['n']} ({', '.join(cfg['dns']['names'])})"),
        ("Tempo até o 1º byte (download)", fmt_num(down["ttfb_p50"], 0, " ms na mediana")),
    ]
    for k, v in rows:
        parts.append(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>")
    parts.append("</table></div>")
    parts.append(f"<p class='note'>netmon {VERSION}. Banco: {e(cfg['db_path'])}</p>")
    parts.append("</main></body></html>")
    return "".join(parts)


def render_text(cfg, st):
    down, up = st["speed"]["download"], st["speed"]["upload"]
    lines = [
        f"netmon: últimos {st['days']} dias ({st['cycles']} ciclos, {st['monitored_hours']:.1f} h monitoradas)",
        "",
        f"Disponibilidade     {fmt_num(st['availability_pct'], 2, '%')}  quedas: {len(st['outages'])} "
        f"({fmt_minutes(st['outage_minutes'])})",
        f"Perda de pacotes    média {fmt_num(st['loss_avg'], 2, '%')}, {fmt_num(st['loss_lossy_pct'], 1, '%')} dos ciclos "
        f">= {THRESHOLDS['loss_cycle_pct']:.0f}%",
        f"Latência (ms)       p50 {fmt_num(st['rtt_p50'], 0)}  p95 {fmt_num(st['rtt_p95'], 0)}  max {fmt_num(st['rtt_max'], 0)}"
        f"  jitter p95 {fmt_num(st['jitter_p95'], 0)}",
        f"Download (Mbps)     p5 {fmt_num(down['p5'])}  p50 {fmt_num(down['p50'])}  p95 {fmt_num(down['p95'])}"
        f"  n={down['n']}" + (f"  plano={down['plan']:.0f}" if down["plan"] else ""),
        f"Upload (Mbps)       p5 {fmt_num(up['p5'])}  p50 {fmt_num(up['p50'])}  p95 {fmt_num(up['p95'])}"
        f"  n={up['n']}" + (f"  plano={up['plan']:.0f}" if up["plan"] else ""),
        f"DNS (ms)            p50 {fmt_num(st['dns']['p50'], 0)}  p95 {fmt_num(st['dns']['p95'], 0)}  falhas "
        f"{fmt_num(st['dns']['fail_pct'], 1, '%')}",
    ]
    if st["gateway"]:
        lines.append(f"Roteador            perda {fmt_num(st['gateway']['loss_avg'], 2, '%')}  latência p95 "
                     f"{fmt_num(st['gateway']['rtt_p95'], 0, ' ms')}")
    lines.append("")
    lines.append("Diagnóstico:")
    marks = {"ok": "[ok]  ", "atencao": "[!]   ", "critico": "[!!]  "}
    for level, title, text in st["verdict"]["items"]:
        lines.append(f"  {marks[level]}{title}: {text}")
    lines.append("")
    lines.append("Conclusão: " + st["verdict"]["conclusion"])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_run(args, cfg):
    setup_logging(cfg, to_console=not args.quiet)
    if monitor_status(cfg)["running"]:
        log.error("já existe um monitor rodando (pid %s); saindo", monitor_status(cfg)["pid"])
        return
    store = Store(cfg["db_path"])
    mon = Monitor(cfg, store)
    try:
        import signal
        signal.signal(signal.SIGINT, lambda *_: mon.stop.set())
        signal.signal(signal.SIGTERM, lambda *_: mon.stop.set())
    except (ValueError, OSError, AttributeError):
        pass
    mon.run()
    store.close()


def cmd_once(args, cfg):
    setup_logging(cfg, to_console=True)
    log.setLevel(logging.DEBUG)
    store = Store(cfg["db_path"])
    mon = Monitor(cfg, store)
    mon.setup()
    print(f"Sistema: {SYSTEM} | gateway: {mon.gateway or 'não detectado'} | método de ping: {mon.ping_method}")
    print("Ping...")
    mon.ping_cycle()
    for r in store.query("SELECT host, kind, method, loss_pct, rtt_avg, jitter FROM ping ORDER BY id DESC LIMIT ?",
                         (len(cfg["ping"]["hosts"]) + 1,)):
        print(f"  {r['host']:<16} {r['kind']:<8} {r['method']:<5} perda {r['loss_pct']:5.1f}%  "
              f"rtt {fmt_num(r['rtt_avg'], 1, ' ms'):>10}  jitter {fmt_num(r['jitter'], 1, ' ms')}")
    print("DNS...")
    mon.dns_cycle()
    for r in store.query("SELECT name, ms, ok FROM dns ORDER BY id DESC LIMIT ?", (len(cfg["dns"]["names"]),)):
        print(f"  {r['name']:<24} {r['ms']:6.0f} ms  {'ok' if r['ok'] else 'FALHOU'}")
    traffic = current_traffic_mbps(1.0)
    print(f"Tráfego atual: {fmt_num(traffic, 2, ' Mbps') if traffic is not None else 'não mensurável neste sistema'}")
    print("Velocidade...")
    mon.speed_cycle(force=True)
    for r in store.query("SELECT direction, mbps, bytes, seconds, ok, error FROM speed ORDER BY id DESC LIMIT 2"):
        if r["ok"]:
            print(f"  {r['direction']:<9} {r['mbps']:7.1f} Mbps  ({r['bytes'] / 1e6:.1f} MB em {r['seconds']:.1f} s)")
        else:
            print(f"  {r['direction']:<9} FALHOU: {r['error']}")
    store.close()


def cmd_summary(args, cfg):
    store = Store(cfg["db_path"])
    st = compute_stats(cfg, store, args.days)
    print(render_text(cfg, st))
    store.close()


def cmd_report(args, cfg):
    store = Store(cfg["db_path"])
    st = compute_stats(cfg, store, args.days)
    out = args.out or os.path.join(os.path.dirname(cfg["db_path"]), "report.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render_html(cfg, st))
    print(f"Relatório gravado em {out}")
    store.close()
    if args.open:
        webbrowser.open("file://" + os.path.abspath(out))


def cmd_export(args, cfg):
    if args.table not in ("ping", "dns", "speed", "events"):
        sys.exit("tabela deve ser ping, dns, speed ou events")
    store = Store(cfg["db_path"])
    start = time.time() - args.days * 86400
    rows = store.query(f"SELECT * FROM {args.table} WHERE ts >= ? ORDER BY ts", (start,))
    out = args.out or f"{args.table}.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=["datetime"] + list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                r = dict(r)
                r["datetime"] = dt.datetime.fromtimestamp(r["ts"]).isoformat(timespec="seconds")
                w.writerow(r)
    print(f"{len(rows)} linhas exportadas para {out}")
    store.close()


def cmd_stop(args, cfg):
    print("monitor parado" if request_stop(cfg) else "monitor não respondeu ao pedido de parada")


def cmd_start(args, cfg):
    st = monitor_status(cfg)
    if st["running"]:
        print(f"monitor já está rodando (pid {st['pid']})")
    else:
        pid = spawn_monitor(cfg)
        print(f"monitor iniciado em segundo plano (pid {pid}); log em {cfg['log_path']}")


def cmd_status(args, cfg):
    st = monitor_status(cfg)
    if st["running"]:
        print(f"rodando: pid {st['pid']}, desde {fmt_ts(st['started'])}, gateway {st.get('gateway')}, "
              f"ping {st.get('ping_method')}")
    else:
        print("parado")


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP, description="Monitor leve de qualidade da conexão de internet.")
    ap.add_argument("--config", help="caminho do config.json (padrão: ao lado do script)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="monitora continuamente (primeiro plano)")
    p.add_argument("--quiet", action="store_true", help="não escreve log no console (só no arquivo)")
    sub.add_parser("start", help="inicia o monitor em segundo plano")
    sub.add_parser("stop", help="para o monitor em segundo plano")
    sub.add_parser("status", help="mostra se o monitor está rodando")
    sub.add_parser("once", help="executa todas as medições uma vez e mostra o resultado")
    p = sub.add_parser("summary", help="resumo em texto")
    p.add_argument("--days", type=float, default=7)
    p = sub.add_parser("report", help="gera relatório HTML")
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--out", help="arquivo de saída (padrão: report.html ao lado do banco)")
    p.add_argument("--open", action="store_true", help="abre no navegador")
    p = sub.add_parser("export", help="exporta uma tabela para CSV")
    p.add_argument("--table", default="speed", help="ping, dns, speed ou events")
    p.add_argument("--days", type=float, default=30)
    p.add_argument("--out")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    {"run": cmd_run, "once": cmd_once, "summary": cmd_summary, "report": cmd_report,
     "export": cmd_export, "start": cmd_start, "stop": cmd_stop, "status": cmd_status}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
