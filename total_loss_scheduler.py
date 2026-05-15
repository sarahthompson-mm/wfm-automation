"""
Nonfault Total Loss Scheduler
------------------------------
Schedules 1-hour Nonfault Total Loss sessions for L2 agents on
Wednesdays and Fridays, avoiding:
  - The lunch period (11:30-14:30 Budapest)
  - Days when the agent is already on QC

Logic:
1. For each Wednesday and Friday in the date range
2. Find agents not on QC that day and who have a schedule
3. Pick the agent who did Total Loss least recently (fairness)
4. Try to avoid using the same agent on both Wed and Fri in the same week
5. Find a 1-hour gap outside 11:30-14:30 Budapest, filling around breaks if needed

Usage:
    ASSEMBLED_API_KEY=xxx START_DATE=03/06/2026 END_DATE=30/06/2026 python total_loss_scheduler.py

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

from rotation import AGENTS, BUDAPEST, get_qc_agents_on_day

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

API_KEY     = os.environ["ASSEMBLED_API_KEY"]
BASE_URL    = "https://api.assembledhq.com"
SCHEDULE_ID = "ce63792c-57e1-41ac-85a5-9f09b230c791"

TOTAL_LOSS_TYPE_ID = "2fd07f3b-9f90-42b3-bafb-ee34915031f6"
SLOT_DURATION_MINS = 60

# Lunch exclusion window (Budapest time)
LUNCH_START_H, LUNCH_START_M = 11, 30
LUNCH_END_H,   LUNCH_END_M   = 14, 30

# How far back to look for recent Total Loss history (days)
HISTORY_DAYS = 60

# All eligible agents (excluding Eszter handled separately)
ELIGIBLE_AGENTS = {k: v for k, v in AGENTS.items() if k != "Eszter"}
ESZTER_ID = AGENTS["Eszter"]

TIME_OFF_TYPE_NAMES = {
    "Holiday",
    "Sick And Medical",
    "Time Off",
    "Cops Non-Working Days",
}

SHIFT_BOUNDARY_NAME = "Non-working Hours"


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def get_wed_fri_in_range(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """
    Return pairs of (Wednesday, Friday) for each week in the range.
    Each pair represents one week's slots.
    """
    weeks = []
    current = start
    while current.date() <= end.date():
        if current.weekday() == 2:  # Wednesday
            friday = current + timedelta(days=2)
            weeks.append((current, friday if friday.date() <= end.date() else None))
            current += timedelta(weeks=1)
        else:
            current += timedelta(days=1)
    return weeks


def get_agent_schedule(agent_id: str, date: datetime) -> list:
    """Fetch all activities for an agent on a given date."""
    start_local = BUDAPEST.localize(datetime(date.year, date.month, date.day, 0, 0))
    end_local   = BUDAPEST.localize(datetime(date.year, date.month, date.day, 23, 59))

    params = {
        "agents":                 agent_id,
        "start_time":             int(start_local.astimezone(timezone.utc).timestamp()),
        "end_time":               int(end_local.astimezone(timezone.utc).timestamp()),
        "return_full_schedule":   "true",
        "include_activity_types": "true",
        "schedule_id":            SCHEDULE_ID,
    }

    resp = requests.get(f"{BASE_URL}/v0/activities", auth=(API_KEY, ""), params=params)
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


def agent_has_schedule(activities: list) -> bool:
    """Check if agent has any productive or shift boundary events (i.e. is working today)."""
    return any(
        a.get("productive") or a.get("type_name") == SHIFT_BOUNDARY_NAME
        for a in activities
    )


def agent_is_on_time_off(activities: list, date: datetime) -> bool:
    """Check if agent is on time off for the full day."""
    return any(a.get("type_name") in TIME_OFF_TYPE_NAMES for a in activities)


def get_last_total_loss_date(agent_id: str, before_date: datetime) -> datetime | None:
    """Look back HISTORY_DAYS to find when this agent last had a Total Loss event."""
    end_ts   = int(before_date.astimezone(timezone.utc).timestamp())
    start_ts = int((before_date - timedelta(days=HISTORY_DAYS)).astimezone(timezone.utc).timestamp())

    try:
        resp = requests.get(
            f"{BASE_URL}/v0/activities",
            auth=(API_KEY, ""),
            params={
                "agents":      agent_id,
                "start_time":  start_ts,
                "end_time":    end_ts,
                "schedule_id": SCHEDULE_ID,
            }
        )
        resp.raise_for_status()
        activities = list(resp.json().get("activities", {}).values())
        tl_events = [a for a in activities if a.get("type_id") == TOTAL_LOSS_TYPE_ID]
        if not tl_events:
            return None
        last_ts = max(a["start_time"] for a in tl_events)
        return datetime.fromtimestamp(last_ts, tz=BUDAPEST)
    except Exception:
        return None


def find_total_loss_slot(activities: list, date: datetime) -> tuple[int, int] | None:
    """
    Find a 1-hour gap in the agent's schedule on the given date,
    avoiding the lunch window (11:30-14:30 Budapest).

    Preference order:
    1. Morning slot (09:00-11:30 Budapest)
    2. Afternoon slot (14:30-19:00 Budapest)
    3. Any available gap outside lunch as a last resort

    Returns (start_ts, end_ts) or None if no gap found.
    """
    slot_secs = SLOT_DURATION_MINS * 60

    lunch_start = BUDAPEST.localize(datetime(date.year, date.month, date.day, LUNCH_START_H, LUNCH_START_M))
    lunch_end   = BUDAPEST.localize(datetime(date.year, date.month, date.day, LUNCH_END_H, LUNCH_END_M))
    lunch_start_ts = int(lunch_start.astimezone(timezone.utc).timestamp())
    lunch_end_ts   = int(lunch_end.astimezone(timezone.utc).timestamp())

    # Get shift boundaries from productive events
    productive = [a for a in activities if a.get("productive")]
    if not productive:
        return None

    shift_start_ts = min(a["start_time"] for a in productive)
    shift_end_ts   = max(a["end_time"]   for a in productive)

    # Build list of blocked windows (non-productive default events + lunch)
    blocked = []

    # Add lunch as a blocked window
    blocked.append((lunch_start_ts, lunch_end_ts))

    # Add existing default events (breaks, focus time, meetings etc)
    for a in activities:
        if not a.get("productive") and a.get("type_name") not in TIME_OFF_TYPE_NAMES and a.get("type_name") != SHIFT_BOUNDARY_NAME:
            blocked.append((a["start_time"], a["end_time"]))

    # Sort and merge blocked windows
    blocked.sort()
    merged = []
    for start, end in blocked:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append([start, end])

    # Define search windows (morning preferred, then afternoon)
    morning_start = BUDAPEST.localize(datetime(date.year, date.month, date.day, 9, 0))
    morning_end   = lunch_start
    afternoon_start = lunch_end
    afternoon_end   = BUDAPEST.localize(datetime(date.year, date.month, date.day, 19, 0))

    search_windows = [
        (max(int(morning_start.astimezone(timezone.utc).timestamp()), shift_start_ts),
         min(int(morning_end.astimezone(timezone.utc).timestamp()), shift_end_ts)),
        (max(int(afternoon_start.astimezone(timezone.utc).timestamp()), shift_start_ts),
         min(int(afternoon_end.astimezone(timezone.utc).timestamp()), shift_end_ts)),
    ]

    for window_start, window_end in search_windows:
        if window_end - window_start < slot_secs:
            continue

        # Walk through the window finding gaps
        cursor = window_start
        for block_start, block_end in merged:
            if block_start >= window_end:
                break
            if block_end <= window_start:
                continue

            gap = min(block_start, window_end) - cursor
            if gap >= slot_secs:
                return (cursor, cursor + slot_secs)

            cursor = max(cursor, block_end)

        # Check remaining time after last block
        remaining = window_end - cursor
        if remaining >= slot_secs:
            return (cursor, cursor + slot_secs)

    return None


def create_event(agent_id: str, start_ts: int, end_ts: int):
    """Create a Total Loss event via the Assembled API."""
    payload = {
        "agent_id":    agent_id,
        "type_id":     TOTAL_LOSS_TYPE_ID,
        "start_time":  start_ts,
        "end_time":    end_ts,
        "schedule_id": SCHEDULE_ID,
    }
    resp = requests.post(f"{BASE_URL}/v0/activities", auth=(API_KEY, ""), json=payload)
    resp.raise_for_status()
    return resp.json()


def pick_agent(date: datetime, exclude_names: list[str]) -> tuple[str, str] | None:
    """
    Pick the best available agent for a Total Loss slot on the given date.
    Excludes agents on QC that day and any names in exclude_names.
    Returns (agent_name, agent_id) or None if nobody available.
    """
    qc_agents = get_qc_agents_on_day(date)
    excluded  = set(qc_agents + exclude_names)

    # Build candidate list
    candidates = []

    # Check Eszter first
    if "Eszter" not in excluded:
        try:
            activities = get_agent_schedule(ESZTER_ID, date)
            if agent_has_schedule(activities) and not agent_is_on_time_off(activities, date):
                slot = find_total_loss_slot(activities, date)
                if slot:
                    last_tl = get_last_total_loss_date(ESZTER_ID, date)
                    candidates.append(("Eszter", ESZTER_ID, last_tl, slot))
                    print(f"    Eszter: available ✅")
                else:
                    print(f"    Eszter: no suitable gap ❌")
            else:
                print(f"    Eszter: no schedule/time off ❌")
        except Exception as e:
            print(f"    Eszter: error ({e}) ❌")

    # Check other agents
    for name, agent_id in ELIGIBLE_AGENTS.items():
        if name in excluded:
            print(f"    {name}: on QC or excluded ⏭")
            continue
        try:
            activities = get_agent_schedule(agent_id, date)
            if not agent_has_schedule(activities):
                print(f"    {name}: no schedule ❌")
                continue
            if agent_is_on_time_off(activities, date):
                print(f"    {name}: on time off ❌")
                continue
            slot = find_total_loss_slot(activities, date)
            if slot:
                last_tl = get_last_total_loss_date(agent_id, date)
                candidates.append((name, agent_id, last_tl, slot))
                print(f"    {name}: available ✅")
            else:
                print(f"    {name}: no suitable gap ❌")
        except Exception as e:
            print(f"    {name}: error ({e}) ❌")

    if not candidates:
        return None

    # Sort by last Total Loss date (oldest first, never = highest priority)
    candidates.sort(key=lambda x: x[2] or datetime.min.replace(tzinfo=BUDAPEST))

    chosen_name, chosen_id, last_tl, slot = candidates[0]
    if last_tl:
        print(f"  → Picked {chosen_name} (last Total Loss: {last_tl.strftime('%d %b %Y')})")
    else:
        print(f"  → Picked {chosen_name} (no recent Total Loss history)")

    return chosen_name, chosen_id, slot


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

    weeks = get_wed_fri_in_range(start_date, end_date)

    print(f"Scheduling Total Loss for {len(weeks)} week(s) from {start_str} to {end_str}")
    print(f"Target: schedule {SCHEDULE_ID}")
    print("=" * 60)

    skipped = []

    for wednesday, friday in weeks:
        print(f"\n{'=' * 60}")
        print(f"Week of {wednesday.strftime('%d %b %Y')}")
        print("=" * 60)

        used_this_week = []

        for date in [wednesday, friday]:
            if date is None:
                continue

            day_name = "Wednesday" if date.weekday() == 2 else "Friday"
            print(f"\n── {day_name} {date.strftime('%d %b %Y')} ──")
            print(f"  Checking availability (excluding QC agents: {', '.join(get_qc_agents_on_day(date)) or 'none'})...")

            result = pick_agent(date, exclude_names=used_this_week)

            if result is None:
                print(f"  ⚠ SKIPPED — no agents available!")
                skipped.append({
                    "date":   date.strftime("%a %d %b"),
                    "reason": "No agents available",
                })
                continue

            chosen_name, chosen_id, (slot_start_ts, slot_end_ts) = result
            slot_start_local = datetime.fromtimestamp(slot_start_ts, tz=BUDAPEST)
            slot_end_local   = datetime.fromtimestamp(slot_end_ts,   tz=BUDAPEST)

            print(f"  → Booking {chosen_name}: {slot_start_local.strftime('%H:%M')}–{slot_end_local.strftime('%H:%M')} Budapest")

            try:
                create_event(chosen_id, slot_start_ts, slot_end_ts)
                print(f"  ✓ Total Loss booked for {chosen_name}")
                used_this_week.append(chosen_name)
            except requests.HTTPError as e:
                print(f"  ✗ ERROR: {e} — {e.response.text}")
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
        print("\n✓ All Total Loss slots scheduled successfully!\n")

    print("Done! ✓")


if __name__ == "__main__":
    main()
