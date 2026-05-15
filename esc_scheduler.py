"""
ESC (Escalation) Scheduler
---------------------------
Schedules 30-minute Escalation sessions (18:30-19:00 Budapest time) for
Wed/Thu/Fri across a given date range.

Logic:
1. Check if Eszter has a shift covering 18:30-19:00 Budapest — if so, book her
2. If not, find all available agents (shift ends at 19:00 Budapest that day)
3. From available agents, pick whoever has done ESC least recently
4. Flag any days where nobody is available

Usage:
    ASSEMBLED_API_KEY=xxx START_DATE=03/06/2026 END_DATE=30/06/2026 python esc_scheduler.py

Environment variables:
    ASSEMBLED_API_KEY  — required
    START_DATE         — required, DD/MM/YYYY
    END_DATE           — required, DD/MM/YYYY
"""

import os
import sys
import requests
from datetime import datetime, timedelta, timezone
import pytz

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

API_KEY     = os.environ["ASSEMBLED_API_KEY"]
BASE_URL    = "https://api.assembledhq.com"
BUDAPEST    = pytz.timezone("Europe/Budapest")
SCHEDULE_ID = "ce63792c-57e1-41ac-85a5-9f09b230c791"

ESC_TYPE_ID = "1a64d3a1-dff6-40c1-b223-3928417f6ffb"

# ESC slot — fixed time, always
ESC_START_H, ESC_START_M = 18, 30
ESC_END_H,   ESC_END_M   = 19, 0

# Eszter — checked first
ESZTER_ID = "d05599b8-61b4-4f71-9b15-227f7c69af46"

# Fallback agents — rotated fairly based on who did ESC least recently
FALLBACK_AGENTS = {
    "Henriett":  "1d9e4692-7388-47be-9df1-c9a7bcd1a6cf",
    "Jad":       "109bd604-1e51-4fe4-b653-0002bab43911",
    "Katalin":   "5bea70c6-04e4-41d3-9640-1fb53a4e4015",
    "Krisztina": "b8701026-bd7f-4a18-9856-dd67a9d480fa",
}

# How far back to look for recent ESC history (days)
ESC_HISTORY_DAYS = 60


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def get_wednesdays_in_range(start: datetime, end: datetime):
    """Return all Wed/Thu/Fri dates between start and end inclusive."""
    dates = []
    current = start
    while current.date() <= end.date():
        if current.weekday() in (2, 3, 4):  # Wed=2, Thu=3, Fri=4
            dates.append(current)
        current += timedelta(days=1)
    return dates


def esc_times(date: datetime):
    """Return (start_utc, end_utc) for the ESC slot on a given date."""
    start_local = BUDAPEST.localize(datetime(date.year, date.month, date.day, ESC_START_H, ESC_START_M))
    end_local   = BUDAPEST.localize(datetime(date.year, date.month, date.day, ESC_END_H, ESC_END_M))
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def get_agent_schedule(agent_id: str, date: datetime):
    """Fetch all activities for an agent on a given date."""
    start_local = BUDAPEST.localize(datetime(date.year, date.month, date.day, 0, 0))
    end_local   = BUDAPEST.localize(datetime(date.year, date.month, date.day, 23, 59))
    start_ts = int(start_local.astimezone(timezone.utc).timestamp())
    end_ts   = int(end_local.astimezone(timezone.utc).timestamp())

    params = {
        "agents":                 agent_id,
        "start_time":             start_ts,
        "end_time":               end_ts,
        "return_full_schedule":   "true",
        "include_activity_types": "true",
        "schedule_id":            SCHEDULE_ID,
    }

    resp = requests.get(
        f"{BASE_URL}/v0/activities",
        auth=(API_KEY, ""),
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()

    activity_types = data.get("activity_types", {})
    type_info = {
        tid: {"name": t.get("name", ""), "productive": t.get("productive", False)}
        for tid, t in activity_types.items()
    }

    activities = list(data.get("activities", {}).values())
    for a in activities:
        info = type_info.get(a.get("type_id", ""), {})
        a["type_name"]  = info.get("name", "Unknown")
        a["productive"] = info.get("productive", False)

    return activities


def agent_covers_esc_window(activities: list, esc_start_utc: datetime, esc_end_utc: datetime) -> bool:
    """
    Check if agent has a productive event that covers the ESC window,
    i.e. their shift runs through 18:30-19:00 Budapest.
    """
    esc_start_ts = int(esc_start_utc.timestamp())
    esc_end_ts   = int(esc_end_utc.timestamp())

    productive = [a for a in activities if a.get("productive")]
    if not productive:
        return False

    # Check if any productive event covers the full ESC window
    for a in productive:
        if a["start_time"] <= esc_start_ts and a["end_time"] >= esc_end_ts:
            return True

    # Also check if the last productive event ends at or after 19:00 Budapest
    last_end = max(a["end_time"] for a in productive)
    return last_end >= esc_end_ts


def get_last_esc_date(agent_id: str, before_date: datetime) -> datetime | None:
    """
    Look back ESC_HISTORY_DAYS to find when this agent last had an ESC event.
    Returns the date of their most recent ESC, or None if never.
    """
    end_ts   = int(before_date.astimezone(timezone.utc).timestamp())
    start_ts = int((before_date - timedelta(days=ESC_HISTORY_DAYS)).astimezone(timezone.utc).timestamp())

    params = {
        "agents":     agent_id,
        "start_time": start_ts,
        "end_time":   end_ts,
        "schedule_id": SCHEDULE_ID,
    }

    try:
        resp = requests.get(
            f"{BASE_URL}/v0/activities",
            auth=(API_KEY, ""),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        activities = list(data.get("activities", {}).values())

        esc_events = [a for a in activities if a.get("type_id") == ESC_TYPE_ID]
        if not esc_events:
            return None

        last_ts = max(a["start_time"] for a in esc_events)
        return datetime.fromtimestamp(last_ts, tz=BUDAPEST)
    except Exception:
        return None


def create_esc_event(agent_id: str, start_ts: int, end_ts: int):
    """Create an ESC event via the Assembled API."""
    payload = {
        "agent_id":    agent_id,
        "type_id":     ESC_TYPE_ID,
        "start_time":  start_ts,
        "end_time":    end_ts,
        "schedule_id": SCHEDULE_ID,
    }
    resp = requests.post(
        f"{BASE_URL}/v0/activities",
        auth=(API_KEY, ""),
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main():
    start_str = os.environ.get("START_DATE")
    end_str   = os.environ.get("END_DATE")

    if not start_str or not end_str:
        print("ERROR: START_DATE and END_DATE are required (DD/MM/YYYY)")
        sys.exit(1)

    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            start_date = BUDAPEST.localize(datetime.strptime(start_str, fmt))
            end_date   = BUDAPEST.localize(datetime.strptime(end_str,   fmt))
            break
        except ValueError:
            continue
    else:
        print("ERROR: Dates must be DD/MM/YYYY or YYYY-MM-DD")
        sys.exit(1)

    dates = get_wednesdays_in_range(start_date, end_date)
    day_names = {2: "Wednesday", 3: "Thursday", 4: "Friday"}

    print(f"Scheduling ESC for {len(dates)} day(s) from {start_str} to {end_str}")
    print(f"Target: schedule {SCHEDULE_ID}")
    print("=" * 60)

    skipped = []

    for date in dates:
        day_name = day_names[date.weekday()]
        print(f"\n── {day_name} {date.strftime('%d %b %Y')} ──")

        esc_start_utc, esc_end_utc = esc_times(date)
        esc_start_local = esc_start_utc.astimezone(BUDAPEST)
        esc_end_local   = esc_end_utc.astimezone(BUDAPEST)
        print(f"  ESC slot: {esc_start_local.strftime('%H:%M')}–{esc_end_local.strftime('%H:%M')} Budapest")

        booked = False

        # ── Step 1: Try Eszter first ──
        try:
            eszter_activities = get_agent_schedule(ESZTER_ID, date)
            if agent_covers_esc_window(eszter_activities, esc_start_utc, esc_end_utc):
                print(f"  ✓ Eszter is available — booking her")
                create_esc_event(ESZTER_ID, int(esc_start_utc.timestamp()), int(esc_end_utc.timestamp()))
                print(f"  ✓ ESC booked for Eszter")
                booked = True
            else:
                print(f"  Eszter not available — checking fallback agents")
        except requests.HTTPError as e:
            print(f"  ⚠ Error checking Eszter: {e} — trying fallback agents")

        if booked:
            continue

        # ── Step 2: Find available fallback agents ──
        available = []
        for name, agent_id in FALLBACK_AGENTS.items():
            try:
                activities = get_agent_schedule(agent_id, date)
                if agent_covers_esc_window(activities, esc_start_utc, esc_end_utc):
                    print(f"  ✓ {name} is available")
                    available.append((name, agent_id))
                else:
                    print(f"  ✗ {name} not available")
            except requests.HTTPError as e:
                print(f"  ⚠ Error checking {name}: {e}")

        if not available:
            print(f"  ⚠ SKIPPED — no agents available for ESC!")
            skipped.append({
                "date":   date.strftime("%a %d %b"),
                "reason": "No agents available",
            })
            continue

        # ── Step 3: Pick whoever did ESC least recently ──
        print(f"  Checking ESC history to pick fairest agent...")
        candidates = []
        for name, agent_id in available:
            last_esc = get_last_esc_date(agent_id, date)
            if last_esc:
                print(f"    {name}: last ESC was {last_esc.strftime('%d %b %Y')}")
            else:
                print(f"    {name}: no recent ESC history")
            candidates.append((name, agent_id, last_esc))

        # Sort: agents with no history first, then by oldest ESC date
        candidates.sort(key=lambda x: x[2] or datetime.min.replace(tzinfo=BUDAPEST))
        chosen_name, chosen_id, _ = candidates[0]

        print(f"  → Booking {chosen_name} (least recent ESC)")
        try:
            create_esc_event(chosen_id, int(esc_start_utc.timestamp()), int(esc_end_utc.timestamp()))
            print(f"  ✓ ESC booked for {chosen_name}")
        except requests.HTTPError as e:
            print(f"  ✗ ERROR booking {chosen_name}: {e} — {e.response.text}")
            skipped.append({
                "date":   date.strftime("%a %d %b"),
                "reason": f"API error: {e}",
            })

    # ── Summary ──
    print(f"\n{'=' * 60}")
    if skipped:
        print(f"\n⚠ {len(skipped)} slot(s) skipped — manual action needed:\n")
        for s in skipped:
            print(f"  • {s['date']} — {s['reason']}")
        print()
    else:
        print("\n✓ All ESC slots scheduled successfully!\n")

    print("Done! ✓")


if __name__ == "__main__":
    main()
