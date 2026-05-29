"""
common.py
---------
Shared configuration and helpers used by every program in the project
(agent.py, dashboard.py and controller.py).

Keeping the Redis connection details and the key names in a single place
avoids "magic strings" scattered across the codebase: if a key name ever
changes, it only has to be edited here.
"""

import redis

# ---------------------------------------------------------------------------
# Redis connection
# ---------------------------------------------------------------------------
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0

# ---------------------------------------------------------------------------
# Redis "schema": the keys/channels every program agrees on.
# ---------------------------------------------------------------------------
# Hash holding the most recent sample. It is given a short TTL so that, if the
# agent dies, the key expires and the dashboard can detect "Agent Offline".
KEY_STATUS = "status:current"

# Simple integer counter incremented on every successful sample.
KEY_COUNTER = "metrics:counter"

# Hash holding the live, editable alert thresholds (Option B).
KEY_CONFIG = "config:alerts"

# List holding the last N CPU samples used for the moving average (Option A).
KEY_HISTORY_CPU = "history:cpu"

# Pub/Sub channel used to broadcast alerts the instant a threshold is breached.
CHANNEL_ALERTS = "alerts"

# How long (seconds) the status hash lives before Redis deletes it. It must be
# larger than the sampling interval so a healthy agent keeps it alive, but
# small enough that "staleness" is detected quickly when the agent stops.
STATUS_TTL_SECONDS = 3

# Number of CPU samples kept in the history list (60 -> a 1 minute window when
# sampling once per second).
HISTORY_LENGTH = 60

# Default thresholds written the first time the agent runs. They can be changed
# at runtime with controller.py without restarting anything.
DEFAULT_THRESHOLDS = {
    "cpu": 80.0,
    "memory": 85.0,
    "disk": 90.0,
}


def get_redis():
    """Return a Redis client that decodes responses to str (not bytes)."""
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True,
    )
