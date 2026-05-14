"""
Question Channel Scheduler
--------------------------
Run after schedule generation to fill gaps in agents' schedules with
Question Channel events, based on the 4-week rotation.

Usage:
    ASSEMBLED_API_KEY=xxx START_DATE=2026-06-03 END_DATE=2026-06-30 python question_channel_scheduler.py

Environment variables:
    ASSEMBLED_API_KEY  — required, Assembled API key (sk_live_...)
    START_DATE         — required, first Wednesday to schedule from (YYYY-MM-DD)
    END_DATE           — required, last date to schedule up to, inclusive (YYYY-MM-DD)
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

# Assembled schedule ID — update this if the schedule changes.
# Found in the URL when viewing the schedule in Assembled.
# To use the master schedule instead, set this to None.
SCHEDULE_ID = "ce63792c-57e1-41ac-85a5-9f09b230c791"

QUESTION_CHANNEL_TYPE_ID = "d421c903-4ac6-4c40-ae21-00b00c6a79c2"

# Time off type names — any activity matching these will cause the slot to be skipped.
# Add or remove names here if your Assembled account uses different labels.
TIME_OFF_TYPE_NAMES = {
    "Holiday",
    "Sick And Medical",
    "Time Off",
    "Cops Non-Working Days",
}

# Events that define shift boundaries — used to clamp slot windows to actual working hours
SHIFT_BOUNDARY_NAME = "Non-working Hours"
MIN_SLOT_MINUTES = 30  # Don't book if the available window is shorter than this

# Agent IDs
AGENTS = {
    "Tien":      "8ffbdc6b-8404-43c2-bd2c-da5577260e27",
    "Dora":      "a904049d-524a-45d0-9492-935be9091c59",
    "Henriett":  "1d9e4692-7388-47be-9df1-c9a7bcd1a6cf",
    "Jad":       "109bd604-1e51-4fe4-b653-0002bab43911",
    "Katalin":   "5bea70c6-04e4-41d3-9640-1fb53a4e4015",
    "Krisztina": "b8701026-bd7f-4a18-9856-dd67a9d480fa",
}

# Slot times in Budapest time (hour, minute)
AM_START   = (9, 0)
AM_END     = (13, 30)
DORA_START = (10, 0)
DORA_END   = (14, 30)
PM_START   = (13, 30)
PM_END     = (18, 0)

# 4-week rotation
# Each entry: (agent_name, slot_type)
# slot_type: "am", "dora_am", or "pm"
# Days: 0=Wed, 1=Thu, 2=Fri
ROTATION = {
    1: {  # Week 1
        0: [("Tien", "am"),      ("Jad", "pm")],        # Wed
        1: [("Dora", "dora_am"), ("Krisztina", "pm")],  # Thu
        2: [("Katalin", "am"),   ("Henriett", "pm")],   # Fri
    },
    2: {  # Week 2
        0: [("Tien", "am"),      ("Henriett", "pm")],   # Wed
        1: [("Dora", "dora_am"), ("Katalin", "pm")],    # Thu
        2: [("Henriett", "am"),  ("Krisztina", "pm")],  # Fri
    },
    3: {  # Week 3
        0: [("Tien", "am"),      ("Jad", "pm")],        # Wed
        1: [("Dora", "dora_am"), ("Henriett", "pm")],   # Thu
        2: [("Jad", "am"),       ("Katalin", "pm")],    # Fri
    },
    4: {  # Week 4
        0: [("Tien", "am"),      ("Krisztina", "pm")],  # Wed
        1: [("Dora", "dora_am"), ("Katalin", "pm")],    # Thu
        2: [("Krisztina", "am"), ("Jad", "pm")],        # Fri
    },
}

# Week 1 anchor date (must be a Wednesday)
WEEK_1_ANCHOR = datetime(2026, 6, 3, tzinfo=BUDAPEST)


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def get_week_number(week_start: datetime) -> int:
    """Return which week of the 4-week cycle this is (1-4)."""
    delta = (week_start.date() - WEEK_1_ANCHOR.date()).days
    weeks_since = delta // 7
    return (weeks_since % 4) + 1


def get_wednesdays_in_range(start: datetime, end: datetime):
    """Return all Wednesdays between start and end dates inclusive."""
    wednesdays = []
    # Find the first Wednesday on or after start
    days_until_wednesday = (2 - start.weekday()) % 7
    current = start + timedelta(days=days_until_wednesday)
    while current.date() <= end.date():
        wednesdays.append(current)
        current += timedelta(weeks=1)
    return wednesdays


def slot_times(slot_type: str, date: datetime):
    """Return (start_utc, end_utc) for a slot on a given date."""
    if slot_type == "dora_am":
        start_h, start_m = DORA_START
        end_h, end_m = DORA_END
    elif slot_type == "am":
        start_h, start_m = AM_START
        end_h, end_m = AM_END
    else:  # pm
        start_h, start_m = PM_START
        end_h, end_m = PM_END

    start_local = BUDAPEST.localize(datetime(date.year, date.month, date.day, start_h, start_m))
    end_local   = BUDAPEST.localize(datetime(date.year, date.month, date.day, end_h, end_m))
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def get_agent_schedule(agent_id: str, date: datetime):
    """
    Fetch all activities for an agent on a given date.
    Returns a list of activities with type_name attached.
    """
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
    }
    if SCHEDULE_ID:
        params["schedule_id"] = SCHEDULE_ID

    resp = requests.get(
        f"{BASE_URL}/v0/activities",
        auth=(API_KEY, ""),
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()

    # Build type_id -> type info lookup from the response
    activity_types = data.get("activity_types", {})
    type_info_lookup = {
        tid: {"name": t.get("name", ""), "productive": t.get("productive", False)}
        for tid, t in activity_types.items()
    }

    # Attach type_name and productive flag to each activity
    activities = list(data.get("activities", {}).values())
    for a in activities:
        info = type_info_lookup.get(a.get("type_id", ""), {})
        a["type_name"]  = info.get("name", "Unknown")
        a["productive"] = info.get("productive", False)

    return activities


def check_time_off(activities: list, slot_start_utc: datetime, slot_end_utc: datetime):
    """
    Check if any activity overlapping the slot window is a time off event.
    Returns the time off type name if found, None otherwise.
    """
    slot_start_ts = int(slot_start_utc.timestamp())
    slot_end_ts   = int(slot_end_utc.timestamp())

    for activity in activities:
        if activity["end_time"] <= slot_start_ts:
            continue
        if activity["start_time"] >= slot_end_ts:
            continue
        if activity.get("type_name") in TIME_OFF_TYPE_NAMES:
            return activity.get("type_name")

    return None


def find_gaps(activities: list, slot_start_utc: datetime, slot_end_utc: datetime):
    """
    Find all gaps within the slot window not covered by any existing event.
    Returns a list of (gap_start_ts, gap_end_ts) tuples as UTC unix timestamps.
    """
    slot_start_ts = int(slot_start_utc.timestamp())
    slot_end_ts   = int(slot_end_utc.timestamp())

    # Only consider non-productive events as blockers (breaks, lunch, focus time etc.)
    # Productive events (Chat, Email etc.) sit in a different layer and should be ignored —
    # Question Channel fills the gaps between default events, not around productive ones.
    overlapping = [
        a for a in activities
        if a["end_time"] > slot_start_ts
        and a["start_time"] < slot_end_ts
        and not a.get("productive", False)
        and a.get("type_name") not in TIME_OFF_TYPE_NAMES  # Don't treat time off as a gap blocker either
    ]
    overlapping.sort(key=lambda a: a["start_time"])

    gaps = []
    cursor = slot_start_ts

    for activity in overlapping:
        act_start = activity["start_time"]
        act_end   = activity["end_time"]
        if act_start > cursor:
            gaps.append((cursor, act_start))
        cursor = max(cursor, act_end)

    if cursor < slot_end_ts:
        gaps.append((cursor, slot_end_ts))

    # Drop tiny gaps under 1 minute
    return [(s, e) for s, e in gaps if e - s >= 60]


def clamp_slot_to_shift(activities: list, slot_start_utc: datetime, slot_end_utc: datetime):
    """
    Clamp the slot window to the agent's actual working hours for that day.

    Uses two signals:
    1. Non-working Hours events — explicit shift boundary blocks placed by Assembled
    2. First/last productive event — catches shifts where no NWH block exists at the boundary

    Returns (clamped_start_utc, clamped_end_utc) or None if available window is too short.
    """
    slot_start_ts = int(slot_start_utc.timestamp())
    slot_end_ts   = int(slot_end_utc.timestamp())

    effective_start = slot_start_ts
    effective_end   = slot_end_ts

    # Signal 1: Non-working Hours blocks
    nwh_events = [a for a in activities if a.get("type_name") == SHIFT_BOUNDARY_NAME]
    for nwh in nwh_events:
        nwh_start = nwh["start_time"]
        nwh_end   = nwh["end_time"]
        if nwh_end > slot_start_ts and nwh_start <= slot_start_ts:
            effective_start = max(effective_start, nwh_end)
        if nwh_start < slot_end_ts and nwh_end >= slot_end_ts:
            effective_end = min(effective_end, nwh_start)

    # Signal 2: First productive event start — catches shifts with no leading NWH block
    productive_events = [a for a in activities if a.get("productive")]
    if productive_events:
        first_productive_start = min(a["start_time"] for a in productive_events)
        last_productive_end    = max(a["end_time"]   for a in productive_events)
        # Don't schedule before the first productive event starts
        effective_start = max(effective_start, first_productive_start)
        # Don't schedule after the last productive event ends
        effective_end   = min(effective_end,   last_productive_end)

    available_mins = (effective_end - effective_start) // 60
    if available_mins < MIN_SLOT_MINUTES:
        return None

    return (
        datetime.fromtimestamp(effective_start, tz=timezone.utc),
        datetime.fromtimestamp(effective_end,   tz=timezone.utc),
    )


def create_event(agent_id: str, start_ts: int, end_ts: int):
    """Create a Question Channel event via the Assembled API."""
    payload = {
        "agent_id":   agent_id,
        "type_id":    QUESTION_CHANNEL_TYPE_ID,
        "start_time": start_ts,
        "end_time":   end_ts,
        # allow_conflicts defaults to False — we only fill gaps, never overlap
    }
    if SCHEDULE_ID:
        payload["schedule_id"] = SCHEDULE_ID

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
        print("ERROR: START_DATE and END_DATE environment variables are required (YYYY-MM-DD)")
        sys.exit(1)

    start_date = BUDAPEST.localize(datetime.strptime(start_str, "%Y-%m-%d"))
    end_date   = BUDAPEST.localize(datetime.strptime(end_str,   "%Y-%m-%d"))

    if start_date > end_date:
        print("ERROR: START_DATE must be before or equal to END_DATE")
        sys.exit(1)

    wednesdays = get_wednesdays_in_range(start_date, end_date)

    if not wednesdays:
        print(f"No Wednesdays found between {start_str} and {end_str}")
        sys.exit(0)

    schedule_label = f"schedule {SCHEDULE_ID}" if SCHEDULE_ID else "master schedule"
    print(f"Scheduling {len(wednesdays)} week(s) from {start_str} to {end_str}")
    print(f"Target: {schedule_label}")
    print("=" * 60)

    all_skipped = []

    for week_start in wednesdays:
        week_num = get_week_number(week_start)
        print(f"\n{'=' * 60}")
        print(f"Week of {week_start.strftime('%d %b %Y')} — Rotation week {week_num}")
        print("=" * 60)

        skipped = []
        rotation_week = ROTATION[week_num]

        # day_offset: 0=Wed, 1=Thu, 2=Fri
        for day_offset, slots in rotation_week.items():
            date = week_start + timedelta(days=day_offset)
            day_name = ["Wednesday", "Thursday", "Friday"][day_offset]

            # Skip if this day is outside our date range
            if date.date() > end_date.date():
                continue

            print(f"\n── {day_name} {date.strftime('%d %b %Y')} ──")

            for agent_name, slot_type in slots:
                agent_id = AGENTS[agent_name]
                slot_start_utc, slot_end_utc = slot_times(slot_type, date)
                slot_start_local = slot_start_utc.astimezone(BUDAPEST)
                slot_end_local   = slot_end_utc.astimezone(BUDAPEST)

                print(f"\n  {agent_name} | {slot_type.upper()} | "
                      f"{slot_start_local.strftime('%H:%M')}–{slot_end_local.strftime('%H:%M')} Budapest")

                # Fetch agent's schedule for the day
                try:
                    activities = get_agent_schedule(agent_id, date)
                except requests.HTTPError as e:
                    print(f"    ⚠ ERROR fetching schedule: {e}")
                    skipped.append({
                        "agent":  agent_name,
                        "date":   date.strftime("%a %d %b"),
                        "slot":   slot_type.upper(),
                        "reason": f"API error: {e}",
                    })
                    continue

                # Check if agent has any schedule at all — if the day is empty, skip
                has_any_schedule = any(
                    a.get("productive") or a.get("type_name") == SHIFT_BOUNDARY_NAME
                    for a in activities
                )
                if not has_any_schedule:
                    print(f"    ⏭ SKIPPED — {agent_name} has no schedule this day")
                    skipped.append({
                        "agent":  agent_name,
                        "date":   date.strftime("%a %d %b"),
                        "slot":   slot_type.upper(),
                        "reason": "No schedule this day",
                    })
                    continue

                # Check for time off — skip if found
                time_off_reason = check_time_off(activities, slot_start_utc, slot_end_utc)
                if time_off_reason:
                    print(f"    ⏭ SKIPPED — {agent_name} is on {time_off_reason}")
                    skipped.append({
                        "agent":  agent_name,
                        "date":   date.strftime("%a %d %b"),
                        "slot":   slot_type.upper(),
                        "reason": time_off_reason,
                    })
                    continue

                # Clamp slot to actual shift hours
                clamped = clamp_slot_to_shift(activities, slot_start_utc, slot_end_utc)
                if clamped is None:
                    print(f"    ⏭ SKIPPED — {agent_name} shift too short for this slot")
                    skipped.append({
                        "agent":  agent_name,
                        "date":   date.strftime("%a %d %b"),
                        "slot":   slot_type.upper(),
                        "reason": "Shift too short for slot window",
                    })
                    continue

                slot_start_utc, slot_end_utc = clamped
                slot_start_local = slot_start_utc.astimezone(BUDAPEST)
                slot_end_local   = slot_end_utc.astimezone(BUDAPEST)
                print(f"    ↳ Effective window: {slot_start_local.strftime('%H:%M')}–{slot_end_local.strftime('%H:%M')} Budapest")

                # Find and fill gaps
                gaps = find_gaps(activities, slot_start_utc, slot_end_utc)

                if not gaps:
                    print(f"    ✓ No gaps to fill — slot already fully covered")
                    continue

                for gap_start_ts, gap_end_ts in gaps:
                    gap_start_local = datetime.fromtimestamp(gap_start_ts, tz=BUDAPEST)
                    gap_end_local   = datetime.fromtimestamp(gap_end_ts,   tz=BUDAPEST)
                    duration_mins   = (gap_end_ts - gap_start_ts) // 60
                    print(f"    → Filling gap: {gap_start_local.strftime('%H:%M')}–"
                          f"{gap_end_local.strftime('%H:%M')} ({duration_mins} mins)")

                    try:
                        create_event(agent_id, gap_start_ts, gap_end_ts)
                        print(f"      ✓ Created")
                    except requests.HTTPError as e:
                        print(f"      ✗ ERROR: {e} — {e.response.text}")

        all_skipped.extend(skipped)

    # ── Final summary ──
    print(f"\n{'=' * 60}")
    print(f"SUMMARY — {len(wednesdays)} week(s) processed")
    if all_skipped:
        print(f"\n⚠ {len(all_skipped)} slot(s) skipped — manual cover needed:\n")
        for s in all_skipped:
            print(f"  • {s['date']} | {s['agent']} ({s['slot']}) — {s['reason']}")
        print()
    else:
        print("\n✓ All slots scheduled successfully — no skips!\n")

    print("Done! ✓")


if __name__ == "__main__":
    main()
