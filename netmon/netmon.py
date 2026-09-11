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
VERSION = "1.5.1"
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
        local = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        new_dir = os.path.join(local, APP)
        # Instalações feitas pelo Instalar.bat guardam os dados na pasta do
        # programa. Se ela já tem um banco e a pasta nova não, continua nela
        # para que a troca para o instalador não pareça perda de histórico.
        legacy = os.path.join(local, "Programs", APP)
        if (os.path.exists(os.path.join(legacy, "netmon.db"))
                and not os.path.exists(os.path.join(new_dir, "netmon.db"))):
            return legacy
        return new_dir
    if IS_MAC:
        return os.path.expanduser(f"~/Library/Application Support/{APP}")
    return os.path.expanduser(f"~/.local/share/{APP}")


DATA_DIR = default_data_dir()
USER_AGENT = f"{APP}/{VERSION} (+https://github.com/caiomperet/Claude-Skills)"

DEFAULT_CONFIG = {
    "db_path": "netmon.db",
    "log_path": "netmon.log",
    "log_level": "INFO",
    "retention_days": 0,
    "call_mode": {
        "schedule": [],
        "skip_speed": True,
        "host": "1.1.1.1",
        "burst_after_s": 2.5,
    },
    "update": {"repo": "caiomperet/Claude-Skills", "check_on_start": True},
    "plan": {
        "download_mbps": 0,
        "upload_mbps": 0,
    },
    "ping": {
        "interval_s": 60,
        "hosts": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
        "gateway": "auto",
        "local_hops": "auto",
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
        "max_download_bytes": 80000000,
        "warmup_s": 0.5,
        "min_window_s": 0.5,
        "upload_bytes": 4000000,
        "max_seconds": 12,
        "timeout_s": 25,
        "download_urls": ["https://speed.cloudflare.com/__down?bytes={bytes}"],
        "upload_urls": ["https://speed.cloudflare.com/__up"],
        "skip_if_busy_mbps": 2.0,
        "busy_retry_s": 300,
        "daily_budget_mb": 700,
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
CREATE TABLE IF NOT EXISTS call_minute (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    host TEXT NOT NULL,
    kind TEXT NOT NULL,
    sent INTEGER,
    received INTEGER,
    rtt_min REAL,
    rtt_avg REAL,
    rtt_max REAL
);
CREATE INDEX IF NOT EXISTS call_minute_ts ON call_minute(ts);
CREATE TABLE IF NOT EXISTS call_burst (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    host TEXT NOT NULL,
    kind TEXT NOT NULL,
    duration_s REAL,
    lost INTEGER
);
CREATE INDEX IF NOT EXISTS call_burst_ts ON call_burst(ts);
CREATE TABLE IF NOT EXISTS marks (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS marks_ts ON marks(ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


# Colunas acrescentadas depois da primeira versão. SQLite não tem
# "ADD COLUMN IF NOT EXISTS", então conferimos antes de alterar.
MIGRATIONS = [
    ("call_burst", "suspect", "INTEGER DEFAULT 0"),
    ("call_minute", "suspect", "INTEGER DEFAULT 0"),
    ("speed", "confident", "INTEGER DEFAULT 1"),
    ("speed", "window_s", "REAL"),
    ("ping", "hop", "INTEGER DEFAULT 0"),
]


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        for table, col, decl in MIGRATIONS:
            cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.conn.commit()
        self.mark_artifacts()
        self.mark_weak_speed()

    def coverage_blocks(self):
        """Intervalos em que o modo chamada realmente estava medindo.

        Uma linha por minuto é gravada mesmo quando nada responde, então falta
        de linhas significa processo parado (computador suspenso), não queda."""
        rows = self.conn.execute("SELECT ts, sent FROM call_minute WHERE kind='internet' AND "
                                 "COALESCE(suspect,0)=0 ORDER BY ts").fetchall()
        blocks = []
        for ts, sent in rows:
            end = ts + min(sent or 60, 90)
            if blocks and ts <= blocks[-1][1] + 5:
                blocks[-1][1] = max(blocks[-1][1], end)
            else:
                blocks.append([ts, end])
        return blocks

    def mark_weak_speed(self):
        """Medições antigas sem janela registrada: curtas demais para valer.

        Só marca; não filtra, porque descartar as curtas sobraria justamente as
        lentas e puxaria a mediana para baixo."""
        cur = self.conn.execute("UPDATE speed SET confident=0 WHERE window_s IS NULL AND ok=1 AND "
                                "((direction='download' AND seconds < 1.0) OR "
                                " (direction='upload' AND seconds < 0.4))")
        self.conn.commit()
        return cur.rowcount

    def mark_artifacts(self):
        """Marca como suspeitas as linhas produzidas por suspensão do computador.

        Não apaga nada: só exclui das estatísticas o que não foi medido."""
        cur = self.conn.execute("UPDATE call_minute SET suspect=1 WHERE sent > 90 AND COALESCE(suspect,0)=0")
        minutes = cur.rowcount
        blocks = self.coverage_blocks()
        bursts = 0
        for bid, bts, dur in self.conn.execute(
                "SELECT id, ts, duration_s FROM call_burst WHERE COALESCE(suspect,0)=0").fetchall():
            a, b = bts, bts + (dur or 0)
            if not any(start <= a and b <= end + 1 for start, end in blocks):
                self.conn.execute("UPDATE call_burst SET suspect=1 WHERE id=?", (bid,))
                bursts += 1
        self.conn.commit()
        if minutes or bursts:
            log.info("histórico: %d minutos e %d rajadas marcados como artefato de suspensão",
                     minutes, bursts)
        return minutes, bursts

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


def is_private(ip):
    try:
        parts = [int(x) for x in ip.split(".")]
    except ValueError:
        return False
    if len(parts) != 4 or any(not 0 <= p <= 255 for p in parts):
        return False
    a, b = parts[0], parts[1]
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def discover_local_hops(target="1.1.1.1", max_hops=4, timeout_s=30):
    """Descobre os roteadores da própria casa no caminho até a internet.

    Numa rede com mesh atrás do roteador da operadora, isso devolve os dois:
    o primeiro salto é o Wi-Fi, o segundo é o equipamento da operadora."""
    if IS_WINDOWS:
        cmd = ["tracert", "-d", "-h", str(max_hops), "-w", "1000", target]
    else:
        cmd = ["traceroute", "-n", "-m", str(max_hops), "-w", "1", "-q", "1", target]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                             timeout=timeout_s, **_subprocess_flags()).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("descoberta de saltos locais indisponível: %s", exc)
        return []
    hops = []
    for line in out.splitlines():
        for ip in re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line):
            if ip != target and is_private(ip) and ip not in hops:
                hops.append(ip)
    return hops[:3]


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

def measure_download(url_tpl, max_bytes, max_seconds, timeout_s, warmup_s=0.5, min_window_s=0.5):
    """Mede o download descartando a partida lenta do TCP.

    Numa conexão rápida, um arquivo pequeno termina antes de a janela do TCP
    abrir, e o tempo até o primeiro byte domina a conta. Por isso a medição
    começa só depois de warmup_s de transferência, e o resultado é marcado
    como pouco confiável quando a janela medida ficou curta demais."""
    url = url_tpl.replace("{bytes}", str(max_bytes))
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Cache-Control": "no-cache", "Pragma": "no-cache"})
    total = 0
    first_t = None
    mark_t = None
    mark_bytes = 0
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        while True:
            chunk = resp.read(256 * 1024)
            now = time.perf_counter()
            if not chunk:
                break
            if first_t is None:
                first_t = now
            total += len(chunk)
            if mark_t is None and now - first_t >= warmup_s:
                mark_t, mark_bytes = now, total
            if total >= max_bytes or now - t0 >= max_seconds:
                break
    end = time.perf_counter()
    if first_t is None or total == 0:
        raise RuntimeError("resposta vazia")
    if mark_t is not None and end - mark_t >= 0.2 and total > mark_bytes:
        window = end - mark_t
        mbps = (total - mark_bytes) * 8 / window / 1e6
    else:
        window = max(end - first_t, 1e-6)
        mbps = total * 8 / window / 1e6
    return {"url": url, "bytes": total, "seconds": end - t0, "mbps": mbps,
            "ttfb_ms": (first_t - t0) * 1000.0, "window_s": window,
            "confident": 1 if window >= min_window_s else 0}


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
            "ttfb_ms": None, "window_s": secs, "confident": 1 if secs >= 0.4 else 0}


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
            "testnow": os.path.join(d, "netmon.testnow"), "callon": os.path.join(d, "netmon.callon"),
            "calloff": os.path.join(d, "netmon.calloff"), "state": os.path.join(d, "netmon.state.json")}


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


def request_call_mode(cfg, on):
    """Liga ou desliga manualmente o modo chamada no monitor em execução."""
    with open(control_paths(cfg)["callon" if on else "calloff"], "w", encoding="utf-8") as fh:
        fh.write(str(time.time()))


def load_state(cfg):
    try:
        with open(control_paths(cfg)["state"], encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(cfg, changes):
    state = load_state(cfg)
    state.update(changes)
    tmp = control_paths(cfg)["state"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, control_paths(cfg)["state"])


WEEKDAYS = {"seg": 0, "ter": 1, "qua": 2, "qui": 3, "sex": 4, "sab": 5, "dom": 6,
            "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_schedule_entry(entry):
    """'seg-sex 09:00-18:00' -> (set de dias, 'HH:MM-HH:MM'). Sem dias = todos."""
    parts = entry.strip().split()
    if not parts:
        return None
    hours = parts[-1]
    if not re.fullmatch(r"\d{1,2}:\d{2}-\d{1,2}:\d{2}", hours):
        raise ValueError(f"horário inválido: {entry!r}")
    days = set(range(7))
    if len(parts) > 1:
        days = set()
        for token in " ".join(parts[:-1]).lower().replace(",", " ").split():
            token = token.replace("á", "a")
            if "-" in token:
                a, b = token.split("-", 1)
                if a not in WEEKDAYS or b not in WEEKDAYS:
                    raise ValueError(f"dia inválido: {token!r}")
                i, j = WEEKDAYS[a], WEEKDAYS[b]
                days |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
            elif token in WEEKDAYS:
                days.add(WEEKDAYS[token])
            else:
                raise ValueError(f"dia inválido: {token!r}")
    return days, hours


def schedule_active(entries, now=None):
    now = now or dt.datetime.now()
    for entry in entries:
        try:
            parsed = parse_schedule_entry(entry)
        except ValueError as exc:
            log.warning("agenda do modo chamada: %s", exc)
            continue
        if not parsed:
            continue
        days, hours = parsed
        # faixa que vira a meia-noite conta para o dia em que começou
        if now.weekday() in days and in_quiet_hours([hours], now):
            return True
        prev = (now - dt.timedelta(days=1)).weekday()
        a, b = hours.split("-")
        if a > b and prev in days and in_quiet_hours([hours], now) and now.strftime("%H:%M") < b:
            return True
    return False


# ---- Modo chamada: ping contínuo, 1 por segundo -----------------------------

def build_continuous_ping_cmd(host):
    if IS_WINDOWS:
        return ["ping", "-t", "-w", "1000", host]
    if IS_MAC:
        return ["ping", "-i", "1", "-W", "1000", host]
    return ["ping", "-i", "1", "-W", "1", host]


class CallMonitor:
    """Sonda contínua (1 pacote/s) para cada alvo. Grava um resumo por minuto e
    uma linha por rajada de perda (intervalo sem resposta maior que burst_after_s)."""

    def __init__(self, cfg, store, targets, method):
        self.cfg = cfg
        self.store = store
        self.targets = targets            # [(host, kind)]
        self.method = method
        self.burst_after = float(cfg["call_mode"].get("burst_after_s", 2.5))
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.procs = []
        self.threads = []
        now = time.time()
        self.state = {h: {"kind": k, "rtts": [], "minute_start": now, "last_reply": None, "started": now,
                          "burst_start": None, "bursts": 0} for h, k in targets}

    def start(self):
        for host, _ in self.targets:
            fn = self._run_icmp if self.method == "icmp" else self._run_tcp
            th = threading.Thread(target=fn, args=(host,), daemon=True)
            th.start()
            self.threads.append(th)
        th = threading.Thread(target=self._watchdog, daemon=True)
        th.start()
        self.threads.append(th)

    def close(self):
        self.stop.set()
        for proc in self.procs:
            try:
                proc.kill()
            except OSError:
                pass
        for th in self.threads:
            th.join(timeout=3)
        now = time.time()
        with self.lock:
            for host, st in self.state.items():
                self._flush_minute(host, st, now)
                if st["burst_start"] is not None:
                    self._close_burst(host, st, now)

    # ---- coleta ----------------------------------------------------------

    def _reply(self, host, rtt):
        now = time.time()
        with self.lock:
            st = self.state[host]
            st["rtts"].append(rtt)
            if st["burst_start"] is not None:
                self._close_burst(host, st, now)
            st["last_reply"] = now

    def _run_icmp(self, host):
        # O ping contínuo pode morrer quando a máquina suspende ou troca de
        # rede; por isso ele é reiniciado enquanto o modo chamada estiver ativo.
        while not self.stop.is_set():
            try:
                proc = subprocess.Popen(build_continuous_ping_cmd(host), stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, errors="replace", bufsize=1,
                                        **_subprocess_flags())
            except OSError as exc:
                log.warning("modo chamada: ping indisponível (%s); usando TCP", exc)
                return self._run_tcp(host)
            self.procs.append(proc)
            for line in proc.stdout:
                if self.stop.is_set():
                    break
                if "ttl" in line.lower():
                    m = RTT_RE.search(line)
                    if m:
                        self._reply(host, float(m.group(1).replace(",", ".")))
            try:
                proc.kill()
            except OSError:
                pass
            if not self.stop.is_set():
                log.info("modo chamada: reiniciando o ping contínuo para %s", host)
                self.stop.wait(2)

    def _run_tcp(self, host):
        h, port = split_host_port(host, self.cfg["ping"]["tcp_port"])
        while not self.stop.is_set():
            t0 = time.perf_counter()
            try:
                sock = socket.create_connection((h, port), timeout=1.0)
                sock.close()
                self._reply(host, (time.perf_counter() - t0) * 1000.0)
            except OSError:
                pass
            self.stop.wait(max(0.05, 1.0 - (time.perf_counter() - t0)))

    # ---- contabilidade ---------------------------------------------------

    def after_suspend(self, now):
        """O computador ficou suspenso: descarta tudo que estava em curso."""
        with self.lock:
            for st in self.state.values():
                st["burst_start"] = None
                st["rtts"] = []
                st["last_reply"] = now
                st["minute_start"] = now

    def _watchdog(self):
        last_tick = time.time()
        while not self.stop.wait(0.5):
            now = time.time()
            gap = now - last_tick
            last_tick = now
            if gap > 10:
                # O laço roda a cada 0.5 s. Um salto grande significa processo
                # congelado, não rede parada: nada do intervalo vale.
                self.after_suspend(now)
                continue
            with self.lock:
                for host, st in self.state.items():
                    ref = st["last_reply"] if st["last_reply"] is not None else st["started"] + 5
                    if st["burst_start"] is None and now - ref > self.burst_after:
                        st["burst_start"] = ref + 1.0
                        log.warning("modo chamada: %s sem resposta desde %s", host, fmt_ts(ref))
                    if now - st["minute_start"] >= 60:
                        self._flush_minute(host, st, now)

    def _close_burst(self, host, st, now):
        duration = max(0.0, now - st["burst_start"])
        self.store.insert("call_burst", {"ts": st["burst_start"], "host": host, "kind": st["kind"],
                                         "duration_s": duration, "lost": int(round(duration))})
        st["bursts"] += 1
        log.warning("modo chamada: %s voltou após %.1f s sem resposta", host, duration)
        st["burst_start"] = None

    def _flush_minute(self, host, st, now):
        elapsed = now - st["minute_start"]
        if elapsed < 5:
            return
        rtts = st["rtts"]
        self.store.insert("call_minute", {
            "ts": st["minute_start"], "host": host, "kind": st["kind"],
            "suspect": 1 if elapsed > 90 else 0,
            "sent": max(int(round(elapsed)), len(rtts)), "received": len(rtts),
            "rtt_min": min(rtts) if rtts else None, "rtt_avg": statistics.fmean(rtts) if rtts else None,
            "rtt_max": max(rtts) if rtts else None})
        st["rtts"] = []
        st["minute_start"] = now


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
        self.call = None
        self.call_reason = None
        # None segue a agenda; True força ligado; False pausa a agenda até o fim do período atual
        self.call_manual = load_state(cfg).get("call_manual")

    def setup(self):
        pcfg = self.cfg["ping"]
        configured = pcfg.get("local_hops", "auto")
        if isinstance(configured, list) and configured:
            self.hops = [h for h in configured if h]
        else:
            gw = pcfg.get("gateway", "auto")
            first = detect_gateway() if gw == "auto" else (gw or None)
            self.hops = [first] if first else []
            for ip in discover_local_hops(pcfg["hosts"][0]):
                if ip not in self.hops:
                    self.hops.append(ip)
            self.hops = self.hops[:3]
        self.gateway = self.hops[0] if self.hops else None
        self.ping_method = choose_ping_method(pcfg, pcfg["hosts"][0])
        self.download_bytes = int(load_state(self.cfg).get("download_bytes")
                                  or self.cfg["speed"]["download_bytes"])
        log.info("netmon %s em %s | saltos locais=%s | método ping=%s | banco=%s",
                 VERSION, SYSTEM, ", ".join(self.hops) or "nenhum", self.ping_method, self.store.path)

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
        targets = [(h, "internet", 0) for h in pcfg["hosts"]]
        for i, host in enumerate(self.hops):
            targets.append((host, "gateway" if i == 0 else "local", i + 1))
        results = {}

        def work(target):
            try:
                results[target] = self.probe(target)
            except Exception as exc:  # noqa: BLE001
                log.error("sonda %s falhou: %s", target, exc)
                results[target] = None

        threads = [threading.Thread(target=work, args=(t,), daemon=True) for t, _, _ in targets]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        during = 1 if self.speed_running.is_set() else 0
        summary = []
        for target, kind, hop in targets:
            res = results.get(target)
            if res is None:
                res = ProbeResult(pcfg["count"], 0, [], "error")
            self.store.insert("ping", {
                "ts": time.time(), "cycle": cycle, "host": target, "kind": kind, "hop": hop,
                "method": res.method, "sent": res.sent, "received": res.received,
                "loss_pct": res.loss_pct, "rtt_min": res.rtt_min, "rtt_avg": res.rtt_avg,
                "rtt_max": res.rtt_max, "jitter": res.jitter, "during_speed": during})
            rtt = f"{res.rtt_avg:.0f}ms" if res.rtt_avg is not None else "-"
            summary.append(f"{target}[{kind[0]}] perda={res.loss_pct:.0f}% rtt={rtt}")
        log.debug("ping: %s", " | ".join(summary))

        internet = [results.get(t) for t, k, _ in targets if k == "internet"]
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
        if self.call is not None and self.cfg["call_mode"].get("skip_speed", True):
            return "call_mode"
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

    def speed_interval(self):
        """Espaça os testes para caber no orçamento diário de dados."""
        scfg = self.cfg["speed"]
        base = scfg["interval_s"]
        budget = (scfg.get("daily_budget_mb") or 0) * 1e6
        if budget:
            per_test = self.download_bytes + scfg["upload_bytes"]
            base = max(base, per_test / (budget / 86400.0))
        return base

    def _adapt_download(self, result):
        """Cresce o tamanho do teste até medir uma janela útil na sua velocidade."""
        scfg = self.cfg["speed"]
        cap = int(scfg.get("max_download_bytes") or 60000000)
        if result and not result.get("confident") and self.download_bytes < cap:
            self.download_bytes = min(cap, self.download_bytes * 2)
            # o teto precisa dar uma janela útil também em linhas rápidas:
            # 80 MB rendem ~0,9 s de medição numa conexão de 500 Mbps
            save_state(self.cfg, {"download_bytes": self.download_bytes})
            log.info("janela de medição curta demais; próximo download usará %.0f MB",
                     self.download_bytes / 1e6)

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
            r = self._run_direction("download", scfg["download_urls"],
                                    lambda u: measure_download(u, self.download_bytes, scfg["max_seconds"],
                                                               scfg["timeout_s"], scfg.get("warmup_s", 0.5),
                                                               scfg.get("min_window_s", 0.7)))
            self._adapt_download(r)
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
                                        "ttfb_ms": r["ttfb_ms"], "ok": 1, "error": None,
                                        "window_s": r.get("window_s"), "confident": r.get("confident", 1)})
            log.info("%s: %.1f Mbps (%.1f MB em %.1fs, janela %.2fs%s)", direction, r["mbps"],
                     r["bytes"] / 1e6, r["seconds"], r.get("window_s") or 0,
                     "" if r.get("confident", 1) else ", pouco confiável")
            return r
        self.store.insert("speed", {"ts": time.time(), "direction": direction, "url": None, "bytes": 0,
                                    "seconds": None, "mbps": None, "ttfb_ms": None, "ok": 0,
                                    "error": last_err})
        return None

    # ---- laço -------------------------------------------------------------

    def _interval(self, name, interval_key):
        if name == "speed":
            return self.speed_interval()
        return self.cfg[name][interval_key]

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
                interval = self._interval(name, interval_key)
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
            self._set_call_mode(None)
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
                       "gateway": self.gateway, "ping_method": self.ping_method, "version": VERSION,
                       "call_mode": self.call is not None, "call_reason": self.call_reason,
                       "call_manual": self.call_manual}, fh)
        os.replace(tmp, self.paths["pid"])
        self._last_beat = time.time()

    def _prune(self):
        days = self.cfg.get("retention_days") or 0
        if not days:
            return
        cutoff = time.time() - days * 86400
        with self.store.lock:
            for table in ("ping", "dns", "speed", "events"):
                self.store.conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
            self.store.conn.commit()
        log.info("histórico anterior a %d dias removido", days)

    def _check_signals(self):
        now = time.time()
        gap = now - getattr(self, "_last_tick", now)
        self._last_tick = now
        if gap > 10:
            self.store.event("suspend", f"{gap:.0f}")
            log.warning("computador ficou %.0f s suspenso; medições do intervalo descartadas", gap)
            if self.call is not None:
                self.call.after_suspend(now)
        if time.time() - getattr(self, "_last_beat", 0) >= 10:
            self._write_heartbeat()
        if time.time() - getattr(self, "_last_prune", 0) >= 86400:
            self._last_prune = time.time()
            try:
                self._prune()
            except Exception:  # noqa: BLE001
                log.exception("erro na limpeza do histórico")
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
        for key, value in (("callon", True), ("calloff", False)):
            if os.path.exists(self.paths[key]):
                try:
                    os.remove(self.paths[key])
                except OSError:
                    pass
                self.call_manual = value
                save_state(self.cfg, {"call_manual": value})
                self._write_heartbeat()
        self._update_call_mode()

    def _update_call_mode(self):
        scheduled = schedule_active(self.cfg["call_mode"].get("schedule", []))
        if self.call_manual is True:
            wanted = "manual"
        elif self.call_manual is False:
            wanted = None
            if not scheduled:
                self.call_manual = None
                save_state(self.cfg, {"call_manual": None})
        else:
            wanted = "agenda" if scheduled else None
        if wanted != self.call_reason:
            self._set_call_mode(wanted)

    def _set_call_mode(self, reason):
        if self.call is not None:
            self.call.close()
            self.call = None
            self.store.event("call_mode_off", self.call_reason or "")
            log.info("modo chamada desligado")
        self.call_reason = reason
        if reason:
            targets = [(self.cfg["call_mode"].get("host") or self.cfg["ping"]["hosts"][0], "internet")]
            if self.gateway:
                targets.append((self.gateway, "gateway"))
            self.call = CallMonitor(self.cfg, self.store, targets, self.ping_method)
            self.call.start()
            self.store.event("call_mode_on", reason)
            log.info("modo chamada ligado (%s): 1 pacote/s para %s", reason, ", ".join(h for h, _ in targets))
        self._write_heartbeat()


# ---- Atualização pelo GitHub Releases --------------------------------------

UPDATE_FILES = ["netmon.py", "netmon_gui.py", "config.example.json", "README.md", "Instalar.bat"]


def _version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def check_update(cfg, timeout_s=10):
    """Consulta a última release. Retorna dict {version, tag, setup_url, newer} ou None se falhar."""
    repo = cfg.get("update", {}).get("repo") or "caiomperet/Claude-Skills"
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.info("verificação de atualização falhou: %s", exc)
        return None
    tag = data.get("tag_name") or ""
    setup_url = None
    for asset in data.get("assets", []):
        if asset.get("name", "").lower().endswith(".exe"):
            setup_url = asset.get("browser_download_url")
    version = ".".join(str(x) for x in _version_tuple(tag)) if tag else ""
    return {"version": version, "tag": tag, "setup_url": setup_url, "repo": repo,
            "newer": bool(version) and _version_tuple(version) > _version_tuple(VERSION),
            "notes_url": data.get("html_url")}


def _download(url, dest, timeout_s=120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)


def _env_path(path):
    """Troca prefixos por variáveis de ambiente para o .cmd não depender de acentos no caminho."""
    for var in ("LOCALAPPDATA", "TEMP", "USERPROFILE"):
        base = os.environ.get(var)
        if base and path.lower().startswith(base.lower().rstrip("\\") + "\\"):
            return f"%{var}%" + path[len(base.rstrip("\\")):]
    return path


def update_log_path(cfg):
    return os.path.join(cfg["_dir"], "netmon-update.log")


def apply_update(cfg, info):
    """Baixa e instala a versão indicada por check_update(). Encerra o monitor antes.

    Instalado como executável (Windows): baixa o netmon-setup.exe e deixa um
    script .cmd que espera esta interface fechar, roda o instalador em modo
    silêncioso forçando o fechamento de qualquer netmon.exe restante, registra
    o resultado em netmon-update.log e reabre a interface. Rodando do código:
    substitui os arquivos .py pela versão da tag e reabre a interface. O
    chamador deve encerrar a interface logo em seguida.
    """
    stopped = request_stop(cfg)
    st = monitor_status(cfg)
    if IS_WINDOWS and not stopped and st.get("pid"):
        subprocess.run(["taskkill", "/PID", str(st["pid"]), "/F"], capture_output=True, **_subprocess_flags())
    if FROZEN:
        if not (IS_WINDOWS and info.get("setup_url")):
            raise RuntimeError("Esta release não tem instalador para o seu sistema.")
        import tempfile
        setup = os.path.join(tempfile.gettempdir(), "netmon-setup.exe")
        _download(info["setup_url"], setup)
        if os.path.getsize(setup) < 1_000_000:
            raise RuntimeError("Download do instalador incompleto")
        exe = sys.executable
        ulog = update_log_path(cfg)
        setup_log = os.path.join(cfg["_dir"], "netmon-setup.log")
        script = os.path.join(cfg["_dir"], "netmon-update.cmd")
        pid = os.getpid()
        lines = [
            "@echo off",
            "chcp 65001 >nul",
            f'echo [%date% %time%] atualizando para {info["version"]}; aguardando a interface (pid {pid}) fechar>> "{_env_path(ulog)}"',
            ":wait",
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)',
            f'echo [%date% %time%] executando o instalador>> "{_env_path(ulog)}"',
            f'"{_env_path(setup)}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /FORCECLOSEAPPLICATIONS '
            f'/LOG="{_env_path(setup_log)}"',
            f'echo [%date% %time%] instalador terminou com codigo %ERRORLEVEL%>> "{_env_path(ulog)}"',
            f'start "" "{_env_path(exe)}" --install',
        ]
        with open(script, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("\n".join(lines) + "\n")
        with open(ulog, "a", encoding="utf-8") as fh:
            fh.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] instalador baixado em {setup} "
                     f"({os.path.getsize(setup) / 1e6:.1f} MB); monitor parado={stopped}\n")
        # CREATE_NO_WINDOW dá ao cmd um console invisível (tasklist, find e ping
        # precisam de um); o processo sobrevive ao fechamento desta interface.
        subprocess.Popen(["cmd", "/c", script], cwd=cfg["_dir"], close_fds=True,
                         creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))
        return script
    base = f"https://raw.githubusercontent.com/{info['repo']}/{info['tag']}/netmon/"
    staged = []
    for name in UPDATE_FILES:
        tmp = os.path.join(BASE_DIR, name + ".new")
        _download(base + name, tmp)
        staged.append((tmp, os.path.join(BASE_DIR, name)))
    for tmp, final in staged:
        os.replace(tmp, final)
    cmd = [monitor_command(cfg)[0], os.path.join(BASE_DIR, "netmon_gui.py"), "--install"]
    kwargs = {"close_fds": True}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    return " ".join(cmd)


def last_update_failure(cfg, max_age_s=86400):
    """Se houve tentativa de atualização recente e ainda estamos na versão antiga, devolve o log."""
    ulog = update_log_path(cfg)
    try:
        if time.time() - os.path.getmtime(ulog) > max_age_s:
            return None
        with open(ulog, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip().splitlines()[-4:]
    except OSError:
        return None


# --------------------------------------------------------------------------
# Estatística e relatório
# --------------------------------------------------------------------------

def add_mark(store, note="", ts=None):
    ts = ts or time.time()
    store.insert("marks", {"ts": ts, "note": note or ""})
    return ts


def explain_mark(store, ts, window_s=120, burst_before_s=90, burst_after_s=15):
    """O que o monitor viu ao redor de uma marcação. Retorna (nível, texto).

    Rajadas contam se começaram até 90 s antes da tecla (o travamento estava
    acontecendo) ou até 15 s depois (a tecla veio no começo dele)."""
    bursts = store.query("SELECT ts, duration_s, kind FROM call_burst WHERE COALESCE(suspect,0)=0 "
                         "AND ts BETWEEN ? AND ? ORDER BY ts", (ts - burst_before_s, ts + burst_after_s))
    inet = [b for b in bursts if b["kind"] == "internet"]
    gw = [b for b in bursts if b["kind"] in ("gateway", "local")]
    if inet:
        nearest = min(inet, key=lambda b: abs(b["ts"] - ts))
        delta = nearest["ts"] - ts
        when = ("no mesmo instante" if abs(delta) < 3 else
                f"{abs(delta):.0f} s {'antes' if delta < 0 else 'depois'}")
        local = (" O roteador também falhou: origem na rede local." if gw
                 else " Roteador respondeu: origem fora de casa.")
        return ("critico", f"Rajada de {nearest['duration_s']:.0f} s sem resposta {when}." + local)
    pings = store.query("SELECT cycle, host, kind, loss_pct, rtt_avg, received, jitter, during_speed FROM ping "
                        "WHERE ts BETWEEN ? AND ?", (ts - window_s, ts + window_s + 30))
    cycles = aggregate_cycles(pings)
    lossy = [c for c in cycles if c["loss"] > 0]
    gw_lossy = [c for c in cycles if (c["gw_loss"] or 0) > 0]
    hop2_lossy = [c for c in cycles if (c["hop2_loss"] or 0) > 0]
    call_on = store.query("SELECT COUNT(*) AS n FROM call_minute WHERE COALESCE(suspect,0)=0 "
                          "AND ts BETWEEN ? AND ?", (ts - window_s, ts + window_s))[0]["n"] > 0
    if lossy:
        worst = max(lossy, key=lambda c: c["loss"])
        if gw_lossy:
            local = " Também no primeiro salto: Wi-Fi ou roteador de casa."
        elif hop2_lossy:
            local = " Primeiro salto limpo, mas o segundo falhou: problema entre os dois equipamentos."
        else:
            local = " Rede local limpa: origem fora de casa."
        return ("atencao", f"Perda de {worst['loss']:.0f}% no ciclo de {fmt_ts(worst['ts'], False)} "
                           f"(medição por minuto).{local}")
    rtts = [c["rtt"] for c in cycles if c["rtt"] is not None]
    if rtts and max(rtts) > 2.5 * (statistics.median(rtts) or 1):
        return ("atencao", f"Sem perda, mas latência subiu a {max(rtts):.0f} ms. Possível saturação do upload.")
    if cycles:
        base = "Nenhuma perda nem latência anormal nos 2 min ao redor"
        if not call_on:
            return ("ok", base + " (modo chamada estava desligado; rajadas curtas podem ter passado). "
                          "Suspeite do computador da chamada, do Wi-Fi dele ou da VPN.")
        return ("ok", base + ", nem no ping por segundo. A rede compartilhada estava boa; suspeite do "
                      "computador da chamada, do Wi-Fi dele ou da VPN.")
    return ("atencao", "Monitor não tinha medições nesse horário.")


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
        loc = [r for r in rows if r["kind"] == "local"]
        if not inet:
            continue
        rtts = [r["rtt_avg"] for r in inet if r["rtt_avg"] is not None]
        jits = [r["jitter"] for r in inet if r["jitter"] is not None]
        gw = gws[0] if gws else None
        hop2 = loc[0] if loc else None
        cycles.append({
            "ts": cyc,
            "loss": statistics.median(r["loss_pct"] for r in inet),
            "rtt": statistics.median(rtts) if rtts else None,
            "jitter": statistics.median(jits) if jits else None,
            "down": all((r["received"] or 0) == 0 for r in inet),
            "during_speed": any(r["during_speed"] for r in rows),
            "gw_loss": gw["loss_pct"] if gw else None,
            "gw_rtt": gw["rtt_avg"] if gw else None,
            "gw_host": gw["host"] if gw else None,
            "hop2_loss": hop2["loss_pct"] if hop2 else None,
            "hop2_rtt": hop2["rtt_avg"] if hop2 else None,
            "hop2_host": hop2["host"] if hop2 else None,
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

    T2 = THRESHOLDS["loss_cycle_pct"]
    hop2 = [c for c in clean if c["hop2_loss"] is not None]
    st["hop2"] = None
    if hop2:
        st["hop2"] = {
            "host": next((c["hop2_host"] for c in hop2 if c["hop2_host"]), None),
            "n": len(hop2),
            "loss_avg": statistics.fmean(c["hop2_loss"] for c in hop2),
            "rtt_p50": percentile([c["hop2_rtt"] for c in hop2], 50),
            "rtt_p95": percentile([c["hop2_rtt"] for c in hop2], 95),
        }
    # Onde a perda nasce: primeiro salto (Wi-Fi), entre os equipamentos da casa,
    # ou depois da rede local (provedor).
    blame = {"wifi": 0, "casa": 0, "fora": 0, "total": 0}
    for c in clean:
        if c["loss"] < T2 or c["gw_loss"] is None:
            continue
        blame["total"] += 1
        if c["gw_loss"] >= T2:
            blame["wifi"] += 1
        elif (c["hop2_loss"] or 0) >= T2:
            blame["casa"] += 1
        else:
            blame["fora"] += 1
    st["blame"] = blame

    gw = [c for c in clean if c["gw_loss"] is not None]
    st["gateway"] = None
    if gw:
        st["gateway"] = {
            "host": next((c["gw_host"] for c in gw if c["gw_host"]), None),
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
        # Todas as medições entram na estatística: filtrar as curtas deixaria só
        # as lentas, porque num teste de bytes fixos o lento é justamente o que
        # dura mais. A contagem de medições fracas vira um aviso de confiança.
        ok = [s for s in rows if s["ok"] and s["mbps"] is not None]
        weak = sum(1 for s in ok if not s.get("confident", 1))
        vals = [s["mbps"] for s in ok]
        plan_mbps = float(plan.get(plan_key) or 0)
        d = {
            "n": len(ok), "weak": weak, "failed": sum(1 for s in rows if not s["ok"]), "plan": plan_mbps,
            "unreliable": bool(ok) and weak > 0.3 * len(ok),
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

    bursts = store.query("SELECT * FROM call_burst WHERE COALESCE(suspect,0)=0 AND ts >= ? ORDER BY ts", (start,))
    call_minutes = store.query("SELECT COUNT(*) AS n FROM call_minute WHERE kind='internet' AND "
                               "COALESCE(suspect,0)=0 AND ts >= ?", (start,))[0]["n"]
    discarded = store.query("SELECT COUNT(*) AS n FROM call_burst WHERE COALESCE(suspect,0)=1 AND ts >= ?",
                            (start,))[0]["n"]
    inet_bursts = [b for b in bursts if b["kind"] == "internet"]
    gw_bursts = [b for b in bursts if b["kind"] in ("gateway", "local")]
    st["call"] = {
        "minutes": call_minutes,
        "bursts": inet_bursts,
        "gw_bursts": len(gw_bursts),
        "total_s": sum(b["duration_s"] or 0 for b in inet_bursts),
        "longest_s": max((b["duration_s"] or 0 for b in inet_bursts), default=0),
        "per_hour": 60.0 * len(inet_bursts) / call_minutes if call_minutes else None,
        "discarded": discarded,
    }

    marks = store.query("SELECT ts, note FROM marks WHERE ts >= ? ORDER BY ts", (start,))
    st["marks"] = []
    for m in marks:
        level, text = explain_mark(store, m["ts"])
        st["marks"].append({"ts": m["ts"], "note": m["note"], "level": level, "text": text})

    dns_ok = [d for d in dns if d["ok"]]
    st["dns"] = {
        "n": len(dns),
        "fail_pct": 100.0 * (len(dns) - len(dns_ok)) / len(dns) if dns else None,
        "p50": percentile([d["ms"] for d in dns_ok], 50),
        "p95": percentile([d["ms"] for d in dns_ok], 95),
    }
    st["suspends"] = [e for e in events if e["kind"] == "suspend"]
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

    blame = st.get("blame") or {"total": 0}
    gw = st.get("gateway")
    hop2 = st.get("hop2")
    if blame["total"]:
        share = lambda k: 100.0 * blame[k] / blame["total"]
        onde = [f"{share('wifi'):.0f}% no primeiro salto"]
        if hop2:
            onde.append(f"{share('casa'):.0f}% entre os equipamentos da casa")
        onde.append(f"{share('fora'):.0f}% além da rede local")
        detalhe = (f"Dos {blame['total']} ciclos com perda relevante: " + ", ".join(onde) + ". "
                   + (f"Primeiro salto: {gw['host']}. " if gw and gw.get("host") else "")
                   + (f"Segundo: {hop2['host']}. " if hop2 and hop2.get("host") else ""))
        if share("wifi") >= 50:
            local_problem = True
            items.append(("critico", "A maior parte da perda nasce no primeiro salto",
                          detalhe + "Isso é a ligação entre este computador e o roteador, normalmente o Wi-Fi. "
                          "Trocar de provedor não resolve. Teste por cabo, aproxime-se do roteador ou "
                          "verifique se há duas redes sem fio disputando canal."))
        elif hop2 and share("casa") >= 30:
            local_problem = True
            items.append(("critico", "Perda entre os equipamentos da casa",
                          detalhe + "O primeiro salto responde, mas o seguinte falha. Suspeite do cabo entre os "
                          "dois aparelhos, do enlace sem fio entre nós do mesh ou do equipamento da operadora."))
        else:
            items.append(("ok", "Rede local saudável",
                          detalhe + "A perda se concentra fora de casa, então a responsabilidade é do provedor."))
    elif gw:
        items.append(("ok", "Rede local saudável",
                      f"Perda até o roteador de {gw['loss_avg']:.2f}% (p95 de latência "
                      f"{fmt_num(gw['rtt_p95'], 0, ' ms')}) e nenhum ciclo com perda relevante."))
    else:
        items.append(("atencao", "Rede local não monitorada",
                      "Sem medição até o roteador não dá para separar problema local de problema do provedor. "
                      "Informe os IPs em ping.local_hops no config.json."))

    if st["outages_per_week"] is not None:
        opw, mpw = st["outages_per_week"], st["outage_minutes_per_week"]
        curto = st["monitored_hours"] < 48
        proj = ("" if curto else
                f" Equivale a {opw:.1f} quedas e {mpw:.0f} min por semana.")
        if opw > T["outages_per_week"] or mpw > T["outage_minutes_per_week"]:
            isp_unstable = True
            items.append(("critico", "Quedas frequentes",
                          f"{len(st['outages'])} quedas totais ({fmt_minutes(st['outage_minutes'])}) em "
                          f"{st['monitored_hours']:.0f} h monitoradas.{proj} Disponibilidade de "
                          f"{st['availability_pct']:.2f}%."
                          + (" Amostra curta: confirme com mais dias antes de reclamar." if curto else "")))
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
        if d.get("unreliable"):
            items.append(("atencao", f"{label} sem medição confiável",
                          f"{d['weak']} dos {d['n']} testes terminaram antes de o TCP acelerar, então os valores "
                          f"são um piso do método, não a sua velocidade ({base}). O programa já aumenta o tamanho "
                          "do teste sozinho até medir uma janela útil; refaça a leitura depois de algumas horas. "
                          "Não dá para acusar o provedor com estes números."))
            continue
        if d["plan"]:
            ratio5, ratio50 = d["p5"] / d["plan"], d["p50"] / d["plan"]
            if (ratio5 < T["speed_p5_plan_ratio"] or ratio50 < T["speed_p50_plan_ratio"]) and d["n"] >= 5:
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

    call = st.get("call") or {}
    if call.get("minutes"):
        n = len(call["bursts"])
        hours = call["minutes"] / 60.0
        if n == 0:
            items.append(("ok", "Modo chamada sem travamentos",
                          f"{hours:.1f} h de ping contínuo sem nenhuma rajada de perda."))
        else:
            level = "critico" if call["per_hour"] > 1 else "atencao"
            items.append((level, "Travamentos detectados no modo chamada",
                          f"{n} rajadas de perda em {hours:.1f} h de ping contínuo ({call['per_hour']:.1f} por hora), "
                          f"a mais longa de {call['longest_s']:.0f} s, {call['total_s']:.0f} s no total. "
                          + (f"{call['gw_bursts']} delas também até o roteador (rede local). "
                             if call["gw_bursts"] else "Nenhuma até o roteador: origem no provedor. ")
                          + "Cada uma corresponde a um congelamento de vídeo do mesmo tamanho."))
            if not local_problem and call["gw_bursts"] == 0 and call["per_hour"] > 1:
                isp_unstable = True

    marks = st.get("marks") or []
    if marks:
        confirmed = sum(1 for m in marks if m["level"] == "critico")
        partial = sum(1 for m in marks if m["level"] == "atencao")
        clean = sum(1 for m in marks if m["level"] == "ok")
        level = "critico" if confirmed > len(marks) / 2 else ("atencao" if confirmed + partial else "ok")
        items.append((level, f"{len(marks)} travamentos marcados por você",
                      f"{confirmed} coincidem com rajadas de perda no ping por segundo, {partial} com perda ou "
                      f"latência na medição por minuto, {clean} sem nada anormal na rede compartilhada. "
                      + ("A maioria tem causa na conexão da casa." if confirmed > len(marks) / 2 else
                         "Quando a rede da casa estava limpa, o travamento veio de outro lugar: computador da "
                         "chamada, Wi-Fi dele, VPN ou o serviço de reunião.")))

    susp = st.get("suspends") or []
    if susp:
        total = sum(float(e["detail"] or 0) for e in susp)
        items.append(("atencao", "Computador suspendeu durante o monitoramento",
                      f"{len(susp)} interrupções somando {fmt_minutes(total / 60)}. Esses intervalos foram "
                      "descartados das estatísticas, mas são buracos na cobertura. Deixe a suspensão em "
                      "\"Nunca\" nas opções de energia para o monitoramento ficar contínuo."))

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
        parts.append(kpi("Perda no 1º salto", fmt_num(st["gateway"]["loss_avg"], 2, "%"),
                         f"{st['gateway'].get('host') or ''} | p95 {fmt_num(st['gateway']['rtt_p95'], 0, ' ms')}"))
    if st.get("hop2"):
        parts.append(kpi("Perda no 2º salto", fmt_num(st["hop2"]["loss_avg"], 2, "%"),
                         f"{st['hop2'].get('host') or ''} | p95 {fmt_num(st['hop2']['rtt_p95'], 0, ' ms')}"))
    blame = st.get("blame") or {"total": 0}
    if blame["total"]:
        parts.append(kpi("Origem da perda", f"{100 * blame['wifi'] / blame['total']:.0f}% no 1º salto",
                         f"{100 * blame['casa'] / blame['total']:.0f}% entre equipamentos, "
                         f"{100 * blame['fora'] / blame['total']:.0f}% fora de casa"))
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

    call = st.get("call") or {}
    if call.get("minutes"):
        parts.append(f"<h2>Modo chamada: {call['minutes'] / 60:.1f} h de ping contínuo, "
                     f"{len(call['bursts'])} travamentos</h2><div class='card'>")
        if call.get("discarded"):
            parts.append(f"<p class='note'>{call['discarded']} rajadas foram descartadas por caírem em "
                         "intervalos com o computador suspenso.</p>")
        if call["bursts"]:
            parts.append("<table><tr><th>Início</th><th>Duração</th><th>Alvo</th></tr>")
            for b in call["bursts"][-40:]:
                parts.append(f"<tr><td>{fmt_ts(b['ts'])}</td><td class='mono'>{b['duration_s']:.1f} s</td>"
                             f"<td>{e(b['host'])}</td></tr>")
            parts.append("</table>")
        else:
            parts.append("<p class='note'>Nenhuma rajada de perda registrada.</p>")
        parts.append("</div>")

    if st.get("marks"):
        parts.append(f"<h2>Travamentos marcados por você: {len(st['marks'])}</h2><div class='card'>"
                     "<table><tr><th>Quando</th><th>O que o monitor viu</th></tr>")
        colors = {"critico": "#dc2626", "atencao": "#f59e0b", "ok": "#16a34a"}
        for m in st["marks"]:
            parts.append(f"<tr><td>{dt.datetime.fromtimestamp(m['ts']).strftime('%d/%m %H:%M:%S')}"
                         + (f"<br><span class='note'>{e(m['note'])}</span>" if m["note"] else "")
                         + f"</td><td style='border-left:4px solid {colors[m['level']]}'>{e(m['text'])}</td></tr>")
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
        lines.append(f"1o salto local      perda {fmt_num(st['gateway']['loss_avg'], 2, '%')}  latência p95 "
                     f"{fmt_num(st['gateway']['rtt_p95'], 0, ' ms')}  ({st['gateway'].get('host') or '-'})")
    if st.get("hop2"):
        lines.append(f"2o salto local      perda {fmt_num(st['hop2']['loss_avg'], 2, '%')}  latência p95 "
                     f"{fmt_num(st['hop2']['rtt_p95'], 0, ' ms')}  ({st['hop2'].get('host') or '-'})")
    bl = st.get("blame") or {"total": 0}
    if bl["total"]:
        lines.append(f"Origem da perda     {100*bl['wifi']/bl['total']:.0f}% no 1o salto, "
                     f"{100*bl['casa']/bl['total']:.0f}% entre equipamentos, "
                     f"{100*bl['fora']/bl['total']:.0f}% fora de casa")
    call = st.get("call") or {}
    if call.get("minutes"):
        lines.append(f"Modo chamada        {call['minutes'] / 60:.1f} h, {len(call['bursts'])} travamentos, "
                     f"mais longo {call['longest_s']:.0f} s")
    if st.get("marks"):
        lines.append(f"Marcações           {len(st['marks'])}")
        for m in st["marks"][-10:]:
            lines.append(f"  {dt.datetime.fromtimestamp(m['ts']).strftime('%d/%m %H:%M:%S')}  {m['text']}")
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
    if args.table not in ("ping", "dns", "speed", "events", "call_minute", "call_burst", "marks"):
        sys.exit("tabela deve ser ping, dns, speed, events, call_minute, call_burst ou marks")
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


def cmd_mark(args, cfg):
    store = Store(cfg["db_path"])
    ts = add_mark(store, " ".join(args.note))
    print(f"marcação gravada às {dt.datetime.fromtimestamp(ts).strftime('%H:%M:%S')}")
    store.close()


def cmd_hops(args, cfg):
    pcfg = cfg["ping"]
    gw = detect_gateway()
    print(f"gateway padrão: {gw or 'não detectado'}")
    found = discover_local_hops(pcfg["hosts"][0])
    print(f"saltos locais descobertos: {', '.join(found) if found else 'nenhum'}")
    hops = [h for h in [gw] if h] + [h for h in found if h != gw]
    print(f"seriam monitorados: {', '.join(hops[:3]) or 'nenhum'}")
    print("Para fixar manualmente, defina ping.local_hops no config.json.")


def cmd_repair(args, cfg):
    store = Store(cfg["db_path"])          # a própria abertura já remarca
    m = store.query("SELECT COUNT(*) AS n FROM call_minute WHERE suspect=1")[0]["n"]
    b = store.query("SELECT COUNT(*) AS n FROM call_burst WHERE suspect=1")[0]["n"]
    w = store.query("SELECT COUNT(*) AS n FROM speed WHERE confident=0")[0]["n"]
    total_b = store.query("SELECT COUNT(*) AS n FROM call_burst")[0]["n"]
    print(f"artefatos de suspensão: {m} minutos e {b} de {total_b} rajadas")
    print(f"medições de velocidade curtas demais para valer: {w}")
    store.close()


def cmd_marks(args, cfg):
    store = Store(cfg["db_path"])
    rows = store.query("SELECT ts, note FROM marks WHERE ts >= ? ORDER BY ts",
                       (time.time() - args.days * 86400,))
    if not rows:
        print(f"nenhuma marcação nos últimos {args.days:g} dias (banco: {cfg['db_path']})")
    for r in rows:
        _, text = explain_mark(store, r["ts"])
        note = f"  [{r['note']}]" if r["note"] else ""
        print(f"{dt.datetime.fromtimestamp(r['ts']).strftime('%d/%m %H:%M:%S')}{note}  {text}")
    store.close()


def cmd_call(args, cfg):
    request_call_mode(cfg, args.state == "on")
    print("modo chamada " + ("ligado" if args.state == "on" else "desligado") + " (pedido enviado ao monitor)")


def cmd_status(args, cfg):
    st = monitor_status(cfg)
    if st["running"]:
        print(f"rodando: pid {st['pid']}, desde {fmt_ts(st['started'])}, gateway {st.get('gateway')}, "
              f"ping {st.get('ping_method')}, modo chamada "
              + (f"ligado ({st.get('call_reason')})" if st.get("call_mode") else "desligado"))
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
    p = sub.add_parser("call", help="liga ou desliga o modo chamada (ping contínuo)")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("mark", help="grava uma marcação de travamento agora")
    p.add_argument("note", nargs="*")
    p = sub.add_parser("marks", help="lista as marcações e o que o monitor viu em cada uma")
    p.add_argument("--days", type=float, default=7)
    sub.add_parser("hops", help="mostra os saltos da rede local que seriam monitorados")
    sub.add_parser("repair", help="remarca artefatos de suspensão no histórico")
    sub.add_parser("once", help="executa todas as medições uma vez e mostra o resultado")
    p = sub.add_parser("summary", help="resumo em texto")
    p.add_argument("--days", type=float, default=7)
    p = sub.add_parser("report", help="gera relatório HTML")
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--out", help="arquivo de saída (padrão: report.html ao lado do banco)")
    p.add_argument("--open", action="store_true", help="abre no navegador")
    p = sub.add_parser("export", help="exporta uma tabela para CSV")
    p.add_argument("--table", default="speed", help="ping, dns, speed, events, call_minute ou call_burst")
    p.add_argument("--days", type=float, default=30)
    p.add_argument("--out")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    {"run": cmd_run, "once": cmd_once, "summary": cmd_summary, "report": cmd_report,
     "export": cmd_export, "start": cmd_start, "stop": cmd_stop, "status": cmd_status,
     "call": cmd_call, "mark": cmd_mark, "marks": cmd_marks, "hops": cmd_hops,
     "repair": cmd_repair}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
