"""
L3 Coach Wed/Thu/Fri ESC Scheduler

For each Wednesday, Thursday and Friday in the date range, this script books
two agents with ESC sessions:

Agent A: 45 mins at start of shift + 2 x 45 mins spread through the day (135 mins total)
Agent B: 3 x 45 mins spread through the day (135 mins total)

Agent selection priority:
1. Not on the late shift Mon or Tue that week
2. Didn't have QC or all-day ESC on Mon/Tue that week (soft exclusion)
3. Least recent ESC history

Lunch events (3e211169-...) are respected and not overbooked.
GCal events are ignored — ESC is booked freely around non-GCal activities.

Usage:
    ASSEMBLED_API_KEY=sk_live_... python l3_coach_wed_thu_fri_scheduler.py 01/06/2026 30/06/2026
    ASSEMBLED_API_KEY=sk_live_... python l3_coach_wed_thu_fri_scheduler.py 01/06/2026 30/06/2026 --dry-run
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
SCHEDULE_ID = "ce63792c-57e1-41ac-85a5-9f09b230c791"
BUDAPEST    = ZoneInfo("Europe/Budapest")

ESC_EVENT_TYPE_ID  = "1a64d3a1-dff6-40c1-b223-3928417f6ffb"
QC_EVENT_TYPE_ID   = "d421c903-4ac6-4c40-ae21-00b00c6a79c2"
CHAT_CC_TYPE_ID    = "5bfe27ca-af9a-478b-83a9-26883519ce73"
LUNCH_TYPE_ID      = "3e211169-8456-4dc0-a824-bc1c6b1f24e0"

# Cycle anchor: Monday 1 June 2026 = Week 1
CYCLE_ANCHOR = date(2026, 6, 1)

ASSEMBLED_AUTH = (API_KEY, "")

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

ESC_SLOT_MINS = 45
STANDARD_START_HOUR = 9
STANDARD_END_HOUR   = 18

# ---------------------------------------------------------------------------
# ROTATION HELPERS
# ---------------------------------------------------------------------------

def get_cycle_week(d):
    """Return 1-indexed week number within the 9-week cycle."""
    days_since_anchor = (d - CYCLE_ANCHOR).days
    return (days_since_anchor // 7) % 9 + 1

def get_late_agents_for_week(week_num):
    """Return (mon_late_name, tue_late_name) for the given cycle week."""
    mon_late = next(n for i, n in enumerate(AGENT_NAMES) if (i - 1) % 9 == (week_num - 1) % 9)
    tue_late = next(n for i, n in enumerate(AGENT_NAMES) if (i - 2) % 9 == (week_num - 1) % 9)
    return mon_late, tue_late

# ---------------------------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------------------------

def get_agent_activities(agent_id, d):
    """Fetch all activities for an agent on a given date."""
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

def fetch_esc_history(start_date):
    """Fetch most recent ESC date for all agents, looking back 18 weeks."""
    end_ts   = int(datetime(start_date.year, start_date.month, start_date.day, 0, 0, tzinfo=BUDAPEST).timestamp())
    start_ts = end_ts - (18 * 7 * 86400)
    history  = {}
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
        esc = [a for a in activities if a.get("type_id") == ESC_EVENT_TYPE_ID]
        history[name] = datetime.fromtimestamp(max(a["start_time"] for a in esc), tz=BUDAPEST).date() if esc else None
        time.sleep(0.25)
    return history

def fetch_mon_tue_booked(start_date, end_date):
    """
    For each week in range, find who had QC or all-day ESC on Mon/Tue.
    Returns dict of {week_start_date: set of agent names}.
    """
    booked = {}
    d = start_date
    while d <= end_date:
        if d.weekday() == 0:  # Monday
            week_booked = set()
            for offset in [0, 1]:  # Mon, Tue
                day = d + timedelta(days=offset)
                for name, agent_id in AGENTS.items():
                    acts = get_agent_activities(agent_id, day)
                    for a in acts:
                        if a.get("type_id") in (ESC_EVENT_TYPE_ID, QC_EVENT_TYPE_ID):
                            week_booked.add(name)
                            break
                    time.sleep(0.1)
            booked[d] = week_booked
        d += timedelta(days=1)
    return booked

def create_activity(agent_id, start_ts, end_ts, dry_run=False):
    """Create an ESC activity."""
    if dry_run:
        s = datetime.fromtimestamp(start_ts, tz=BUDAPEST).strftime("%H:%M")
        e = datetime.fromtimestamp(end_ts,   tz=BUDAPEST).strftime("%H:%M")
        print(f"      [DRY RUN] ESC {s}–{e} Budapest")
        return
    resp = requests.post(
        f"{BASE_URL}/activities",
        json={
            "agent_id":    agent_id,
            "type_id":     ESC_EVENT_TYPE_ID,
            "start_time":  start_ts,
            "end_time":    end_ts,
            "schedule_id": SCHEDULE_ID,
        },
        auth=ASSEMBLED_AUTH,
    )
    resp.raise_for_status()
    time.sleep(0.25)

# ---------------------------------------------------------------------------
# HOLIDAY / SHIFT HELPERS
# ---------------------------------------------------------------------------

def is_on_holiday(activities):
    """Return True if any activity spans >= 20 hours."""
    for a in activities:
        if (a["end_time"] - a["start_time"]) / 3600 >= 20:
            return True
    return False

def get_standard_shift_bounds(d):
    """Fixed standard shift: 09:00–18:00 Budapest."""
    return (
        int(datetime(d.year, d.month, d.day, STANDARD_START_HOUR, 0, tzinfo=BUDAPEST).timestamp()),
        int(datetime(d.year, d.month, d.day, STANDARD_END_HOUR,   0, tzinfo=BUDAPEST).timestamp()),
    )

# ---------------------------------------------------------------------------
# SCHEDULING LOGIC
# ---------------------------------------------------------------------------

def get_lunch_blocks(activities):
    """Return list of (start_ts, end_ts) for lunch events."""
    return [(a["start_time"], a["end_time"]) for a in activities if a.get("type_id") == LUNCH_TYPE_ID]

def is_gcal_event(activity):
    """
    Heuristic: GCal events tend to have no type_id or a type_id we don't recognise
    as a core Assembled type. We treat Chat-CC, ESC, QC, Lunch as known types.
    Everything else is treated as potentially GCal and ignored when finding free
    slots (we book over them).
    """
    known_types = {ESC_EVENT_TYPE_ID, QC_EVENT_TYPE_ID, CHAT_CC_TYPE_ID, LUNCH_TYPE_ID}
    return activity.get("type_id") not in known_types

def find_free_slots(activities, shift_start_ts, shift_end_ts, slot_mins, exclude_start_mins=None):
    """
    Find free slots of exactly slot_mins within the shift window.
    Respects lunch events (won't overlap them).
    Ignores GCal/unknown events (books over them).
    exclude_start_mins: if set, skip the first N minutes of the shift
                        (used to leave room for the start-of-shift slot).
    Returns list of (start_ts, end_ts) candidates.
    """
    slot_secs    = slot_mins * 60
    lunch_blocks = get_lunch_blocks(activities)

    # Build blocked ranges from lunch only
    blocked = sorted(lunch_blocks, key=lambda x: x[0])

    # Find free windows avoiding lunch
    free_windows = []
    cursor = shift_start_ts
    if exclude_start_mins:
        cursor = shift_start_ts + (exclude_start_mins * 60)

    for b_start, b_end in blocked:
        if b_start > cursor:
            free_windows.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if cursor < shift_end_ts:
        free_windows.append((cursor, shift_end_ts))

    # Find valid slots within free windows
    slots = []
    for w_start, w_end in free_windows:
        t = w_start
        while t + slot_secs <= w_end:
            slots.append((t, t + slot_secs))
            t += slot_secs  # non-overlapping candidates

    return slots

def spread_slots(all_slots, n, shift_start_ts, shift_end_ts):
    """
    Pick n slots spread as evenly as possible across the shift window.
    """
    if not all_slots or len(all_slots) < n:
        return all_slots[:n] if all_slots else []

    # Divide shift into n equal segments, pick first available slot in each
    segment = (shift_end_ts - shift_start_ts) // n
    chosen  = []
    for i in range(n):
        seg_start = shift_start_ts + i * segment
        seg_end   = shift_start_ts + (i + 1) * segment
        # Find first slot that starts within this segment
        for s, e in all_slots:
            if seg_start <= s < seg_end and (s, e) not in chosen:
                chosen.append((s, e))
                break
    # If we couldn't fill all segments, pad with any remaining slots
    if len(chosen) < n:
        for s, e in all_slots:
            if (s, e) not in chosen:
                chosen.append((s, e))
            if len(chosen) == n:
                break
    return chosen[:n]

def book_agent_a(agent_id, agent_name, activities, d, dry_run):
    """
    Agent A pattern: 45 mins at start of shift + 2 x 45 mins spread through rest of day.
    The 2 remaining slots are targeted at the middle and last third of the shift.
    Falls back to next available if no slot found in the preferred segment.
    """
    shift_start_ts, shift_end_ts = get_standard_shift_bounds(d)
    slot_secs = ESC_SLOT_MINS * 60

    # Slot 1: first 45 mins of shift
    slot1_start = shift_start_ts
    slot1_end   = shift_start_ts + slot_secs
    print(f"    → ESC slot 1 (start of shift): {datetime.fromtimestamp(slot1_start, tz=BUDAPEST).strftime('%H:%M')}–{datetime.fromtimestamp(slot1_end, tz=BUDAPEST).strftime('%H:%M')} Budapest")
    create_activity(agent_id, slot1_start, slot1_end, dry_run)

    # Remaining window: after first slot to end of shift
    remaining_start = shift_start_ts + slot_secs
    remaining_secs  = shift_end_ts - remaining_start
    third           = remaining_secs // 3

    # Target: slot 2 in middle third, slot 3 in final third
    seg2_start = remaining_start + third
    seg2_end   = remaining_start + (2 * third)
    seg3_start = remaining_start + (2 * third)
    seg3_end   = shift_end_ts

    all_remaining = find_free_slots(activities, shift_start_ts, shift_end_ts,
                                    ESC_SLOT_MINS, exclude_start_mins=ESC_SLOT_MINS)

    def pick_from_segment(seg_s, seg_e, fallback_slots):
        """Pick first slot in segment, or fall back to any remaining slot."""
        for s, e in fallback_slots:
            if seg_s <= s < seg_e:
                return (s, e)
        # Fallback: first available slot not already chosen
        return fallback_slots[0] if fallback_slots else None

    chosen = []
    remaining_pool = list(all_remaining)

    slot2 = pick_from_segment(seg2_start, seg2_end, remaining_pool)
    if slot2:
        chosen.append(slot2)
        remaining_pool = [(s, e) for s, e in remaining_pool if s != slot2[0]]

    slot3 = pick_from_segment(seg3_start, seg3_end, remaining_pool)
    if slot3:
        chosen.append(slot3)

    if len(chosen) < 2:
        print(f"    ⚠ Could only find {len(chosen)} remaining slot(s) for {agent_name} — booking what we can")

    for i, (s, e) in enumerate(chosen, 2):
        print(f"    → ESC slot {i}: {datetime.fromtimestamp(s, tz=BUDAPEST).strftime('%H:%M')}–{datetime.fromtimestamp(e, tz=BUDAPEST).strftime('%H:%M')} Budapest")
        create_activity(agent_id, s, e, dry_run)

def book_agent_b(agent_id, agent_name, activities, d, dry_run):
    """
    Agent B pattern: 3 x 45 mins spread through the day, avoiding lunch.
    """
    shift_start_ts, shift_end_ts = get_standard_shift_bounds(d)

    all_slots = find_free_slots(activities, shift_start_ts, shift_end_ts, ESC_SLOT_MINS)
    chosen    = spread_slots(all_slots, 3, shift_start_ts, shift_end_ts)

    if len(chosen) < 3:
        print(f"    ⚠ Could only find {len(chosen)} slot(s) for {agent_name} — booking what we can")

    for i, (s, e) in enumerate(chosen, 1):
        print(f"    → ESC slot {i}: {datetime.fromtimestamp(s, tz=BUDAPEST).strftime('%H:%M')}–{datetime.fromtimestamp(e, tz=BUDAPEST).strftime('%H:%M')} Budapest")
        create_activity(agent_id, s, e, dry_run)

# ---------------------------------------------------------------------------
# AGENT PICKER
# ---------------------------------------------------------------------------

def pick_two_agents(d, week_mon, activities_by_name, esc_history,
                    mon_tue_booked, already_used, week_num):
    """
    Pick two agents for the day. Returns (agent_a_name, agent_b_name, already_used).
    Priority:
    1. Not on late shift this week
    2. Not booked for QC/ESC on Mon/Tue this week (soft)
    3. Least recent ESC history
    """
    mon_late, tue_late = get_late_agents_for_week(week_num)
    late_agents  = {mon_late, tue_late}
    mon_tue_busy = mon_tue_booked.get(week_mon, set())

    def score_agent(name):
        """Lower = higher priority."""
        on_late    = name in late_agents
        busy_mon_tue = name in mon_tue_busy
        last_esc   = esc_history.get(name)
        # Convert last_esc to a comparable value (older = lower = higher priority)
        esc_score  = last_esc.toordinal() if last_esc else 0
        return (on_late, busy_mon_tue, esc_score)

    available = []
    for name, agent_id in AGENTS.items():
        acts = activities_by_name.get(name, [])
        if is_on_holiday(acts):
            print(f"    {name}: on holiday — skipping")
            continue
        if name in already_used:
            print(f"    {name}: already used this week — skipping")
            continue
        last = esc_history.get(name)
        on_late = name in late_agents
        busy    = name in mon_tue_busy
        print(f"    {name}: last ESC {last.strftime('%d %b %Y') if last else 'never'}"
              f"{' [late Mon/Tue]' if on_late else ''}"
              f"{' [busy Mon/Tue]' if busy else ''}")
        available.append((name, agent_id))

    available.sort(key=lambda x: score_agent(x[0]))

    if len(available) < 2:
        print(f"    ⚠ Not enough agents available — trying without Mon/Tue exclusions")
        # Retry without the already_used filter
        available = []
        for name, agent_id in AGENTS.items():
            acts = activities_by_name.get(name, [])
            if is_on_holiday(acts):
                continue
            available.append((name, agent_id))
        available.sort(key=lambda x: score_agent(x[0]))

    if len(available) < 2:
        print(f"    ⚠ Could not find 2 agents — skipping day")
        return None, None, already_used

    agent_a_name, agent_a_id = available[0]
    agent_b_name, agent_b_id = available[1]

    already_used.add(agent_a_name)
    already_used.add(agent_b_name)

    # Update in-memory history
    esc_history[agent_a_name] = d
    esc_history[agent_b_name] = d

    return (agent_a_name, agent_a_id), (agent_b_name, agent_b_id), already_used

# ---------------------------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------------------------

def get_wed_thu_fri_in_range(start_date, end_date):
    """Return list of (date, day_name) for all Wed/Thu/Fri in range."""
    results = []
    d = start_date
    while d <= end_date:
        if d.weekday() in (2, 3, 4):
            results.append((d, {2: "Wednesday", 3: "Thursday", 4: "Friday"}[d.weekday()]))
        d += timedelta(days=1)
    return results

def get_week_monday(d):
    """Return the Monday of the week containing d."""
    return d - timedelta(days=d.weekday())

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    args    = [a for a in sys.argv[1:] if not a.startswith("--")]

    if len(args) < 2:
        print("Usage: python l3_coach_wed_thu_fri_scheduler.py START_DATE END_DATE [--dry-run]")
        print("       Dates in DD/MM/YYYY format")
        sys.exit(1)

    start_date = datetime.strptime(args[0], "%d/%m/%Y").date()
    end_date   = datetime.strptime(args[1], "%d/%m/%Y").date()

    print("=" * 60)
    print(f"L3 Coach Wed/Thu/Fri Scheduler — {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Range: {start_date} → {end_date}")
    print(f"Schedule: {SCHEDULE_ID}")
    print("=" * 60)

    if not dry_run:
        confirm = input("\nType 'yes' to run live: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

    days = get_wed_thu_fri_in_range(start_date, end_date)
    print(f"\nProcessing {len(days)} Wed/Thu/Fri day(s)...")

    # Pre-fetch ESC history once
    print("\nFetching ESC history for all agents...")
    esc_history = fetch_esc_history(start_date)
    for name, last in esc_history.items():
        print(f"  {name}: {'last ESC ' + last.strftime('%d %b %Y') if last else 'no recent ESC history'}")

    # Pre-fetch Mon/Tue bookings per week
    print("\nFetching Mon/Tue ESC/QC bookings...")
    mon_tue_booked = fetch_mon_tue_booked(start_date, end_date)
    for mon, booked in mon_tue_booked.items():
        print(f"  Week of {mon.strftime('%d %b')}: {', '.join(booked) if booked else 'none'}")

    # Pre-fetch all agent schedules for each day
    print("\nFetching schedules for all agents...")
    all_day_activities = {}
    unique_dates = list(dict.fromkeys(d for d, _ in days))
    for d in unique_dates:
        all_day_activities[d] = {}
        for name, agent_id in AGENTS.items():
            all_day_activities[d][name] = get_agent_activities(agent_id, d)
        print(f"  Fetched {d.strftime('%d %b')}")

    print()

    # Track used agents per week (reset each Monday)
    already_used  = set()
    current_week  = None

    for d, day_name in days:
        week_mon = get_week_monday(d)
        week_num = get_cycle_week(d)

        # Reset used agents each new week
        if week_mon != current_week:
            current_week = week_mon
            already_used = set()

        print(f"── {day_name} {d.strftime('%d %b %Y')} (cycle week {week_num}) ──")

        activities_by_name = all_day_activities[d]

        print(f"  Picking agents:")
        result = pick_two_agents(d, week_mon, activities_by_name, esc_history,
                                 mon_tue_booked, already_used, week_num)
        agent_a, agent_b, already_used = result

        if agent_a is None:
            print()
            continue

        agent_a_name, agent_a_id = agent_a
        agent_b_name, agent_b_id = agent_b
        print(f"  Agent A (start of shift + 2x spread): {agent_a_name}")
        print(f"  Agent B (3x spread): {agent_b_name}")

        print(f"\n  [A] ESC for {agent_a_name}:")
        book_agent_a(agent_a_id, agent_a_name, activities_by_name[agent_a_name], d, dry_run)

        print(f"\n  [B] ESC for {agent_b_name}:")
        book_agent_b(agent_b_id, agent_b_name, activities_by_name[agent_b_name], d, dry_run)

        print()

    print("=" * 60)
    print(f"{'DRY RUN complete — no changes made.' if dry_run else 'All done!'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
