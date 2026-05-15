# WFM Automation

Automation scripts for Marshmallow's Workforce Management tooling in Assembled.

---

## Repository structure

```
wfm-automation/
├── .github/
│   └── workflows/
│       ├── question_channel_scheduler.yml
│       ├── esc_scheduler.yml
│       └── total_loss_scheduler.yml
├── question_channel_scheduler.py   # QC rotation scheduler
├── esc_scheduler.py                # Escalation session scheduler
├── total_loss_scheduler.py         # Nonfault Total Loss scheduler
├── rotation.py                     # Shared rotation config (edit this to change agents/rotation)
├── .gitignore
└── README.md
```

> ⚠️ If you need to update the agent list or QC rotation, **only edit `rotation.py`**. All scripts import from it.

---

## Scripts

### `question_channel_scheduler.py`

Automatically schedules **Question Channel** events for L2 agents after the monthly schedule has been generated in Assembled. Fills gaps between existing default events (breaks, lunch, focus time) within the correct slot window.

#### The rotation

The 4-week rotation cycles from **3rd June 2026**. All times are **Europe/Budapest**.

| Slot | Week 1 | Week 2 | Week 3 | Week 4 |
|------|--------|--------|--------|--------|
| Wed AM (09:00–13:30) | Tien | Tien | Tien | Tien |
| Wed PM (13:30–18:00) | Jad | Henriett | Jad | Krisztina |
| Thu AM (10:00–14:30) | Dora | Dora | Dora | Dora |
| Thu PM (13:30–18:00) | Krisztina | Katalin | Henriett | Katalin |
| Fri AM (09:00–13:30) | Katalin | Henriett | Jad | Krisztina |
| Fri PM (13:30–18:00) | Henriett | Krisztina | Katalin | Jad |

Weeks 5–8 repeat weeks 1–4.

**NWD notes:**
- Tien and Dora are fixed to their slots every week (no conflicting NWDs on Wed/Thu/Fri)
- Jad has NWDs on Thursdays in odd weeks — never scheduled Thu PM in weeks 1 or 3
- Krisztina has NWDs on Fridays in odd weeks — never scheduled on Fri in weeks 1 or 3
- Katalin has NWDs on Fridays in even weeks — never scheduled on Fri in weeks 2 or 4
- Henriett has no NWDs on Wed/Thu/Fri — fully flexible

#### Skipped slots

If an agent is on holiday or has no schedule, the script will skip their slot and flag it:

```
⏭ SKIPPED — Katalin is on Holiday

⚠ 1 slot(s) skipped — manual cover needed:
  • Fri 05 Jun | Katalin (AM) — Holiday
```

---

### `esc_scheduler.py`

Schedules **Escalation (ESC)** sessions at a fixed **18:30–19:00 Budapest** time on Wed/Thu/Fri.

#### Logic
1. Checks **Eszter** first — if she has a shift covering 18:30–19:00, she gets the slot
2. If not, checks Henriett, Jad, Katalin, Krisztina for availability
3. Picks whoever has done ESC **least recently** (fairness rotation)
4. Flags any days where nobody is available

---

### `total_loss_scheduler.py`

Schedules **Nonfault Total Loss** 1-hour sessions on Wednesdays and Fridays.

#### Logic
1. Skips agents already on QC that day (cross-references `rotation.py`)
2. Checks Eszter first (if she has a schedule that day)
3. Picks whoever has done Total Loss **least recently**
4. Tries not to use the same agent on both Wed and Fri in the same week
5. Finds a 1-hour gap **outside 11:30–14:30 Budapest** (avoiding lunch)
6. Prefers morning (09:00–11:30) then afternoon (14:30–19:00)

---

## Running the scripts

### Option 1 — Scheduler UI (recommended)

A web interface is available that lets you pick dates and trigger workflows with one click — no GitHub access needed.

1. Open the **Question Channel Scheduler** HTML file from Google Drive (link in 1Password → WFM Automation)
2. First time: expand **GitHub settings**, paste the token from 1Password, fill in repo owner (`sarahthompson-mm`) and repo name (`wfm-automation`) — saves to your browser automatically
3. Enter **from** and **to** dates in DD/MM/YYYY format
4. Click **Run scheduler**

> Note: The UI currently only triggers the QC script. For ESC and Total Loss, use Option 2.

### Option 2 — GitHub Actions directly

1. Go to **Actions** in this repository
2. Select the workflow you want to run
3. Click **Run workflow**
4. Enter **from** and **to** dates in DD/MM/YYYY format
5. Click **Run workflow**

The script logs its progress and prints a summary of any skipped slots at the end.

> ⚠️ Always run scripts **after** generating the schedule in Assembled for that period, not before.

---

## Order of operations (monthly)

Run the scripts in this order after generating the schedule:

1. **Question Channel** (`question_channel_scheduler.py`) — fills QC slots
2. **ESC** (`esc_scheduler.py`) — books escalation sessions
3. **Total Loss** (`total_loss_scheduler.py`) — books Total Loss sessions

All three accept the same DD/MM/YYYY date range format. Running a full month at once (e.g. `01/06/2026` to `30/06/2026`) works for all scripts.

---

## Setup

### Requirements

- Python 3.11+
- `requests` and `pytz` (installed automatically by GitHub Actions)

### GitHub secrets

Set under **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `ASSEMBLED_API_KEY` | Assembled API key. Generate at [app.assembledhq.com/settings/api](https://app.assembledhq.com/settings/api) |

### GitHub personal access token (for the UI)

1. Go to [github.com/settings/tokens/new](https://github.com/settings/tokens/new?scopes=workflow)
2. Name it "WFM Automation", tick `workflow` scope, set to no expiration
3. Save in **1Password → WFM Automation**

---

## Updating the rotation

If an agent joins, leaves, or changes hours — edit **`rotation.py`** only. All three scripts import from it so you only need to change it in one place.

If the rotation needs a new anchor date, update `WEEK_1_ANCHOR` in `rotation.py`.

---

## Background

These scripts exist because Assembled's native tooling doesn't support these scheduling patterns well:

- **Event rules** fail to place events at shift start when other default events are already scheduled first
- **Recurring events** are wiped by Assembled's schedule generation
- **Templates** can't be applied to individual agents

The API-based approach (generate schedule first, then overlay via scripts) is the most reliable solution.
