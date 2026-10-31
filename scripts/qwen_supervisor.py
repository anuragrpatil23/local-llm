"""Keeps the local Qwen engine running on AC power and reports its state.

Run by launchd (com.anurag.local-qwen). Clients read the state from
GET http://127.0.0.1:1233/status, which answers even while the engine is
down, restarting, or paused on battery.

States: starting -> ready; restarting (after any failure, with backoff,
or a planned memory recycle); failed (circuit open: too many failures,
retries after a cooldown); paused_on_battery; paused_memory_pressure (macOS
reports critical memory pressure, so the model stays unloaded);
external (something else holds the engine port).

Memory: the engine caps its own wired memory and buffer cache (see
qwen_engine.py) and the prompt cache is bounded here. On top of that the
engine is recycled when idle if it holds more than ENGINE_MEMORY_LIMIT_GB,
or when macOS memory pressure stays critical (immediately if idle, after
PRESSURE_FORCE_AFTER seconds even if busy, since a frozen Mac is worse than
a lost job).

Every failure, whatever its cause, ends the engine process and is recorded
with a reason: the engine's own failure record (see qwen_engine.py) when it
wrote one, otherwise the exit code/signal or the failed health check.
"""
import collections
import json
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = "/Users/patila06/.pyenv/versions/3.11.4/bin/python3.11"
MODEL = "mlx-community/Qwen3.8-27B-4bit"
PORT = 1234
STATUS_PORT = 1233
PROMPT_CACHE_SIZE = 4         # saved conversation caches (mlx_lm default: 10)
PROMPT_CACHE_BYTES = "4GB"    # and their total size (mlx_lm default: unbounded)
ENGINE_MEMORY_LIMIT_GB = 40   # active + cached MLX memory before an idle recycle
PRESSURE_IDLE_AFTER = 30      # seconds of critical pressure before an idle recycle
PRESSURE_FORCE_AFTER = 120    # ... before recycling even mid-request

HEALTH_EVERY = 5          # seconds between health checks while ready
HEALTH_FAILS = 3          # consecutive failed checks before killing the engine
START_TIMEOUT = 300       # seconds for the engine to load and pass the canary
BACKOFF = [0, 5, 30, 120] # delay before restart N (last value repeats)
CIRCUIT_FAILURES = 4      # failures within CIRCUIT_WINDOW open the circuit
CIRCUIT_WINDOW = 15 * 60
CIRCUIT_COOLDOWN = 15 * 60

LOG_DIR = Path.home() / "Library/Logs"
STATE_DIR = Path.home() / "Library/Application Support/local-qwen"
FAILURE_FILE = STATE_DIR / "engine-failure.json"
EVENTS_FILE = LOG_DIR / "local-qwen-events.jsonl"

ENGINE_URL = f"http://127.0.0.1:{PORT}"
NOISE = ("Prompt processing progress", "GET /health", "GET /v1/models")

log = logging.getLogger("supervisor")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Supervisor:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.state = "starting"
        self.reason = "supervisor started"
        self.since = now_iso()
        self.health = None
        self.last_in_flight = []  # from the last health answer, for failures we detect ourselves
        self.failures = collections.deque(maxlen=20)  # recent failure records
        self.restart_at = 0.0
        self.stopping = False
        self.pressure = "normal"
        self.critical_since = None

    # --- state -----------------------------------------------------------

    def set_state(self, state, reason):
        with self.lock:
            if (state, reason) == (self.state, self.reason):
                return
            self.state, self.reason, self.since = state, reason, now_iso()
        log.info("state=%s reason=%s", state, reason)
        self.event({"event": "state", "state": state, "reason": reason})

    def event(self, record):
        record = {"at": now_iso(), **record}
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")

    def status(self):
        with self.lock:
            window = time.time() - 3600
            return {
                "state": self.state,
                "reason": self.reason,
                "since": self.since,
                "ready": self.state == "ready",
                "retry_after_s": max(0, round(self.restart_at - time.time())) if self.state in ("restarting", "failed") else None,
                "engine": {
                    "url": f"{ENGINE_URL}/v1",
                    "model": MODEL,
                    "pid": self.proc.pid if self.proc else None,
                    "health": self.health,
                },
                "system": {"memory_pressure": self.pressure},
                "failures_last_hour": sum(1 for f in self.failures if f["t"] > window),
                "last_failure": self.failures[-1]["record"] if self.failures else None,
            }

    # --- engine process --------------------------------------------------

    def start_engine(self):
        FAILURE_FILE.unlink(missing_ok=True)
        env = {
            **os.environ,
            "HF_HOME": str(ROOT / "Models"),
            "HF_HUB_OFFLINE": "1",
            "PYTHONUNBUFFERED": "1",
            "QWEN_FAILURE_FILE": str(FAILURE_FILE),
        }
        self.proc = subprocess.Popen(
            [PYTHON, str(ROOT / "scripts/qwen_engine.py"), "--model", MODEL, "--port", str(PORT),
             "--prompt-cache-size", str(PROMPT_CACHE_SIZE), "--prompt-cache-bytes", PROMPT_CACHE_BYTES],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=True,
        )
        threading.Thread(target=self.pump_output, args=(self.proc,), daemon=True).start()
        log.info("started engine pid %s", self.proc.pid)

    def pump_output(self, proc):
        engine_log = logging.getLogger("engine")
        for line in proc.stdout:
            line = line.rstrip()
            if line and not any(n in line for n in NOISE):
                engine_log.info(line)

    def stop_engine(self, why, sig=signal.SIGTERM):
        if not self.proc or self.proc.poll() is not None:
            self.proc = None
            return
        log.info("stopping engine pid %s: %s", self.proc.pid, why)
        try:
            os.killpg(self.proc.pid, sig)
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        except ProcessLookupError:
            pass
        self.proc = None

    def record_failure(self, fallback_reason, detail=""):
        record = None
        if FAILURE_FILE.exists():
            try:
                record = json.loads(FAILURE_FILE.read_text())
            except ValueError:
                pass
            FAILURE_FILE.unlink(missing_ok=True)
        if record is None:
            record = {"at": now_iso(), "reason": fallback_reason, "detail": detail,
                      "in_flight": self.last_in_flight, "in_flight_source": "last health check"}
        self.last_in_flight = []
        record["id"] = uuid.uuid4().hex  # unique per failure; "at" has 1s resolution
        record["previous_state"] = self.state
        with self.lock:
            self.failures.append({"t": time.time(), "record": record})
            recent = sum(1 for f in self.failures if f["t"] > time.time() - CIRCUIT_WINDOW)
        self.event({"event": "failure", **record})
        log.error("engine failure: %s %s", record["reason"], record.get("error") or record.get("detail", "")[:300])

        if recent >= CIRCUIT_FAILURES:
            self.restart_at = time.time() + CIRCUIT_COOLDOWN
            self.set_state("failed", f"{recent} failures in {CIRCUIT_WINDOW // 60} min; last: {record['reason']}")
            notify("Local Qwen is down", f"{recent} failures in {CIRCUIT_WINDOW // 60} min ({record['reason']}). Retrying in {CIRCUIT_COOLDOWN // 60} min.")
        else:
            delay = BACKOFF[min(recent - 1, len(BACKOFF) - 1)]
            self.restart_at = time.time() + delay
            self.set_state("restarting", f"{record['reason']}; restarting in {delay}s")

    # --- probes ----------------------------------------------------------

    def probe_health(self):
        try:
            with urllib.request.urlopen(f"{ENGINE_URL}/health", timeout=5) as r:
                self.health = json.loads(r.read())
                self.last_in_flight = self.health.get("in_flight_requests", [])
                return True
        except urllib.error.HTTPError as e:  # 503: engine reports itself unhealthy
            try:
                self.health = json.loads(e.read())
            except ValueError:
                self.health = None
            return False
        except (OSError, ValueError):
            self.health = None
            return False

    def canary(self):
        body = json.dumps({
            "model": MODEL, "max_tokens": 1, "temperature": 0,
            "messages": [{"role": "user", "content": "Say OK."}],
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode()
        req = urllib.request.Request(
            f"{ENGINE_URL}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json", "X-Request-Id": "supervisor-canary", "X-Job": "canary"},
        )
        try:
            with urllib.request.urlopen(req, timeout=START_TIMEOUT) as r:
                return bool(json.loads(r.read()).get("choices"))
        except (OSError, ValueError):
            return False

    # --- main loop -------------------------------------------------------

    def run(self):
        fails = 0
        while not self.stopping:
            if not on_ac():
                if self.proc:
                    self.stop_engine("on battery")
                self.health = None
                self.set_state("paused_on_battery", "Mac is on battery")
                time.sleep(5)
                continue

            if self.proc and (rc := self.proc.poll()) is not None:
                self.proc = None
                self.health = None
                self.record_failure(f"engine exited ({describe_exit(rc)})")
                continue

            if not self.proc:
                if self.state == "paused_on_battery":
                    self.restart_at = 0
                if time.time() < self.restart_at:
                    time.sleep(1)
                    continue
                if self.update_pressure() == "critical":
                    self.set_state("paused_memory_pressure", "macOS memory pressure is critical; model unloaded")
                    time.sleep(5)
                    continue
                if port_in_use(PORT):
                    self.set_state("external", f"port {PORT} is held by another process")
                    time.sleep(10)
                    continue
                self.set_state("starting", "loading model")
                self.start_engine()
                if self.warm_up():
                    fails = 0
                    self.set_state("ready", "canary passed")
                continue

            if self.probe_health():
                fails = 0
                if self.recycle_for_memory():
                    continue
                if self.state != "ready":
                    self.set_state("ready", "health ok")
            else:
                fails += 1
                if fails >= HEALTH_FAILS:
                    why = "engine reported unhealthy" if self.health else "health check unanswered"
                    detail = json.dumps(self.health) if self.health else ""
                    self.stop_engine(why, signal.SIGKILL)
                    fails = 0
                    self.record_failure(why, detail)
                    continue
            # Wait on the process rather than sleeping, so an exit is seen at once.
            try:
                self.proc.wait(timeout=HEALTH_EVERY)
            except subprocess.TimeoutExpired:
                pass

    def update_pressure(self):
        self.pressure = memory_pressure()
        if self.pressure != "critical":
            self.critical_since = None
        elif self.critical_since is None:
            self.critical_since = time.time()
        return self.pressure

    def recycle_for_memory(self):
        """Restart the engine to give memory back. Planned, so not a failure."""
        self.update_pressure()
        health = self.health or {}
        mem = health.get("memory_gb", {})
        used = mem.get("active", 0) + mem.get("cache", 0)
        idle = health.get("in_flight", 0) == 0
        critical_for = time.time() - self.critical_since if self.critical_since else 0

        why = None
        if idle and used > ENGINE_MEMORY_LIMIT_GB:
            why = f"engine holds {used:.1f} GB (limit {ENGINE_MEMORY_LIMIT_GB})"
        elif critical_for > (PRESSURE_IDLE_AFTER if idle else PRESSURE_FORCE_AFTER):
            why = f"memory pressure critical for {critical_for:.0f}s" + ("" if idle else f", {health.get('in_flight')} request(s) dropped")
        if not why:
            return False
        self.event({"event": "recycle", "reason": why, "memory_gb": mem, "in_flight": health.get("in_flight")})
        self.stop_engine(f"recycle: {why}")
        self.health = None
        self.restart_at = 0
        self.set_state("restarting", f"memory recycle: {why}")
        return True

    def warm_up(self):
        """Wait for the engine to answer, then prove it can generate."""
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if self.proc.poll() is not None or self.stopping:
                return False  # main loop records the exit
            if self.probe_health():
                if self.canary():
                    self.probe_health()
                    return True
                break
            time.sleep(2)
        if self.proc and self.proc.poll() is None:
            self.stop_engine("did not become ready", signal.SIGKILL)
            self.record_failure("startup_failed", f"no successful canary within {START_TIMEOUT}s")
        return False


def on_ac():
    out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    return "AC Power" in out.splitlines()[0] if out else True


def memory_pressure():
    level = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True).stdout.strip()
    return {"1": "normal", "2": "warn", "4": "critical"}.get(level, "normal")


def port_in_use(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def describe_exit(rc):
    if rc == 75:
        return "engine fail-fast"
    if rc < 0:
        name = signal.Signals(-rc).name
        return f"killed by {name}" + (" (external kill or out of memory)" if name == "SIGKILL" else "")
    return f"exit code {rc}"


def notify(title, message):
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], capture_output=True)


def serve_status(sup):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] not in ("/status", "/"):
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(sup.status()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", STATUS_PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_DIR / "local-qwen.log", maxBytes=10_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    # An engine left over from a previous supervisor (e.g. one that crashed)
    # would hold the port and leave us in "external" forever.
    subprocess.run(["pkill", "-f", str(ROOT / "scripts/qwen_engine.py")])
    sup = Supervisor()

    def shutdown(*_):
        sup.stopping = True
        sup.stop_engine("supervisor exiting")
        log.info("supervisor exiting")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    serve_status(sup)
    log.info("supervisor started; status on http://127.0.0.1:%s/status", STATUS_PORT)
    sup.run()


if __name__ == "__main__":
    main()
