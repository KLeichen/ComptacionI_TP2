"""
controller.py
-------------
The Configuration Controller (Option B: Dynamic Architecture).

This script changes the alert thresholds stored in the Redis hash
`config:alerts` *while the agent is running*. Because agent.py re-reads that
hash on every loop, the new value takes effect on the very next sample -- no
restart required.

Demo idea: lower the CPU threshold to something tiny (e.g. 5) and watch
dashboard.py light up with an alert within a second.

Run it in its own terminal:  python controller.py
"""

import common


def show_current(r):
    """Print the thresholds currently stored in Redis."""
    current = r.hgetall(common.KEY_CONFIG)
    if not current:
        print("(no config yet -- start agent.py once to seed defaults)")
        return
    print("Current thresholds:")
    for field in common.DEFAULT_THRESHOLDS:
        print(f"  {field:8} >= {current.get(field, '?')}%")


def main():
    r = common.get_redis()
    try:
        r.ping()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Could not connect to Redis -> {exc}")
        print("Start it with:  brew services start redis")
        return

    print("=== Configuration Controller ===")
    print("Type 'q' at any prompt to quit.\n")

    while True:
        show_current(r)

        resource = input(
            "\nWhich threshold to change? [cpu/memory/disk] (q to quit): "
        ).strip().lower()
        if resource in ("q", "quit", "exit"):
            break
        if resource not in common.DEFAULT_THRESHOLDS:
            print(f"  '{resource}' is not a valid resource.\n")
            continue

        value_raw = input(f"New {resource} threshold (0-100): ").strip()
        if value_raw.lower() in ("q", "quit", "exit"):
            break
        try:
            value = float(value_raw)
        except ValueError:
            print("  Not a number, try again.\n")
            continue
        if not 0 <= value <= 100:
            print("  Value must be between 0 and 100.\n")
            continue

        # The single write that the running agent will pick up immediately.
        r.hset(common.KEY_CONFIG, resource, value)
        print(f"  -> {resource} threshold is now {value}%. "
              f"The agent will use it on its next sample.\n")

    print("Controller closed.")


if __name__ == "__main__":
    main()
