"""mlx_lm.server with fail-fast, real liveness, and failure records.

mlx_lm.server generates on a single background thread. If that thread dies
or wedges, for whatever reason, the HTTP layer keeps
accepting requests that never complete. This wrapper turns every such state
into a process exit, so the supervisor can restart it and report why:

- any uncaught exception on a non-request thread  -> exit
- generation thread no longer alive                -> exit
- requests in flight but no prefill/decode progress
  for QWEN_STALL_SECONDS                           -> exit

Before exiting it writes a JSON failure record (reason, detail, what was in
flight) to QWEN_FAILURE_FILE. GET /health reports real generator state and
MLX memory use.

Memory: mlx_lm.server wires up to ~84% of RAM (max_recommended_working_set_size)
and keeps freed GPU buffers cached without bound, which can starve the rest
of the Mac. QWEN_WIRED_LIMIT_GB caps the wired memory and QWEN_CACHE_LIMIT_GB
caps the freed-buffer cache.

Arguments are passed through to mlx_lm.server.
"""
import collections
import json
import logging
import os
import sys
import threading
import time
import traceback

import mlx.core as mx
from mlx_lm import server
from mlx_lm.generate import BatchGenerator

STALL_SECONDS = float(os.environ.get("QWEN_STALL_SECONDS", "180"))
WIRED_LIMIT = int(float(os.environ.get("QWEN_WIRED_LIMIT_GB", "32")) * 2**30)
CACHE_LIMIT = int(float(os.environ.get("QWEN_CACHE_LIMIT_GB", "4")) * 2**30)
FAILURE_FILE = os.environ.get("QWEN_FAILURE_FILE")
GB = 2**30
SPEED_WINDOW = 60  # seconds of engine steps behind the speed figures in /health
EXIT_CODE = 75

started = time.time()
lock = threading.Lock()
in_flight = {}  # request id -> {"job", "since"}
# Stall timing uses the monotonic clock, which on macOS stops while the Mac
# sleeps, so a request suspended by sleep is not mistaken for a stall.
progress = {"last": time.monotonic(), "tokens": 0}
generator = {}  # "instance" -> the ResponseGenerator
steps = collections.deque()  # (monotonic time, step seconds, decode tokens, prefill tokens)
prefill_done = {}  # uid -> prompt tokens processed so far
dying = threading.Event()


def die(reason, detail="", error=None):
    if dying.is_set():
        return
    dying.set()
    with lock:
        flight = [
            {"request_id": rid, "job": f["job"], "age_s": round(time.time() - f["since"], 1)}
            for rid, f in in_flight.items()
        ]
    record = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reason": reason,
        "error": error,
        "detail": detail[-2000:],
        "uptime_s": round(time.time() - started),
        "tokens_generated": progress["tokens"],
        "in_flight": flight,
    }
    logging.critical("FATAL %s", json.dumps(record))
    if FAILURE_FILE:
        tmp = FAILURE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(record, f)
        os.replace(tmp, FAILURE_FILE)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(EXIT_CODE)


def excepthook(args):
    detail = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    thread = args.thread.name if args.thread else "unknown"
    die("thread_exception", detail, f"{args.exc_type.__name__} in {thread}: {args.exc_value}"[:300])


def snapshot():
    gen = generator.get("instance")
    alive = gen is not None and gen._generation_thread.is_alive()
    with lock:
        active = len(in_flight)
        flight = [{"request_id": rid, "job": f["job"]} for rid, f in in_flight.items()]
    idle_for = time.monotonic() - progress["last"]
    stalled = active > 0 and idle_for > STALL_SECONDS
    return {
        "status": "ok" if alive and not stalled and not dying.is_set() else "unhealthy",
        "generator_alive": alive,
        "stalled": stalled,
        "uptime_s": round(time.time() - started),
        "in_flight": active,
        "in_flight_requests": flight,
        "queued": gen.requests.qsize() if gen else 0,
        "tokens_generated": progress["tokens"],
        "last_progress_age_s": round(idle_for, 1),
        "speed": speed(),
        "memory_gb": {
            "active": round(mx.get_active_memory() / GB, 2),
            "cache": round(mx.get_cache_memory() / GB, 2),
            "peak": round(mx.get_peak_memory() / GB, 2),
            "wired_limit": round(WIRED_LIMIT / GB, 1),
            "cache_limit": round(CACHE_LIMIT / GB, 1),
        },
    }


def speed():
    """Engine speed over the last SPEED_WINDOW s, timed by the steps themselves
    so idle time does not dilute it."""
    cutoff = time.monotonic() - SPEED_WINDOW
    with lock:
        recent = [st for st in steps if st[0] >= cutoff]
    decode = [(dt, n) for _, dt, n, _ in recent if n]
    prefill = [(dt, n) for _, dt, _, n in recent if n]
    if not decode and not prefill:
        return None
    out = {"window_s": SPEED_WINDOW}
    if decode:
        tokens, seconds = sum(n for _, n in decode), sum(dt for dt, _ in decode)
        out["decode_tok_s"] = round(tokens / seconds, 1)  # all requests together
        out["avg_batch"] = round(tokens / len(decode), 2)
        out["decode_tok_s_per_request"] = round(tokens / seconds / (tokens / len(decode)), 1)
    if prefill:
        out["prefill_tok_s"] = round(sum(n for _, n in prefill) / sum(dt for dt, _ in prefill), 1)
    return out


def watchdog():
    while True:
        time.sleep(2)
        gen = generator.get("instance")
        if gen is None:
            continue
        if not gen._generation_thread.is_alive():
            die("generator_exited", "generation thread is no longer alive")
        snap = snapshot()
        if snap["stalled"]:
            die("stalled", f"{snap['in_flight']} request(s) in flight, no progress for {snap['last_progress_age_s']}s")


# --- patches -----------------------------------------------------------------

_generator_init = server.ResponseGenerator.__init__


def generator_init(self, *args, **kwargs):
    _generator_init(self, *args, **kwargs)
    generator["instance"] = self


_batch_next = BatchGenerator.next


def batch_next(self):
    t0 = time.monotonic()
    prompt_responses, gen_responses = _batch_next(self)
    if prompt_responses or gen_responses:
        now = time.monotonic()
        progress["last"] = now
        progress["tokens"] += len(gen_responses)
        prefilled = 0
        for r in prompt_responses:
            done = r.progress[0]
            prefilled += max(0, done - prefill_done.get(r.uid, 0))
            if r.end_of_prompt:
                prefill_done.pop(r.uid, None)
            else:
                prefill_done[r.uid] = done
        if len(prefill_done) > 1000:  # requests cancelled mid-prefill never send end_of_prompt
            prefill_done.clear()
        with lock:
            steps.append((now, now - t0, len(gen_responses), prefilled))
            while steps and steps[0][0] < now - SPEED_WINDOW:
                steps.popleft()
    return prompt_responses, gen_responses


_do_post = server.APIHandler.do_POST


def do_post(self):
    rid = self.headers.get("X-Request-Id") or f"anon-{threading.get_ident()}-{time.time_ns()}"
    with lock:
        if not in_flight:  # idle until now: don't count the idle time as a stall
            progress["last"] = time.monotonic()
        in_flight[rid] = {"job": self.headers.get("X-Job"), "since": time.time()}
    try:
        return _do_post(self)
    finally:
        with lock:
            in_flight.pop(rid, None)


def handle_health_check(self):
    snap = snapshot()
    body = json.dumps(snap).encode()
    self._set_completion_headers(200 if snap["status"] == "ok" else 503)
    self.end_headers()
    self.wfile.write(body)
    self.wfile.flush()


_log_message = server.APIHandler.log_message


def log_message(self, fmt, *args):
    if self.command == "GET" and (self.path == "/health" or self.path.startswith("/v1/models")):
        return
    _log_message(self, fmt, *args)


_set_wired_limit = mx.set_wired_limit


def set_wired_limit(limit):
    return _set_wired_limit(min(limit, WIRED_LIMIT))


mx.set_wired_limit = set_wired_limit
mx.set_cache_limit(CACHE_LIMIT)
server.ResponseGenerator.__init__ = generator_init
BatchGenerator.next = batch_next
server.APIHandler.do_POST = do_post
server.APIHandler.handle_health_check = handle_health_check
server.APIHandler.log_message = log_message
threading.excepthook = excepthook

if __name__ == "__main__":
    threading.Thread(target=watchdog, name="qwen-watchdog", daemon=True).start()
    server.main()
