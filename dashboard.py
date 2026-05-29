"""
dashboard.py
------------
The Live Dashboard. Runs alongside agent.py and talks to it ONLY through Redis.

It demonstrates all three challenge options at once and lets you toggle between
views with the keyboard:

  [1] OVERVIEW  - everything on one screen
  [2] LIVE      - live metrics + sample counter
  [3] HISTORY   - 1-minute moving CPU average + sparkline   (Option A)
  [4] ALERTS    - scrolling log of alerts received via Pub/Sub
  [q] quit

Always active, regardless of the selected view:
  * Option B: thresholds are read live from `config:alerts`, so changes made by
    controller.py show up here immediately.
  * Option C: if `status:current` has expired (agent stopped), the screen is
    cleared and a big [CRITICAL] Agent Offline banner is shown.

A background thread holds the Pub/Sub subscription so alerts are caught
asynchronously the instant they are published.

Run it in its own terminal:  python dashboard.py
"""

import json
import select
import sys
import termios
import threading
import time
import tty
from collections import deque

import common

# Shared state between the Pub/Sub listener thread and the render loop.
# A deque with maxlen keeps only the most recent alerts automatically.
recent_alerts = deque(maxlen=12)
alert_flash = {"until": 0.0, "text": ""}  # drives a brief on-screen flash
state_lock = threading.Lock()

# ANSI helpers for a bit of colour / cursor control.
CLEAR = "\033[2J\033[H"      # clear screen + move cursor home
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
BOLD = "\033[1m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Pub/Sub listener (runs in its own thread)
# ---------------------------------------------------------------------------
def alert_listener(r, stop_event):
    """Block on the alerts channel and record every message that arrives.

    Because this lives in a separate thread, an alert is captured the exact
    moment the agent publishes it, independently of the render loop's timing.
    """
    pubsub = r.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(common.CHANNEL_ALERTS)
    for message in pubsub.listen():
        if stop_event.is_set():
            break
        if message["type"] != "message":
            continue
        try:
            alert = json.loads(message["data"])
        except (ValueError, TypeError):
            continue
        text = (f"{alert['timestamp']}  {alert['resource']} "
                f"{alert['value']}% >= {alert['threshold']}%")
        with state_lock:
            recent_alerts.appendleft(text)
            alert_flash["text"] = text
            alert_flash["until"] = time.time() + 2.0  # flash for 2 seconds
    pubsub.close()


# ---------------------------------------------------------------------------
# Non-blocking keyboard handling (for the view toggle)
# ---------------------------------------------------------------------------
def read_key():
    """Return a single pressed key if one is waiting, else None (non-blocking)."""
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.read(1)
    return None


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def bar(value, limit, width=30):
    """Return a coloured text gauge for a 0-100 metric."""
    filled = int((min(value, 100) / 100) * width)
    colour = GREEN
    if value >= limit:
        colour = RED
    elif value >= limit * 0.8:
        colour = YELLOW
    gauge = "█" * filled + "░" * (width - filled)
    return f"{colour}{gauge}{RESET} {value:5.1f}%"


def sparkline(values):
    """Render a list of 0-100 values as a compact unicode sparkline."""
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    out = []
    for v in values:
        idx = int((min(v, 100) / 100) * (len(blocks) - 1))
        out.append(blocks[idx])
    return "".join(out)


def render_header(view, thresholds, online):
    lines = []
    status = (f"{GREEN}● ONLINE{RESET}" if online
              else f"{RED}● OFFLINE{RESET}")
    lines.append(f"{BOLD}{CYAN}  SYSTEM RESOURCE MONITOR{RESET}    {status}")
    lines.append(f"{DIM}  thresholds  cpu>={thresholds['cpu']}%  "
                 f"mem>={thresholds['memory']}%  "
                 f"disk>={thresholds['disk']}%{RESET}")
    tabs = {"1": "OVERVIEW", "2": "LIVE", "3": "HISTORY", "4": "ALERTS"}
    rendered = []
    for key, name in tabs.items():
        if key == view:
            rendered.append(f"{BOLD}{CYAN}[{key}:{name}]{RESET}")
        else:
            rendered.append(f"{DIM}{key}:{name}{RESET}")
    lines.append("  " + "  ".join(rendered) + f"   {DIM}q:quit{RESET}")
    lines.append(f"{DIM}  " + "-" * 52 + RESET)
    return "\n".join(lines)


def render_live(snapshot, counter, thresholds):
    return "\n".join([
        f"  {BOLD}LIVE METRICS{RESET}   (sample #{counter})  "
        f"@ {snapshot.get('timestamp', '--')}",
        f"  CPU    {bar(float(snapshot['cpu']), thresholds['cpu'])}",
        f"  MEMORY {bar(float(snapshot['memory']), thresholds['memory'])}",
        f"  DISK   {bar(float(snapshot['disk']), thresholds['disk'])}",
    ])


def render_history(history, thresholds):
    if not history:
        return f"  {DIM}No history yet...{RESET}"
    avg = sum(history) / len(history)
    latest = history[-1]
    colour = RED if avg >= thresholds["cpu"] else GREEN
    return "\n".join([
        f"  {BOLD}CPU HISTORY{RESET}  (last {len(history)} samples, "
        f"~{len(history)}s window)",
        f"  1-min moving average:  {colour}{avg:5.1f}%{RESET}",
        f"  latest sample:         {latest:5.1f}%",
        f"  min/max in window:     {min(history):.1f}% / {max(history):.1f}%",
        f"  {CYAN}{sparkline(history)}{RESET}",
    ])


def render_alerts():
    with state_lock:
        alerts = list(recent_alerts)
    if not alerts:
        return f"  {BOLD}ALERTS{RESET}\n  {DIM}No alerts received yet.{RESET}"
    lines = [f"  {BOLD}ALERTS{RESET}  ({len(alerts)} most recent)"]
    for a in alerts:
        lines.append(f"  {RED}⚠{RESET}  {a}")
    return "\n".join(lines)


def render_offline():
    return "\n".join([
        "",
        f"  {RED}{BOLD}{'#' * 50}{RESET}",
        f"  {RED}{BOLD}#  [CRITICAL] Agent Offline: Data is Stale.       #{RESET}",
        f"  {RED}{BOLD}{'#' * 50}{RESET}",
        "",
        f"  {DIM}status:current has expired in Redis.{RESET}",
        f"  {DIM}Start the agent again:  python agent.py{RESET}",
    ])


def render_flash():
    with state_lock:
        active = time.time() < alert_flash["until"]
        text = alert_flash["text"]
    if active:
        return f"\n  {RED}{BOLD}>>> NEW ALERT: {text} <<<{RESET}"
    return ""


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def read_thresholds(r):
    raw = r.hgetall(common.KEY_CONFIG)
    out = {}
    for field, default in common.DEFAULT_THRESHOLDS.items():
        try:
            out[field] = float(raw.get(field, default))
        except (TypeError, ValueError):
            out[field] = default
    return out


def main():
    r = common.get_redis()
    try:
        r.ping()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Could not connect to Redis -> {exc}")
        print("Start it with:  brew services start redis")
        return

    stop_event = threading.Event()
    listener = threading.Thread(
        target=alert_listener, args=(r, stop_event), daemon=True
    )
    listener.start()

    view = "1"  # default to OVERVIEW

    # Put the terminal in cbreak mode so single keystrokes are read instantly
    # without waiting for Enter. We always restore it in the finally block.
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write(HIDE_CURSOR)
        while True:
            key = read_key()
            if key:
                if key in ("q", "Q"):
                    break
                if key in ("1", "2", "3", "4"):
                    view = key

            # --- Pull the latest state from Redis ---------------------------
            snapshot = r.hgetall(common.KEY_STATUS)
            counter = r.get(common.KEY_COUNTER) or "0"
            thresholds = read_thresholds(r)
            history_raw = r.lrange(common.KEY_HISTORY_CPU, 0, -1)
            # The list is newest-first (LPUSH); reverse for chronological order.
            history = [float(x) for x in reversed(history_raw)]

            online = bool(snapshot)  # empty dict => key expired => Option C

            out = [CLEAR]
            if not online:
                # Option C: prominent stale-data warning.
                out.append(render_header(view, thresholds, online))
                out.append(render_offline())
            else:
                out.append(render_header(view, thresholds, online))
                if view == "1":  # OVERVIEW: show all panels
                    out.append(render_live(snapshot, counter, thresholds))
                    out.append("")
                    out.append(render_history(history, thresholds))
                    out.append("")
                    out.append(render_alerts())
                elif view == "2":
                    out.append(render_live(snapshot, counter, thresholds))
                elif view == "3":
                    out.append(render_history(history, thresholds))
                elif view == "4":
                    out.append(render_alerts())
                out.append(render_flash())

            sys.stdout.write("\n".join(out) + "\n")
            sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        sys.stdout.write(SHOW_CURSOR + RESET + "\n")
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        print("[DASHBOARD] Stopped.")


if __name__ == "__main__":
    main()
