"""
app.py
------
All-in-one Flask application that runs the WHOLE project in a single process,
while still using Redis as the "multi-tool" intermediary exactly like the
two-program version:

  * A background thread runs the Monitoring Agent loop (sampling -> Redis).
      - caching volatile data   -> hash `status:current` with a TTL
      - dynamic configuration   -> hash `config:alerts`
      - rolling history list    -> list `history:cpu` (Option A)
      - event-driven messaging  -> Pub/Sub channel `alerts`
  * Flask serves a realtime, BIOS-style web dashboard.
  * Server-Sent Events (SSE) push live state every second AND forward Pub/Sub
    alerts the instant they happen.
  * A small REST endpoint lets the page change thresholds live (Option B), and
    the page shows the [CRITICAL] Agent Offline banner if the data goes stale
    (Option C).

Run it:  python app.py   then open http://localhost:5000
"""

import json
import os
import threading
import time
from datetime import datetime

import psutil
from flask import Flask, Response, jsonify, render_template, request

import common
# Reuse the exact same agent helpers as the standalone agent.py.
from agent import ensure_default_config, publish_alert, read_thresholds, sample_metrics

app = Flask(__name__)
# Pick up template/static edits without needing a restart.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Guard so the agent thread is started exactly once.
_agent_started = False
_agent_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Background Monitoring Agent (same behaviour as agent.py, in a thread)
# ---------------------------------------------------------------------------
def agent_loop():
    r = common.get_redis()
    ensure_default_config(r)
    psutil.cpu_percent(interval=None)  # prime so first reading isn't 0.0

    while True:
        cpu, memory, disk = sample_metrics()
        thresholds = read_thresholds(r)

        snapshot = {
            "cpu": round(cpu, 1),
            "memory": round(memory, 1),
            "disk": round(disk, 1),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }

        pipe = r.pipeline()
        pipe.hset(common.KEY_STATUS, mapping=snapshot)
        pipe.expire(common.KEY_STATUS, common.STATUS_TTL_SECONDS)
        pipe.incr(common.KEY_COUNTER)
        pipe.lpush(common.KEY_HISTORY_CPU, round(cpu, 1))
        pipe.ltrim(common.KEY_HISTORY_CPU, 0, common.HISTORY_LENGTH - 1)
        pipe.execute()

        for resource, value, limit in (
            ("CPU", cpu, thresholds["cpu"]),
            ("MEMORY", memory, thresholds["memory"]),
            ("DISK", disk, thresholds["disk"]),
        ):
            if value >= limit:
                publish_alert(r, resource, value, limit)

        time.sleep(1)


def start_agent_once():
    global _agent_started
    with _agent_lock:
        if _agent_started:
            return
        t = threading.Thread(target=agent_loop, daemon=True)
        t.start()
        _agent_started = True


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def build_state():
    """Read everything the dashboard needs from Redis in one shot."""
    r = common.get_redis()
    snapshot = r.hgetall(common.KEY_STATUS)
    counter = int(r.get(common.KEY_COUNTER) or 0)
    raw_cfg = r.hgetall(common.KEY_CONFIG)
    history_raw = r.lrange(common.KEY_HISTORY_CPU, 0, -1)
    history = [float(x) for x in reversed(history_raw)]  # chronological order

    thresholds = {}
    for field, default in common.DEFAULT_THRESHOLDS.items():
        try:
            thresholds[field] = float(raw_cfg.get(field, default))
        except (TypeError, ValueError):
            thresholds[field] = default

    online = bool(snapshot)  # empty => status:current expired => Option C
    metrics = {
        "cpu": float(snapshot.get("cpu", 0)) if online else 0.0,
        "memory": float(snapshot.get("memory", 0)) if online else 0.0,
        "disk": float(snapshot.get("disk", 0)) if online else 0.0,
    }
    avg = round(sum(history) / len(history), 1) if history else 0.0

    return {
        "online": online,
        "counter": counter,
        "timestamp": snapshot.get("timestamp", "--:--:--"),
        "metrics": metrics,
        "thresholds": thresholds,
        "history": history,
        "avg": avg,
        "min": round(min(history), 1) if history else 0.0,
        "max": round(max(history), 1) if history else 0.0,
        "window": len(history),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    return jsonify(build_state())


@app.route("/api/config", methods=["POST"])
def api_config():
    """Update a threshold live (Option B controller, from the browser)."""
    data = request.get_json(force=True, silent=True) or {}
    resource = str(data.get("resource", "")).lower()
    if resource not in common.DEFAULT_THRESHOLDS:
        return jsonify({"ok": False, "error": "invalid resource"}), 400
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "value must be a number"}), 400
    if not 0 <= value <= 100:
        return jsonify({"ok": False, "error": "value must be 0-100"}), 400

    common.get_redis().hset(common.KEY_CONFIG, resource, value)
    return jsonify({"ok": True, "resource": resource, "value": value})


@app.route("/api/stream")
def api_stream():
    """SSE stream: live state every second + instant Pub/Sub alerts."""

    def event_stream():
        r = common.get_redis()
        pubsub = r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(common.CHANNEL_ALERTS)
        last_state = 0.0
        try:
            while True:
                # Forward any alert the agent published, immediately.
                message = pubsub.get_message(timeout=0.25)
                if message and message["type"] == "message":
                    yield f"event: alert\ndata: {message['data']}\n\n"

                # Push a full state refresh about once per second.
                now = time.time()
                if now - last_state >= 1.0:
                    yield f"event: state\ndata: {json.dumps(build_state())}\n\n"
                    last_state = now
        finally:
            pubsub.close()

    return Response(event_stream(), mimetype="text/event-stream")


if __name__ == "__main__":
    # Start the agent before serving so data exists immediately.
    start_agent_once()
    # Port 5000 is often taken on macOS (AirPlay Receiver), so default to 5050.
    # Override with: PORT=8080 python app.py
    port = int(os.environ.get("PORT", 5050))
    print(f" * Open the dashboard at http://localhost:{port}")
    # threaded=True so SSE streams don't block other requests.
    # use_reloader=False so we don't spawn a second agent thread.
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
