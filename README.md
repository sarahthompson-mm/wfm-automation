# WFM Automation

Automation scripts for Marshmallow's Workforce Management tooling in Assembled.

---

## Scripts

### `question_channel_scheduler.py`

Automatically schedules **Question Channel** events for L2 agents after the monthly schedule has been generated in Assembled.

#### Background

L2 agents spend one half-day per week working the Question Channel (a dedicated internal support queue). Because Assembled's schedule generation doesn't natively support this kind of rotation overlay, this script runs after generation and fills the correct time slots with Question Channel events for the right agents.

#### How it works

1. You run the script manually via GitHub Actions after generating the monthly schedule in Assembled
2. The script works out which week of the 4-week rotation cycle the given date falls in
3. For each slot that week (Wed AM, Wed PM, Thu AM, Thu PM, Fri AM, Fri PM), it:
   - Fetches the assigned agent's schedule for that day
   - Checks if the agent is on holiday or time off — if so, skips the slot and flags it in the output
   - Finds any gaps between existing events (breaks, lunch, focus time etc.) within the slot window
   - Creates Question Channel events to fill those gaps

Question Channel events are **Default** type, so they sit between existing scheduled events rather than overwriting them.

#### The rotation

The 4-week rotation cycles from **3rd June 2026**. Each agent does one slot per week:

| Slot | Week 1 | Week 2 | Week 3 | Week 4 |
|------|--------|--------|--------|--------|
| Wed AM (09:00–13:30) | Tien | Tien | Tien | Tien |
| Wed PM (13:30–18:00) | Jad | Henriett | Jad | Krisztina |
| Thu AM (10:00–14:30) | Dora | Dora | Dora | Dora |
| Thu PM (13:30–18:00) | Krisztina | Katalin | Henriett | Katalin |
| Fri AM (09:00–13:30) | Katalin | Henriett | Jad | Krisztina |
| Fri PM (13:30–18:00) | Henriett | Krisztina | Katalin | Jad |

Weeks 5–8 repeat weeks 1–4. All times are **Europe/Budapest**.

**NWD notes:**
- Tien and Dora are fixed to their slots every week (no conflicting NWDs on Wed/Thu/Fri)
- Jad has NWDs on Thursdays in odd weeks — never scheduled Thu PM in weeks 1 or 3
- Krisztina has NWDs on Fridays in odd weeks — never scheduled on Fri in weeks 1 or 3
- Katalin has NWDs on Fridays in even weeks — never scheduled on Fri in weeks 2 or 4
- Henriett has no NWDs on Wed/Thu/Fri — fully flexible

If an agent is on holiday (not a regular NWD), the script will skip their slot and flag it in the output so the team can arrange cover manually.

#### Running the script

1. Go to **Actions** in this repository
2. Select **Schedule Question Channel Events**
3. Click **Run workflow**
4. Enter the **Wednesday date** of the week you want to schedule (format: `YYYY-MM-DD`, e.g. `2026-06-03`)
5. Click **Run workflow**

The script will log its progress and print a summary at the end showing any skipped slots that need manual attention.

> ⚠️ Always run this **after** generating the schedule in Assembled for that period, not before.

#### Skipped slots

If an agent is on time off, the script will print something like:

```
⏭ SKIPPED — Katalin is on Holiday

⚠ 1 slot(s) skipped — manual cover needed:
  • Fri 05 Jun | Katalin (AM) — Holiday
```

When this happens, someone will need to manually assign that slot to another available agent in Assembled.

---

## Setup

### Requirements

- Python 3.11+
- `requests` and `pytz` libraries (installed automatically by the GitHub Action)

### GitHub secrets

The following secret must be set under **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `ASSEMBLED_API_KEY` | Assembled API key (starts with `sk_live_`). Generate at [app.assembledhq.com/settings/api](https://app.assembledhq.com/settings/api) |

### Updating the rotation

If the team changes (someone joins, leaves, or changes hours), update the `AGENTS` dict and `ROTATION` in `question_channel_scheduler.py`. The rotation table in this README should also be updated to match.

If the rotation needs to restart from a new anchor date, update `WEEK_1_ANCHOR` in the script.

---

## Repo structure

```
wfm-automation/
├── .github/
│   └── workflows/
│       └── question_channel_scheduler.yml   # GitHub Actions workflow
├── question_channel_scheduler.py            # Main script
├── .gitignore                               # Prevents committing junk/secrets
└── README.md                               # You are here!
```

---

## Background & decisions

This script exists because Assembled's native tooling doesn't support this scheduling pattern well:

- **Event rules** fail to place events at shift start when other default events are already scheduled first
- **Recurring events** are wiped by Assembled's schedule generation
- **Templates** can't be applied to individual agents

The API-based approach (generate schedule first, then fill gaps via script) is the most reliable solution and gives full control over the rotation logic.
