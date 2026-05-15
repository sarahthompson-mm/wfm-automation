"""
L3 Coach Monday/Tuesday Scheduler

For each Monday and Tuesday in the date range, this script:

1. LATE AGENT (from 9-week rotation):
   - Books Question Channel in any gaps in their schedule (their "base code")
   - Books ESC in the last 30 minutes of their shift (18:30–19:00 Budapest)

2. ALL-DAY ESC AGENT (picked by least recent, NOT the late agent):
   - Books ESC in any gaps in their schedule throughout the day

Usage:
    ASSEMBLED_API_KEY=sk_live_... python l3_coach_mon_tue_scheduler.py 2026-06-01 2026-06-30
    ASSEMBLED_API_KEY=sk_live_... python l3_coach_mon_tue_scheduler.py 2026-06-01 2026-06-30 --dry-run
"""

import os
import sys
import time
import requests
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

API_KEY     = os.environ.get("ASSEMBLED_API_KEY", "sk_live_YOUR_KEY_HERE")
BASE_URL    = "https://api.assembledhq.com/v0"
SCHEDULE_ID = "ce63792c-57e1-41ac-85a5-9f09b230c791"  # test schedule
BUDAPEST    = ZoneInfo("Europe/Budapest")

QC_EVENT_TYPE_ID  = "d421c903-4ac6-4c40-ae21-00b00c6a79c2"  # Question Channel
ESC_EVENT_TYPE_ID = "1a64d3a1-dff6-40c1-b223-3928417f6ffb"  # ESC

# Cycle anchor: Monday 2 June 2026 = Week 1
CYCLE_ANCHOR = date(2026, 6, 1)

# Agent roster (name → Assembled ID)
AGENTS = {
    "Barbara Tothova":  "bcc47bbc-a64e-4c5c-b9da-5ba3d9221fd3",
    "Barnabas Fono":    "fffd48ab-1abc-4264-b35e-7d1b72c002d5",
    "Csongor Zeitler":  "9bb19696-3974-4664-a66c-b5b7ecf93be2",
    "Dorina Barany":    "84c0949f-b286-4337-b684-c369535e8807",
    "Eszter Borsia":    "bf3086f1-ad27-4caf-b546-dc2501a6edac",
    "Gabo Bata":        "03b9c60c-55d9-43d9-91f0-b29c003a8aad",
    "Marton Onody":     "a862b950-5eaa-4135-bf0d-f5e2cdd90037",
    "Nora Nemeth":      "8e037202-8e52-4020-8d20-aef4794e4016",
    "Timea Rabb":       "f1e73f06-14ab-445f-96d3-416d2172b4b1",
}

AGENT_NAMES = list(AGENTS.keys())

# 9-week rotation: agent index → (mon_late_week, tue_late_week) (1-indexed)
# mon_late_week = (idx - 1) % 9 + 1
# tue_late_week = (idx - 2) % 9 + 1
def get_late_agent(week_num, day):
    """Return (name, agent_id) of the late agent for the given week and day ('mon'/'tue')."""
    for i, name in enumerate(AGENT_NAMES):
        if day == "mon" and (i - 1) % 9 == (week_num - 1) % 9:
            return name, AGENTS[name]
        if day == "tue" and (i - 2) % 9 == (week_num - 1) % 9:
            return name, AGENTS[name]

def get_cycle_week(d):
    """Return 1-indexed week number within the 9-week cycle for a given date."""
    days_since_anchor = (d - CYCLE_ANCHOR).days
    return (days_since_anchor // 7) % 9 + 1

# ---------------------------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------------------------

ASSEMBLED_AUTH = (API_KEY, "")

def headers():
    return {"Content-Type": "application/json"}

def get_agent_activities(agent_id, d):
    """Fetch all activities for an agent on a given date (returns list of activity dicts)."""
    start_ts = int(datetime(d.year, d.month, d.day, 0, 0, tzinfo=BUDAPEST).timestamp())
    end_ts   = int(datetime(d.year, d.month, d.day, 23, 59, tzinfo=BUDAPEST).timestamp())
    resp = requests.get(
        f"{BASE_URL}/activities",
        params={
            "agents": agent_id,
            "start_time": start_ts,
            "end_time": end_ts,
            "schedule_id": SCHEDULE_ID,
            "return_full_schedule": "true",
        },
        auth=ASSEMBLED_AUTH,
    )
    resp.raise_for_status()
    return list(resp.json().get("activities", {}).values())

def get_last_allday_esc_date(agent_id, before_date):
    """
    Look back up to 18 weeks to find the most recent date this agent did all-day ESC.
    Returns a date or None.
    """
    end_ts   = int(datetime(before_date.year, before_date.month, before_date.day, 0, 0, tzinfo=BUDAPEST).timestamp())
    start_ts = end_ts - (18 * 7 * 86400)
    resp = requests.get(
        f"{BASE_URL}/activities",
        params={
            "agents": agent_id,
            "start_time": start_ts,
            "end_time": end_ts,
            "schedule_id": SCHEDULE_ID,
            "return_full_schedule": "true",
        },
        auth=ASSEMBLED_AUTH,
    )
    resp.raise_for_status()
    activities = list(resp.json().get("activities", {}).values())
    esc_activities = [a for a in activities if a.get("event_type_id") == ESC_EVENT_TYPE_ID]
    if not esc_activities:
        return None
    latest_ts = max(a["start_time"] for a in esc_activities)
    return datetime.fromtimestamp(latest_ts, tz=BUDAPEST).date()

def create_activity(agent_id, event_type_id, start_ts, end_ts, dry_run=False):
    payload = {
        "agent_id":      agent_id,
        "event_type_id": event_type_id,
        "start_time":    start_ts,
        "end_time":      end_ts,
        "schedule_id":   SCHEDULE_ID,
    }
    if dry_run:
        start_local = datetime.fromtimestamp(start_ts, tz=BUDAPEST).strftime("%H:%M")
        end_local   = datetime.fromtimestamp(end_ts,   tz=BUDAPEST).strftime("%H:%M")
        label = "QC " if event_type_id == QC_EVENT_TYPE_ID else "ESC"
        print(f"      [DRY RUN] {label} {start_local}–{end_local} Budapest")
        return
    resp = requests.post(
        f"{BASE_URL}/activities",
        json=payload,
        auth=ASSEMBLED_AUTH,
    )
    resp.raise_for_status()
    time.sleep(0.25)

# ---------------------------------------------------------------------------
# SCHEDULING LOGIC
# ---------------------------------------------------------------------------

def get_gaps(activities, day_start_ts, day_end_ts, min_gap_mins=5):
    """
    Given a list of existing activities, return gaps (free slots) as list of (start_ts, end_ts).
    Clips to day_start_ts / day_end_ts.
    Only returns gaps >= min_gap_mins long.
    """
    # Sort by start time
    booked = sorted(
        [(a["start_time"], a["end_time"]) for a in activities],
        key=lambda x: x[0]
    )

    gaps = []
    cursor = day_start_ts

    for start, end in booked:
        if start > cursor + (min_gap_mins * 60):
            gaps.append((cursor, start))
        cursor = max(cursor, end)

    if cursor < day_end_ts - (min_gap_mins * 60):
        gaps.append((cursor, day_end_ts))

    return gaps

def get_shift_bounds(activities, d):
    """
    Return (shift_start_ts, shift_end_ts) based on the earliest and latest
    activity on the day. Falls back to 09:00-18:00 Budapest if no activities
    or if derived bounds look nonsensical (e.g. dodgy GCal events bleeding
    into midnight). Valid shift: start 06:00-14:00, end 14:00-23:00 Budapest.
    """
    default_start = datetime(d.year, d.month, d.day, 9, 0, tzinfo=BUDAPEST)
    default_end   = datetime(d.year, d.month, d.day, 18, 0, tzinfo=BUDAPEST)

    if not activities:
        return int(default_start.timestamp()), int(default_end.timestamp())

    # Check for holiday: any single event >= 20 hours = full day holiday, skip agent
    for a in activities:
        duration_hours = (a["end_time"] - a["start_time"]) / 3600
        if duration_hours >= 20:
            print(f"    Skipping — holiday detected (event spans {duration_hours:.0f}hrs)")
            return None

    all_starts = [a["start_time"] for a in activities]
    all_ends   = [a["end_time"]   for a in activities]
    shift_start_ts = min(all_starts)
    shift_end_ts   = max(all_ends)

    # Sanity check: start must be 06:00-14:00, end must be 14:00-23:00
    valid_start_min = int(datetime(d.year, d.month, d.day, 6,  0, tzinfo=BUDAPEST).timestamp())
    valid_start_max = int(datetime(d.year, d.month, d.day, 14, 0, tzinfo=BUDAPEST).timestamp())
    valid_end_min   = int(datetime(d.year, d.month, d.day, 14, 0, tzinfo=BUDAPEST).timestamp())
    valid_end_max   = int(datetime(d.year, d.month, d.day, 23, 0, tzinfo=BUDAPEST).timestamp())

    if not (valid_start_min <= shift_start_ts <= valid_start_max and
            valid_end_min   <= shift_end_ts   <= valid_end_max):
        print(f"    Warning: shift bounds look wrong "
              f"({datetime.fromtimestamp(shift_start_ts, tz=BUDAPEST).strftime('%H:%M')}-"
              f"{datetime.fromtimestamp(shift_end_ts, tz=BUDAPEST).strftime('%H:%M')}) "
              f"-- defaulting to 09:00-18:00 Budapest")
        return int(default_start.timestamp()), int(default_end.timestamp())

    return shift_start_ts, shift_end_ts

def schedule_gaps(agent_id, agent_name, event_type_id, activities, d, day_start_ts, day_end_ts, label, dry_run):
    """Fill gaps in the agent's schedule with the given event type."""
    gaps = get_gaps(activities, day_start_ts, day_end_ts)
    if not gaps:
        print(f"    No gaps found for {agent_name} — nothing to book")
        return
    for gap_start, gap_end in gaps:
        duration_mins = (gap_end - gap_start) // 60
        if duration_mins < 5:
            continue
        start_local = datetime.fromtimestamp(gap_start, tz=BUDAPEST).strftime("%H:%M")
        end_local   = datetime.fromtimestamp(gap_end,   tz=BUDAPEST).strftime("%H:%M")
        print(f"    → {label} gap {start_local}–{end_local} ({duration_mins} mins)")
        create_activity(agent_id, event_type_id, gap_start, gap_end, dry_run)

def schedule_end_of_shift_esc(agent_id, agent_name, activities, d, dry_run):
    """Book ESC in the last 30 minutes of the agent's shift."""
    _, shift_end_ts = get_shift_bounds(activities, d)
    esc_end_ts   = shift_end_ts
    esc_start_ts = esc_end_ts - (30 * 60)

    start_local = datetime.fromtimestamp(esc_start_ts, tz=BUDAPEST).strftime("%H:%M")
    end_local   = datetime.fromtimestamp(esc_end_ts,   tz=BUDAPEST).strftime("%H:%M")
    print(f"    → ESC (end of shift) {start_local}–{end_local} Budapest")
    create_activity(agent_id, ESC_EVENT_TYPE_ID, esc_start_ts, esc_end_ts, dry_run)

def pick_allday_esc_agent(exclude_name, d, dry_run):
    """
    Pick the agent (excluding the late agent) who did all-day ESC least recently.
    Returns (name, agent_id).
    """
    candidates = []
    for name, agent_id in AGENTS.items():
        if name == exclude_name:
            continue
        # Skip agents on holiday
        activities = get_agent_activities(agent_id, d)
        bounds = get_shift_bounds(activities, d)
        if bounds is None:
            print(f"      {name}: on holiday — skipping")
            continue
        if not dry_run:
            last = get_last_allday_esc_date(agent_id, d)
        else:
            last = None  # in dry run, treat all as equal
        candidates.append((name, agent_id, last))
        if last:
            print(f"      {name}: last all-day ESC {last.strftime('%d %b %Y')}")
        else:
            print(f"      {name}: no recent all-day ESC history")

    # Sort: no history first, then oldest first
    candidates.sort(key=lambda x: x[2] or date.min)
    chosen_name, chosen_id, _ = candidates[0]
    return chosen_name, chosen_id

# ---------------------------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------------------------

def get_mon_tue_in_range(start_date, end_date):
    """Return list of (date, 'mon'/'tue') tuples for all Mon/Tue in range."""
    results = []
    d = start_date
    while d <= end_date:
        if d.weekday() == 0:
            results.append((d, "mon"))
        elif d.weekday() == 1:
            results.append((d, "tue"))
        d += timedelta(days=1)
    return results

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    args    = [a for a in sys.argv[1:] if not a.startswith("--")]

    if len(args) < 2:
        print("Usage: python l3_coach_mon_tue_scheduler.py START_DATE END_DATE [--dry-run]")
        print("       Dates in DD/MM/YYYY format")
        sys.exit(1)

    start_date = datetime.strptime(args[0], "%d/%m/%Y").date()
    end_date   = datetime.strptime(args[1], "%d/%m/%Y").date()

    print("=" * 60)
    print(f"L3 Coach Mon/Tue Scheduler — {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Range: {start_date} → {end_date}")
    print(f"Schedule: {SCHEDULE_ID}")
    print("=" * 60)

    if not dry_run:
        confirm = input("\nType 'yes' to run live: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

    days = get_mon_tue_in_range(start_date, end_date)
    print(f"\nProcessing {len(days)} Mon/Tue day(s)...\n")

    for d, day_label in days:
        week_num = get_cycle_week(d)
        day_name = "Monday" if day_label == "mon" else "Tuesday"
        print(f"── {day_name} {d.strftime('%d %b %Y')} (cycle week {week_num}) ──")

        late_name, late_id = get_late_agent(week_num, day_label)
        print(f"  Late agent: {late_name}")

        # Fetch late agent's activities
        late_activities = get_agent_activities(late_id, d)

        # Shift bounds for late agent (expect 10:00–19:00 Budapest)
        shift_bounds = get_shift_bounds(late_activities, d)
        if shift_bounds is None:
            print(f"  ⚠ {late_name} is on holiday — skipping QC and end-of-shift ESC")
        else:
            shift_start_ts, shift_end_ts = shift_bounds
            shift_start_local = datetime.fromtimestamp(shift_start_ts, tz=BUDAPEST).strftime("%H:%M")
            shift_end_local   = datetime.fromtimestamp(shift_end_ts,   tz=BUDAPEST).strftime("%H:%M")
            print(f"  Shift: {shift_start_local}–{shift_end_local} Budapest")

            # ESC window = last 30 mins, so QC fills gaps up to 30 mins before shift end
            qc_end_ts = shift_end_ts - (30 * 60)

            # 1. Question Channel in gaps (between shift start and 30 mins before end)
            print(f"\n  [1] Question Channel gaps for {late_name}:")
            schedule_gaps(
                late_id, late_name, QC_EVENT_TYPE_ID,
                late_activities, d,
                shift_start_ts, qc_end_ts,
                "QC", dry_run
            )

            # 2. ESC at end of shift
            print(f"\n  [2] End-of-shift ESC for {late_name}:")
            schedule_end_of_shift_esc(late_id, late_name, late_activities, d, dry_run)

        # 3. All-day ESC agent (least recent, not the late agent)
        print(f"\n  [3] Picking all-day ESC agent (excluding {late_name}):")
        allday_name, allday_id = pick_allday_esc_agent(late_name, d, dry_run)
        print(f"  All-day ESC agent: {allday_name}")

        allday_activities = get_agent_activities(allday_id, d)
        allday_bounds = get_shift_bounds(allday_activities, d)

        print(f"\n  [3] ESC gaps for {allday_name}:")
        if allday_bounds is None:
            print(f"    ⚠ {allday_name} is on holiday — skipping all-day ESC")
        else:
            allday_start_ts, allday_end_ts = allday_bounds
            schedule_gaps(
                allday_id, allday_name, ESC_EVENT_TYPE_ID,
                allday_activities, d,
                allday_start_ts, allday_end_ts,
                "ESC", dry_run
            )

        print()

    print("=" * 60)
    print(f"{'DRY RUN complete — no changes made.' if dry_run else 'All done!'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
