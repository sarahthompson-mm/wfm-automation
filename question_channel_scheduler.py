"""
Question Channel Scheduler
--------------------------
Run after schedule generation to fill gaps in agents' schedules with
Question Channel events, based on the 4-week rotation.

Usage:
    ASSEMBLED_API_KEY=xxx WEEK_START_DATE=2026-06-03 python question_channel_scheduler.py

The WEEK_START_DATE must be a Wednesday (the start of the Question Channel week).

Environment variables:
    ASSEMBLED_API_KEY  — required, Assembled API key (sk_live_...)
    WEEK_START_DATE    — required, the Wednesday to schedule for (YYYY-MM-DD)
"""

import os
import sys
import requests
from datetime import datetime, timedelta, timezone
import pytz

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

API_KEY  = os.environ["ASSEMBLED_API_KEY"]
BASE_URL = "https://api.assembledhq.com"
BUDAPEST = pytz.timezone("Europe/Budapest")

QUESTION_CHANNEL_TYPE_ID = "d421c903-4ac6-4c40-ae21-00b00c6a79c2"

# Time off type names — any activity matching these will cause the slot to be skipped.
# Add or remove names here if your Assembled account uses different labels.
TIME_OFF_TYPE_NAMES = {
    "Holiday",
    "Sick And Medical",
    "Time Off",
    "Cops Non-Working Days",
    "Non-working Hours",
}

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
        0: [("Tien", "am"),      ("Henriett", "pm")],   # Wed
        1: [("Dora", "dora_am"), ("Henriett", "pm")],   # Thu
        2: [("Katalin", "am"),   ("Jad", "pm")],        # Fri
    },
    2: {  # Week 2
        0: [("Tien", "am"),      ("Jad", "pm")],        # Wed
        1: [("Dora", "dora_am"), ("Jad", "pm")],        # Thu
        2: [("Henriett", "am"),  ("Krisztina", "pm")],  # Fri
    },
    3: {  # Week 3
        0: [("Tien", "am"),      ("Katalin", "pm")],    # Wed
        1: [("Dora", "dora_am"), ("Katalin", "pm")],    # Thu
        2: [("Krisztina", "am"), ("Henriett", "pm")],   # Fri
    },
    4: {  # Week 4
        0: [("Tien", "am"),      ("Krisztina", "pm")],  # Wed
        1: [("Dora", "dora_am"), ("Henriett", "pm")],   # Thu
        2: [("Jad", "am"),       ("Katalin", "pm")],    # Fri
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

    resp = requests.get(
        f"{BASE_URL}/v0/activities",
        auth=(API_KEY, ""),
        params={
            "agents":                 agent_id,
            "start_time":             start_ts,
            "end_time":               end_ts,
            "return_full_schedule":   "true",  # All layers, not just flattened
            "include_activity_types": "true",  # So we can detect time off by name
        }
    )
    resp.raise_for_status()
    data = resp.json()

    # Build type_id -> type_name lookup from the response
    activity_types = data.get("activity_types", {})
    type_name_lookup = {tid: t.get("name", "") for tid, t in activity_types.items()}

    # Attach type_name to each activity for easy checking
    activities = list(data.get("activities", {}).values())
    for a in activities:
        a["type_name"] = type_name_lookup.get(a.get("type_id", ""), "Unknown")

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
    Find all gaps (empty time) within the slot window not covered by any existing event.
    Returns a list of (gap_start_ts, gap_end_ts) tuples as UTC unix timestamps.
    """
    slot_start_ts = int(slot_start_utc.timestamp())
    slot_end_ts   = int(slot_end_utc.timestamp())

    # Get activities that overlap the slot window
    overlapping = [
        a for a in activities
        if a["end_time"] > slot_start_ts and a["start_time"] < slot_end_ts
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

    # Any remaining time after the last activity
    if cursor < slot_end_ts:
        gaps.append((cursor, slot_end_ts))

    # Drop tiny gaps under 1 minute (rounding artefacts)
    return [(s, e) for s, e in gaps if e - s >= 60]


def create_event(agent_id: str, start_ts: int, end_ts: int):
    """Create a Question Channel event via the Assembled API."""
    payload = {
        "agent_id":   agent_id,
        "type_id":    QUESTION_CHANNEL_TYPE_ID,
        "start_time": start_ts,
        "end_time":   end_ts,
        # allow_conflicts defaults to False — we only fill gaps, never overlap
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
    week_start_str = os.environ.get("WEEK_START_DATE")
    if not week_start_str:
        print("ERROR: WEEK_START_DATE environment variable is required (YYYY-MM-DD)")
        sys.exit(1)

    week_start = BUDAPEST.localize(datetime.strptime(week_start_str, "%Y-%m-%d"))

    if week_start.weekday() != 2:  # 2 = Wednesday
        print(f"ERROR: {week_start_str} is not a Wednesday!")
        sys.exit(1)

    week_num = get_week_number(week_start)
    print(f"Processing week starting {week_start_str} (Week {week_num} of 4-week cycle)")
    print("=" * 60)

    skipped = []  # Track skipped slots for summary

    rotation_week = ROTATION[week_num]

    # day_offset: 0=Wed, 1=Thu, 2=Fri
    for day_offset, slots in rotation_week.items():
        date = week_start + timedelta(days=day_offset)
        day_name = ["Wednesday", "Thursday", "Friday"][day_offset]
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
                    "reason": f"API error fetching schedule: {e}",
                })
                continue

            # Check if agent is on time off — skip if so
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

            # Find gaps in the slot window and fill them
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
                    print(f"      ✗ ERROR creating event: {e} — {e.response.text}")

    # ── Summary ──
    print("\n" + "=" * 60)
    if skipped:
        print(f"\n⚠ {len(skipped)} slot(s) skipped — manual cover needed:\n")
        for s in skipped:
            print(f"  • {s['date']} | {s['agent']} ({s['slot']}) — {s['reason']}")
        print()
    else:
        print("\n✓ All slots scheduled successfully — no skips!\n")

    print("Done! ✓")


if __name__ == "__main__":
    main()
