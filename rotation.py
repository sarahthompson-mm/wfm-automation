"""
rotation.py
-----------
Shared rotation configuration for WFM automation scripts.
Import this module to get the QC rotation and agent IDs.

The 4-week rotation anchors to 3rd June 2026 (a Wednesday).
Weeks 5-8 repeat weeks 1-4.
"""

from datetime import datetime
import pytz

BUDAPEST = pytz.timezone("Europe/Budapest")

# Week 1 anchor date (must be a Wednesday)
WEEK_1_ANCHOR = datetime(2026, 6, 3, tzinfo=BUDAPEST)

# All agent IDs
AGENTS = {
    "Tien":      "8ffbdc6b-8404-43c2-bd2c-da5577260e27",
    "Dora":      "a904049d-524a-45d0-9492-935be9091c59",
    "Henriett":  "1d9e4692-7388-47be-9df1-c9a7bcd1a6cf",
    "Jad":       "109bd604-1e51-4fe4-b653-0002bab43911",
    "Katalin":   "5bea70c6-04e4-41d3-9640-1fb53a4e4015",
    "Krisztina": "b8701026-bd7f-4a18-9856-dd67a9d480fa",
    "Eszter":    "d05599b8-61b4-4f71-9b15-227f7c69af46",
}

# 4-week QC rotation
# Structure: week_number -> day_offset (0=Wed, 1=Thu, 2=Fri) -> [(agent_name, slot_type)]
# slot_type: "am", "dora_am", "pm"
QC_ROTATION = {
    1: {
        0: [("Tien", "am"),      ("Jad", "pm")],        # Wed
        1: [("Dora", "dora_am"), ("Krisztina", "pm")],  # Thu
        2: [("Katalin", "am"),   ("Henriett", "pm")],   # Fri
    },
    2: {
        0: [("Tien", "am"),      ("Henriett", "pm")],   # Wed
        1: [("Dora", "dora_am"), ("Katalin", "pm")],    # Thu
        2: [("Henriett", "am"),  ("Krisztina", "pm")],  # Fri
    },
    3: {
        0: [("Tien", "am"),      ("Jad", "pm")],        # Wed
        1: [("Dora", "dora_am"), ("Henriett", "pm")],   # Thu
        2: [("Jad", "am"),       ("Katalin", "pm")],    # Fri
    },
    4: {
        0: [("Tien", "am"),      ("Krisztina", "pm")],  # Wed
        1: [("Dora", "dora_am"), ("Katalin", "pm")],    # Thu
        2: [("Krisztina", "am"), ("Jad", "pm")],        # Fri
    },
}


def get_week_number(date: datetime) -> int:
    """Return which week of the 4-week QC cycle a given date falls in (1-4)."""
    # Find the Wednesday of the week containing this date
    days_since_wednesday = (date.weekday() - 2) % 7
    week_wednesday = date - __import__('datetime').timedelta(days=days_since_wednesday)
    delta = (week_wednesday.date() - WEEK_1_ANCHOR.date()).days
    weeks_since = delta // 7
    return (weeks_since % 4) + 1


def get_qc_agents_on_day(date: datetime) -> list[str]:
    """
    Return list of agent names scheduled for QC on a given date.
    Returns empty list if the date is not a Wed/Thu/Fri.
    """
    weekday = date.weekday()
    if weekday not in (2, 3, 4):  # Wed=2, Thu=3, Fri=4
        return []

    day_offset = weekday - 2  # 0=Wed, 1=Thu, 2=Fri
    week_num = get_week_number(date)
    slots = QC_ROTATION[week_num].get(day_offset, [])
    return [agent_name for agent_name, _ in slots]
