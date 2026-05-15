"""
L3 Coach Mon/Tue/Sat Scheduler

For each Monday and Tuesday in the date range, this script:

1. LATE AGENT (from 9-week rotation):
   - Books Question Channel in any gaps in their schedule (their "base code")
   - Books ESC in the last 30 minutes of their shift (18:30–19:00 Budapest)

2. ALL-DAY ESC AGENT (picked by least recent, NOT the late agent):
   - Books ESC in any gaps in their schedule throughout the day

Usage:
    ASSEMBLED_API_KEY=sk_live_... python l3_coach_mon_tue_sat_scheduler.py 01/06/2026 30/06/2026
    ASSEMBLED_API_KEY=sk_live_... python l3_coach_mon_tue_sat_scheduler.py 01/06/2026 30/06/2026 --dry-run
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
ESC_EVENT_TYPE_ID  = "1a64d3a1-dff6-40c1-b223-3928417f6ffb"  # ESC
CHAT_CC_TYPE_ID    = "5bfe27ca-af9a-478b-83a9-26883519ce73"  # Chat - Customer Care (treated as gap)

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

def get_saturday_agent(week_num):
    """Return (name, agent_id) of the agent working Saturday in the given cycle week."""
    for i, name in enumerate(AGENT_NAMES):
        if i == (week_num - 1) % 9:  # sat_week = agent index (0-indexed)
            return name, AGENTS[name]

def get_saturday_shift_bounds(d):
    """Fixed Saturday shift: 09:00–18:00 Budapest."""
    return (
        int(datetime(d.year, d.month, d.day, 9, 0, tzinfo=BUDAPEST).timestamp()),
        int(datetime(d.year, d.month, d.day, 18, 0, tzinfo=BUDAPEST).timestamp()),
    )

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
    activities = list(resp.json().get("activities", {}).values())
    # Strip Chat - Customer Care events — treat as gaps for QC/ESC filling
    return [a for a in activities if a.get("type_id") != CHAT_CC_TYPE_ID]

def fetch_esc_history(start_date):
    """
    Fetch ESC history for all agents in one pass, looking back 18 weeks from start_date.
    Returns dict of {agent_name: last_esc_date or None}.
    """
    end_ts   = int(datetime(start_date.year, start_date.month, start_date.day, 0, 0, tzinfo=BUDAPEST).timestamp())
    start_ts = end_ts - (18 * 7 * 86400)
    history = {}
    for name, agent_id in AGENTS.items():
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
        esc_activities = [a for a in activities if a.get("type_id") == ESC_EVENT_TYPE_ID]
        if esc_activities:
            latest_ts = max(a["start_time"] for a in esc_activities)
            history[name] = datetime.fromtimestamp(latest_ts, tz=BUDAPEST).date()
        else:
            history[name] = None
        time.sleep(0.25)
    return history

def create_activity(agent_id, event_type_id, start_ts, end_ts, dry_run=False):
    payload = {
        "agent_id":    agent_id,
        "type_id":     event_type_id,
        "start_time":  start_ts,
        "end_time":    end_ts,
        "schedule_id": SCHEDULE_ID,
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

def is_on_holiday(activities):
    """Return True if any activity spans >= 20 hours (i.e. a full-day holiday event)."""
    for a in activities:
        duration_hours = (a["end_time"] - a["start_time"]) / 3600
        if duration_hours >= 20:
            print(f"    Skipping — holiday detected (event spans {duration_hours:.0f}hrs)")
            return True
    return False

def get_late_shift_bounds(d):
    """Fixed bounds for the late shift: 10:00–19:00 Budapest."""
    return (
        int(datetime(d.year, d.month, d.day, 10, 0, tzinfo=BUDAPEST).timestamp()),
        int(datetime(d.year, d.month, d.day, 19, 0, tzinfo=BUDAPEST).timestamp()),
    )

def get_standard_shift_bounds(d):
    """Fixed bounds for the standard shift: 09:00–18:00 Budapest."""
    return (
        int(datetime(d.year, d.month, d.day, 9, 0, tzinfo=BUDAPEST).timestamp()),
        int(datetime(d.year, d.month, d.day, 18, 0, tzinfo=BUDAPEST).timestamp()),
    )

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

def schedule_end_of_shift_esc(agent_id, agent_name, d, dry_run):
    """Book ESC in the last 30 minutes of the late shift (always 18:30–19:00 Budapest)."""
    _, shift_end_ts = get_late_shift_bounds(d)
    esc_end_ts   = shift_end_ts
    esc_start_ts = esc_end_ts - (30 * 60)

    start_local = datetime.fromtimestamp(esc_start_ts, tz=BUDAPEST).strftime("%H:%M")
    end_local   = datetime.fromtimestamp(esc_end_ts,   tz=BUDAPEST).strftime("%H:%M")
    print(f"    → ESC (end of shift) {start_local}–{end_local} Budapest")
    create_activity(agent_id, ESC_EVENT_TYPE_ID, esc_start_ts, esc_end_ts, dry_run)

def build_candidates(exclude_name, d, skip_names=None, esc_history=None, day_activities=None):
    """Build a sorted list of available candidates, excluding exclude_name and skip_names.
    Uses pre-fetched esc_history and day_activities to avoid repeated API calls."""
    skip_names    = skip_names or set()
    esc_history   = esc_history or {}
    day_activities = day_activities or {}
    candidates = []
    for name, agent_id in AGENTS.items():
        if name == exclude_name:
            continue
        if name in skip_names:
            print(f"      {name}: already done all-day ESC this run — skipping")
            continue
        activities = day_activities.get(name, [])
        if is_on_holiday(activities):
            print(f"      {name}: on holiday — skipping")
            continue
        last = esc_history.get(name)
        candidates.append((name, agent_id, last))
        if last:
            print(f"      {name}: last all-day ESC {last.strftime('%d %b %Y')}")
        else:
            print(f"      {name}: no recent all-day ESC history")
    candidates.sort(key=lambda x: x[2] or date.min)
    return candidates


def pick_allday_esc_agent(exclude_name, d, already_used=None, esc_history=None, day_activities=None):
    """
    Pick the agent who did all-day ESC least recently, excluding:
    - the late agent
    - anyone already used this Mon/Tue pair
    If everyone has been used this run, reset and pick from full pool.
    Returns (name, agent_id, already_used).
    """
    already_used = already_used or set()

    # First pass: exclude already used this run
    candidates = build_candidates(exclude_name, d, skip_names=already_used,
                                  esc_history=esc_history, day_activities=day_activities)

    if not candidates:
        # Everyone's been used — reset and try full pool (holidays still excluded)
        print(f"    ↺ All agents used this run — resetting and picking from full pool")
        already_used = set()
        candidates = build_candidates(exclude_name, d, skip_names=already_used,
                                      esc_history=esc_history, day_activities=day_activities)

    if not candidates:
        print(f"    ⚠ No available agents for all-day ESC — everyone on holiday!")
        return None, None, already_used

    chosen_name, chosen_id, _ = candidates[0]
    already_used.add(chosen_name)
    return chosen_name, chosen_id, already_used

# ---------------------------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------------------------

def get_mon_tue_sat_in_range(start_date, end_date):
    """Return list of (date, 'mon'/'tue'/'sat') tuples for all Mon/Tue/Sat in range."""
    results = []
    d = start_date
    while d <= end_date:
        if d.weekday() == 0:
            results.append((d, "mon"))
        elif d.weekday() == 1:
            results.append((d, "tue"))
        elif d.weekday() == 5:
            results.append((d, "sat"))
        d += timedelta(days=1)
    return results

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    args    = [a for a in sys.argv[1:] if not a.startswith("--")]

    if len(args) < 2:
        print("Usage: python l3_coach_mon_tue_sat_scheduler.py START_DATE END_DATE [--dry-run]")
        print("       Dates in DD/MM/YYYY format")
        sys.exit(1)

    start_date = datetime.strptime(args[0], "%d/%m/%Y").date()
    end_date   = datetime.strptime(args[1], "%d/%m/%Y").date()

    print("=" * 60)
    print(f"L3 Coach Mon/Tue/Sat Scheduler — {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Range: {start_date} → {end_date}")
    print(f"Schedule: {SCHEDULE_ID}")
    print("=" * 60)

    if not dry_run:
        confirm = input("\nType 'yes' to run live: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

    days = get_mon_tue_sat_in_range(start_date, end_date)
    print(f"\nProcessing {len(days)} Mon/Tue/Sat day(s)...")

    # Pre-fetch ESC history for all agents once upfront
    print("Fetching ESC history for all agents...")
    esc_history = fetch_esc_history(start_date)
    for name, last in esc_history.items():
        if last:
            print(f"  {name}: last ESC {last.strftime('%d %b %Y')}")
        else:
            print(f"  {name}: no recent ESC history")

    # Pre-fetch all agents' activities for each day upfront
    print("\nFetching schedules for all agents...")
    all_day_activities = {}  # {date: {agent_name: [activities]}}
    unique_dates = list(dict.fromkeys(d for d, _ in days))
    for d in unique_dates:
        all_day_activities[d] = {}
        for name, agent_id in AGENTS.items():
            all_day_activities[d][name] = get_agent_activities(agent_id, d)
        print(f"  Fetched {d.strftime('%d %b')}")

    print()
    already_used_allday_esc = set()  # resets each Monday, persists Mon→Tue

    for d, day_label in days:
        # Reset the used set each Monday so we get a fresh pair each week
        if day_label == "mon":
            already_used_allday_esc = set()
        week_num = get_cycle_week(d)
        day_name = {"mon": "Monday", "tue": "Tuesday", "sat": "Saturday"}[day_label]
        print(f"── {day_name} {d.strftime('%d %b %Y')} (cycle week {week_num}) ──")

        day_acts = all_day_activities[d]

        # ── SATURDAY ──────────────────────────────────────────────────────────
        if day_label == "sat":
            sat_name, sat_id = get_saturday_agent(week_num)
            print(f"  Saturday agent: {sat_name}")
            sat_activities = day_acts[sat_name]

            if is_on_holiday(sat_activities):
                print(f"  ⚠ {sat_name} is on holiday — skipping Saturday ESC")
            else:
                sat_start_ts, sat_end_ts = get_saturday_shift_bounds(d)
                print(f"  Shift: 09:00–18:00 Budapest (Saturday)")
                print(f"\n  [SAT] ESC gaps for {sat_name}:")
                schedule_gaps(
                    sat_id, sat_name, ESC_EVENT_TYPE_ID,
                    sat_activities, d,
                    sat_start_ts, sat_end_ts,
                    "ESC", dry_run
                )
            print()
            continue

        # ── MON / TUE ─────────────────────────────────────────────────────────
        late_name, late_id = get_late_agent(week_num, day_label)
        print(f"  Late agent: {late_name}")

        late_activities = day_acts[late_name]

        # Check for holiday
        if is_on_holiday(late_activities):
            print(f"  ⚠ {late_name} is on holiday — skipping QC and end-of-shift ESC")
        else:
            # Always use fixed late shift bounds: 10:00–19:00 Budapest
            shift_start_ts, shift_end_ts = get_late_shift_bounds(d)
            print(f"  Shift: 10:00–19:00 Budapest (late)")

            # QC fills gaps up to 30 mins before shift end
            qc_end_ts = shift_end_ts - (30 * 60)

            # 1. Question Channel in gaps
            print(f"\n  [1] Question Channel gaps for {late_name}:")
            schedule_gaps(
                late_id, late_name, QC_EVENT_TYPE_ID,
                late_activities, d,
                shift_start_ts, qc_end_ts,
                "QC", dry_run
            )

            # 2. ESC at end of shift
            print(f"\n  [2] End-of-shift ESC for {late_name}:")
            schedule_end_of_shift_esc(late_id, late_name, d, dry_run)

        # 3. All-day ESC agent (least recent, not the late agent)
        print(f"\n  [3] Picking all-day ESC agent (excluding {late_name}):")
        allday_name, allday_id, already_used_allday_esc = pick_allday_esc_agent(
            late_name, d,
            already_used=already_used_allday_esc,
            esc_history=esc_history,
            day_activities=day_acts,
        )
        if allday_name is None:
            print(f"  ⚠ No all-day ESC agent available today — skipping")
            print()
            continue
        print(f"  All-day ESC agent: {allday_name}")
        # Update in-memory history so the rest of this run knows this agent was just used
        esc_history[allday_name] = d

        allday_activities = day_acts[allday_name]

        # Always use fixed standard shift bounds: 09:00–18:00 Budapest
        allday_start_ts, allday_end_ts = get_standard_shift_bounds(d)

        print(f"\n  [3] ESC gaps for {allday_name} (09:00–18:00 Budapest):")
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
