# Real-Time System Resource Monitor & Alert Dashboard

A production-inspired system monitor built with **Python + Redis**. Redis is used
as a *multi-tool* solving three distinct problems:

| Problem | Redis feature used | Where |
|---|---|---|
| **Caching volatile data** | Hash with a TTL (`status:current`, expires in 3s) | `agent.py` |
| **Dynamic configuration** | Hash (`config:alerts`) editable at runtime | `controller.py` |
| **Event-driven messaging** | Pub/Sub channel (`alerts`) | `agent.py` → `dashboard.py` |

All three **challenge options are implemented**, and the dashboard has a live
**toggle** to switch what you see.

---

## What each file does

- **`common.py`** – shared Redis connection + the key/channel names ("schema")
  used by every script.
- **`agent.py`** – the Monitoring Agent. Samples CPU/MEM/DISK every second,
  caches them, keeps a rolling history list, and publishes alerts.
- **`dashboard.py`** – the Live Dashboard. Subscribes to alerts in a background
  thread and renders a toggleable, colour terminal UI.
- **`controller.py`** – the Configuration Controller. Changes thresholds at
  runtime with no restart.
- **`app.py`** – an all-in-one **Flask web app** that runs the agent loop in a
  background thread and serves a realtime, **BIOS-style** web dashboard. It
  still uses Redis as the multi-tool intermediary (caching hash + TTL, config
  hash, history list, Pub/Sub). Templates live in `templates/` and assets in
  `static/`.

There are two equivalent ways to run the project: the **terminal version**
(`agent.py` + `dashboard.py` + `controller.py`) or the **web version**
(`app.py`). Use one or the other, not both at once (running two agents would
double-count the metrics).

## Challenge options included

- **Option A – Historical Average Tracker.** The agent does
  `LPUSH history:cpu` + `LTRIM history:cpu 0 59` to keep the last 60 CPU
  samples. The dashboard's **HISTORY** view shows the 1-minute moving average
  plus a sparkline.
- **Option B – Configuration Controller.** `controller.py` writes to the
  `config:alerts` hash; the agent re-reads it every loop, so lowering a
  threshold triggers an alert instantly without restarting anything.
- **Option C – Graceful Degradation.** If `status:current` has expired
  (agent stopped), the dashboard clears the screen and prints a prominent
  **`[CRITICAL] Agent Offline: Data is Stale.`** banner.

---

## Setup

> Requires Python 3 and a running Redis instance on `localhost:6379`.

### 1. Install Redis (macOS / Homebrew)

```bash
brew install redis
brew services start redis   # starts Redis and keeps it running
redis-cli ping              # should print: PONG
```

### 2. Install the Python dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Running the project

### Option 1 — Web app (BIOS-style realtime dashboard)

One process runs the agent and serves the dashboard:

```bash
source venv/bin/activate
python app.py
# then open the URL it prints, e.g. http://localhost:5050
```

Port 5000 is usually taken on macOS by AirPlay Receiver, so the app defaults to
**5050**. Override it with `PORT=8080 python app.py`.

In the browser (a single futuristic screen shows everything at once):

- Three **radial gauges** (CPU / MEMORY / DISK), a **telemetry** panel, the
  realtime **CPU history chart** (1-minute moving average — Option A) and the
  live **alert feed** are all visible simultaneously.
- The **⚙ THRESHOLDS** button opens a slide-in drawer — the only secondary
  screen — with sliders to change limits live (Option B). The agent applies the
  new value on its next sample; alerts arrive instantly via SSE forwarding the
  Redis Pub/Sub channel.
- When any resource reaches its limit, a big **flashing danger sign** appears in
  the center of the screen while **red alarm lights pulse** in the background and
  the breached gauge glows red.
- If the data goes stale (agent/app stopped), a red **[CRITICAL] Agent Offline:
  Data is Stale.** overlay appears (Option C).

### Option 2 — Terminal version

Open **separate terminal windows** (activate the venv in each with
`source venv/bin/activate`).

**Terminal 1 – the agent:**

```bash
python agent.py
```

**Terminal 2 – the dashboard:**

```bash
python dashboard.py
```

**Terminal 3 (optional) – change a threshold live (Option B):**

```bash
python controller.py
```

### Dashboard controls (the toggle)

While `dashboard.py` is focused, press:

- `1` – **OVERVIEW** (live metrics + history + alerts together)
- `2` – **LIVE** (live metrics + sample counter)
- `3` – **HISTORY** (1-minute moving average + sparkline — Option A)
- `4` – **ALERTS** (scrolling Pub/Sub alert log)
- `q` – quit

### How to demo each option

- **Option A:** let it run ~1 minute, press `3`, watch the moving average.
- **Option B:** with the agent running, run `controller.py`, set the `cpu`
  threshold to `5`, and watch an alert appear on the dashboard within a second.
- **Option C:** stop `agent.py` (Ctrl+C). After ~3 seconds the dashboard shows
  the **Agent Offline** banner because `status:current` expired.

---

## Short write-up

**Why use a Redis Pub/Sub channel for alerts instead of just checking the key
value in a loop?**

Polling a key in a loop (the alternative) has three problems that Pub/Sub
solves:

1. **Latency vs. waste trade-off.** Polling forces a bad choice: poll *often*
   and you hammer Redis with constant `GET`s that mostly return "nothing
   changed" (wasted CPU and network), or poll *rarely* and you add delay before
   an alert is noticed. Pub/Sub is **push-based** — the subscriber is woken the
   instant `PUBLISH` runs, so alerts are delivered with near-zero latency and
   zero busy-work in between.

2. **No missed/duplicated events.** A polled value only tells you the *current*
   state. If CPU spikes above the threshold and drops back between two polls,
   the loop never sees it. With Pub/Sub each breach is an explicit *event*, so
   transient spikes aren't lost, and you won't re-fire the same alert every
   poll just because the value is still high.

3. **Decoupling and fan-out.** Pub/Sub cleanly separates the producer (agent)
   from the consumers (dashboards). Any number of dashboards — or future
   consumers like a logger, an email notifier, or a phone-notification service
   — can subscribe to the same `alerts` channel without the agent knowing or
   caring who is listening. Polling would require every consumer to implement
   and tune its own loop against the data keys.

In short: the cached `status:current` key answers *"what is the value right
now?"*, while the Pub/Sub channel answers *"tell me the moment something goes
wrong"* — and those are genuinely different jobs.
