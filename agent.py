"""
agent.py
--------
The Monitoring Agent.

Every second it:
  1. Samples live system metrics (CPU, memory, disk) with psutil.
  2. Caches the sample in the Redis hash `status:current` with a short TTL
     (volatile data that disappears if the agent stops -> enables Option C).
  3. Increments the `metrics:counter` key.
  4. Pushes the CPU value into the `history:cpu` list, trimmed to the last 60
     samples (Option A: historical average tracker).
  5. Re-reads the thresholds from the `config:alerts` hash on every loop, so a
     change made by controller.py is picked up immediately (Option B).
  6. PUBLISHes an alert on the `alerts` channel the instant a threshold is
     breached (event-driven messaging).

Run it in its own terminal:  python agent.py
"""

import json
import time
from datetime import datetime

import psutil

import common


def ensure_default_config(r):
    """Seed `config:alerts` with default thresholds the first time we run.

    Using HSETNX-style logic (only set when missing) means a restart of the
    agent will NOT clobber a threshold the user already tuned with the
    controller.
    """
    for field, value in common.DEFAULT_THRESHOLDS.items():
        r.hsetnx(common.KEY_CONFIG, field, value)


def read_thresholds(r):
    """Read the live thresholds from Redis, falling back to defaults."""
    raw = r.hgetall(common.KEY_CONFIG)
    thresholds = {}
    for field, default in common.DEFAULT_THRESHOLDS.items():
        try:
            thresholds[field] = float(raw.get(field, default))
        except (TypeError, ValueError):
            thresholds[field] = default
    return thresholds


def sample_metrics():
    """Take one snapshot of the machine's resource usage."""
    # interval=None makes psutil return the CPU usage since the *previous*
    # call instantly, instead of blocking. We sleep 1s ourselves in the loop.
    cpu = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    return cpu, memory, disk


def publish_alert(r, resource, value, threshold):
    """Broadcast a structured alert message to every subscriber."""
    alert = {
        "resource": resource,
        "value": round(value, 1),
        "threshold": threshold,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }
    r.publish(common.CHANNEL_ALERTS, json.dumps(alert))
    return alert


def main():
    r = common.get_redis()

    # Fail fast with a friendly message if Redis is not reachable.
    try:
        r.ping()
    except Exception as exc:  # noqa: BLE001 - we want any connection error here
        print(f"[ERROR] Could not connect to Redis at "
              f"{common.REDIS_HOST}:{common.REDIS_PORT} -> {exc}")
        print("Start it with:  brew services start redis")
        return

    ensure_default_config(r)

    # Prime psutil so the very first reading is not a misleading 0.0%.
    psutil.cpu_percent(interval=None)

    print("[AGENT] Monitoring started. Sampling every 1s. Press Ctrl+C to stop.")

    try:
        while True:
            cpu, memory, disk = sample_metrics()
            thresholds = read_thresholds(r)

            # --- 2. Cache the volatile snapshot with a TTL -----------------
            snapshot = {
                "cpu": round(cpu, 1),
                "memory": round(memory, 1),
                "disk": round(disk, 1),
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }
            # A pipeline groups the writes into a single round-trip.
            pipe = r.pipeline()
            pipe.hset(common.KEY_STATUS, mapping=snapshot)
            pipe.expire(common.KEY_STATUS, common.STATUS_TTL_SECONDS)
            # --- 3. Live counter ------------------------------------------
            pipe.incr(common.KEY_COUNTER)
            # --- 4. Rolling history list (Option A) -----------------------
            pipe.lpush(common.KEY_HISTORY_CPU, round(cpu, 1))
            pipe.ltrim(common.KEY_HISTORY_CPU, 0, common.HISTORY_LENGTH - 1)
            pipe.execute()

            # --- 5 & 6. Threshold check + instant alert -------------------
            checks = (
                ("CPU", cpu, thresholds["cpu"]),
                ("MEMORY", memory, thresholds["memory"]),
                ("DISK", disk, thresholds["disk"]),
            )
            for resource, value, limit in checks:
                if value >= limit:
                    alert = publish_alert(r, resource, value, limit)
                    print(f"[ALERT] {alert['resource']} {alert['value']}% "
                          f">= {alert['threshold']}% (published)")

            print(f"[AGENT] cpu={snapshot['cpu']}%  mem={snapshot['memory']}%  "
                  f"disk={snapshot['disk']}%  @ {snapshot['timestamp']}")

            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[AGENT] Stopped by user. status:current will expire shortly.")


if __name__ == "__main__":
    main()
