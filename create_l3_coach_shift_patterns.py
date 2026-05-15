"""
One-off script: Create 9-week rotating shift patterns for L3 Coaches in Assembled.

Uses the deprecated (but still functional) shift patterns API.
Run once, then delete.

Usage:
    ASSEMBLED_API_KEY=sk_live_your_key_here python create_l3_coach_shift_patterns.py

    Or with the --dry-run flag to preview without making any API calls:
    ASSEMBLED_API_KEY=sk_live_your_key_here python create_l3_coach_shift_patterns.py --dry-run
"""

import os
import sys
import json
import time
import requests
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("ASSEMBLED_API_KEY", "sk_live_YOUR_KEY_HERE")
BASE_URL = "https://api.assembledhq.com/v0"
TIMEZONE = "Europe/Budapest"

# Cycle anchor: Monday 2 June 2026 = Week 1
CYCLE_ANCHOR = date(2026, 6, 2)

# Agent IDs (from Assembled) mapped to their names
AGENTS = [
    {"name": "Barbara Tothova",  "id": "bcc47bbc-a64e-4c5c-b9da-5ba3d9221fd3"},
    {"name": "Barnabas Fono",    "id": "fffd48ab-1abc-4264-b35e-7d1b72c002d5"},
    {"name": "Csongor Zeitler",  "id": "9bb19696-3974-4664-a66c-b5b7ecf93be2"},
    {"name": "Dorina Barany",    "id": "84c0949f-b286-4337-b684-c369535e8807"},
    {"name": "Eszter Borsia",    "id": "bf3086f1-ad27-4caf-b546-dc2501a6edac"},
    {"name": "Gabo Bata",        "id": "03b9c60c-55d9-43d9-91f0-b29c003a8aad"},
    {"name": "Marton Onody",     "id": "a862b950-5eaa-4135-bf0d-f5e2cdd90037"},
    {"name": "Nora Nemeth",      "id": "8e037202-8e52-4020-8d20-aef4794e4016"},
    {"name": "Timea Rabb",       "id": "f1e73f06-14ab-445f-96d3-416d2172b4b1"},
]

# Rotation pattern (0-indexed):
#   sat_week[i] = i          → agent i does Saturday in week i+1
#   mon_week[i] = (i-1) % 9  → agent i does Monday late in week (i-1)%9 + 1
#   tue_week[i] = (i-2) % 9  → agent i does Tuesday late in week (i-2)%9 + 1
#
# NWD cycles Tue/Wed/Thu/Fri/Tue/Wed/Thu/Fri/Tue (index = sat_week = agent index)
NWD_DAYS = ["Tuesday", "Wednesday", "Thursday", "Friday",
            "Tuesday", "Wednesday", "Thursday", "Friday", "Tuesday"]
# Day index within the week: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat
NWD_DAY_INDEX = [1, 2, 3, 4, 1, 2, 3, 4, 1]

# Shift times in Budapest time (HH:MM)
STANDARD_START = "09:00"
STANDARD_END   = "18:00"
LATE_START     = "10:00"
LATE_END       = "19:00"

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def api_post(endpoint, payload, dry_run=False):
    url = f"{BASE_URL}{endpoint}"
    if dry_run:
        print(f"  [DRY RUN] POST {url}")
        print(f"  Payload: {json.dumps(payload, indent=2)}")
        return {"id": "dry-run-id"}
    resp = requests.post(url, json=payload, auth=(API_KEY, ""))
    if not resp.ok:
        print(f"  ERROR {resp.status_code}: {resp.text}")
        sys.exit(1)
    time.sleep(0.25)  # stay well under 5 req/s rate limit
    return resp.json()


def build_week_schedule(agent_idx):
    """
    Returns a list of 9 week dicts, each describing the shift for that week.
    Each week dict has keys: mon, tue, wed, thu, fri, sat, sun
    Values are dicts with 'start' and 'end' times, or None for NWD/off.
    """
    sat_week  = agent_idx           # 0-indexed week where this agent does Saturday
    mon_week  = (agent_idx - 1) % 9
    tue_week  = (agent_idx - 2) % 9
    nwd_day_i = NWD_DAY_INDEX[sat_week]  # which weekday is the NWD (0=Mon..4=Fri)

    std  = {"start": STANDARD_START, "end": STANDARD_END}
    late = {"start": LATE_START,     "end": LATE_END}
    off  = None

    day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    weeks = []
    for w in range(9):
        week = {}
        for i, day in enumerate(day_names):
            if i == 6:  # Sunday always off
                week[day] = off
            elif i == 5:  # Saturday
                week[day] = late if w == sat_week else off
            elif i == nwd_day_i and w == sat_week:
                week[day] = off  # NWD
            elif i == 0 and w == mon_week:
                week[day] = late
            elif i == 1 and w == tue_week:
                week[day] = late
            else:
                week[day] = std
        weeks.append(week)
    return weeks


def weeks_to_shifts_payload(weeks):
    """
    Convert our week schedule into the format expected by the shift patterns API.
    The API expects a 'shifts' list where each item covers one day across all weeks.

    Based on the deprecated shift patterns API, the creation object takes:
    {
      "name": "...",
      "timezone": "...",
      "shifts": [
        {
          "days": ["monday"],   // which days this shift applies to
          "start_time": "09:00",
          "end_time": "18:00",
          "week_numbers": [1, 2, 3, ...]  // which weeks (1-indexed) this applies
        },
        ...
      ]
    }

    We build the minimal set of shift entries by grouping identical
    (start, end, days) combinations across weeks.
    """
    day_names_api = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day_keys      = ["mon",    "tue",     "wed",       "thu",      "fri",    "sat",      "sun"]

    # Collect per-day, per-week shift info
    # structure: {(start, end): {day_name: [week_numbers...]}}
    from collections import defaultdict
    shift_map = defaultdict(lambda: defaultdict(list))

    for w_idx, week in enumerate(weeks):
        week_num = w_idx + 1
        for day_key, day_api in zip(day_keys, day_names_api):
            slot = week[day_key]
            if slot is not None:
                key = (slot["start"], slot["end"])
                shift_map[key][day_api].append(week_num)

    shifts = []
    for (start, end), days_dict in shift_map.items():
        # Try to collapse days that share the same week_numbers into one entry
        # Group days by their week_numbers list
        week_nums_to_days = defaultdict(list)
        for day, wnums in days_dict.items():
            week_nums_to_days[tuple(sorted(wnums))].append(day)

        for wnums, days in week_nums_to_days.items():
            shifts.append({
                "days": days,
                "start_time": start,
                "end_time": end,
                "week_numbers": list(wnums),
            })

    return shifts


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("=" * 60)
        print("DRY RUN MODE — no API calls will be made")
        print("=" * 60)
    else:
        print("=" * 60)
        print("LIVE MODE — this will create shift patterns in Assembled")
        print("=" * 60)
        confirm = input("Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

    created_patterns = {}  # agent_id -> pattern_id

    for agent in AGENTS:
        agent_idx = AGENTS.index(agent)
        name      = agent["name"]
        agent_id  = agent["id"]

        print(f"\n→ Building pattern for {name} (agent index {agent_idx})...")

        weeks  = build_week_schedule(agent_idx)
        shifts = weeks_to_shifts_payload(weeks)

        sat_week  = agent_idx
        mon_week  = (agent_idx - 1) % 9
        tue_week  = (agent_idx - 2) % 9
        nwd_label = NWD_DAYS[sat_week]

        pattern_name = (
            f"L3 Coach - {name} "
            f"(Sat wk{sat_week+1}, Mon late wk{mon_week+1}, "
            f"Tue late wk{tue_week+1}, NWD {nwd_label} wk{sat_week+1})"
        )

        payload = {
            "name":     pattern_name,
            "timezone": TIMEZONE,
            "shifts":   shifts,
        }

        print(f"  Creating pattern: {pattern_name}")
        result = api_post("/shift_patterns", payload, dry_run=dry_run)

        pattern_id = result.get("id", "dry-run-id")
        created_patterns[agent_id] = pattern_id
        print(f"  Created pattern ID: {pattern_id}")

    print("\n" + "=" * 60)
    print("Assigning patterns to agents...")
    print("=" * 60)

    for agent in AGENTS:
        agent_id   = agent["id"]
        name       = agent["name"]
        pattern_id = created_patterns[agent_id]

        # Assign from cycle anchor date
        assign_payload = {
            "agent_id":        agent_id,
            "shift_pattern_id": pattern_id,
            "start_date":      CYCLE_ANCHOR.isoformat(),
        }

        print(f"\n→ Assigning pattern to {name}...")
        api_post("/shift_patterns/assign", assign_payload, dry_run=dry_run)
        print(f"  ✓ Assigned (pattern {pattern_id}, start {CYCLE_ANCHOR})")

    print("\n" + "=" * 60)
    print("ALL DONE!")
    print(f"  9 shift patterns created and assigned.")
    print(f"  Cycle starts: {CYCLE_ANCHOR} (Week 1 = 2 Jun 2026)")
    print(f"  Timezone: {TIMEZONE}")
    if dry_run:
        print("\n  This was a dry run. Run without --dry-run to apply for real.")
    print("=" * 60)

    # Print a summary for reference
    print("\nSUMMARY — agent → pattern:")
    for agent in AGENTS:
        pid = created_patterns[agent["id"]]
        print(f"  {agent['name']:<22} → {pid}")


if __name__ == "__main__":
    main()
