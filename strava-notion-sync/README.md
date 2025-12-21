# Strava → Notion Workout Sync

This project automatically syncs your Strava workouts into a Notion database so you can review training patterns, annotate sessions, and use Notion (including Notion AI) as a thinking and reflection layer.

It is designed to be **reliable, explicit, and boring in the best way**.

Once set up, it runs unattended.

---

## What this does

- Pulls your recent activities from Strava (default: last 90 days)
- Creates **one row per activity** in a Notion database called **Workouts**
- Uses the Strava **Activity ID** to avoid duplicates
- Updates existing rows if an activity changes
- Writes only system-owned fields (metrics, links, sync metadata)
- Preserves any notes, reflections, or ratings you add in Notion
- Runs automatically on a schedule via GitHub Actions

Strava remains the source of truth.  
Notion is where interpretation happens.

---

## What this does *not* do

By design, this project avoids:

- Real-time syncing (webhooks)
- Storing second-by-second GPS or HR data in Notion
- Replacing Strava/Garmin analytics
- Advanced training load modeling (CTL / ATL / TSB)
- Automatically creating or modifying your Notion schema

The goal is clarity and stability, not maximal data ingestion.

---

## How it works (high level)
Strava API
↓
Python sync script
↓
Notion database (Workouts)
↓
Dashboards, reflection, Notion AI

---

## Data synced per activity

Each Strava activity maps to **one Notion row**.

### Always synced (system fields)

- Activity ID (unique key)
- Activity name
- Date & start time
- Sport type
- Duration (minutes)
- Distance (miles)
- Elevation gain (feet)
- Strava activity URL
- Last synced timestamp

### Synced when available

- Average heart rate
- Max heart rate
- Moving time (minutes)
- Average pace (min/mi for running, walking, hiking)

### Never overwritten

- Subjective notes
- RPE or effort ratings
- Injury notes
- Any custom reflection fields you add

You can safely write in Notion without worrying about the sync undoing your work.

---

## Heart rate zones (important)

This project **supports heart rate zones per activity**, with an important constraint.

### What is possible

- The script can compute **time-in-zone per activity** (e.g. Zone 1–5)
- It does this by:
  1. Fetching your **athlete HR zone definitions** from Strava
  2. Pulling the **heart rate stream** for each activity (seconds-level)
  3. Aggregating that stream into **zone totals**
- Only the **derived summary** (minutes per zone) is stored in Notion  
  Raw HR streams are **not** saved.

This keeps Notion lightweight while still enabling meaningful analysis.

### What this requires

- Heart rate data must be present for the activity
- HR zones must be defined in your Strava account
- An extra API call per activity (acceptable for daily syncs)

### Recommended Notion properties for HR zones

If you want HR zones, add these **optional** number fields:

- `HR Zone 1 (min)`
- `HR Zone 2 (min)`
- `HR Zone 3 (min)`
- `HR Zone 4 (min)`
- `HR Zone 5 (min)`

If these properties do not exist, the script will skip them gracefully.

---

## Notion database setup

Create a database called **Workouts** with the following properties.

### Required properties (exact names matter — copy/paste these)

- `Name` (Title)
- `Activity ID` (Rich text)
- `Date` (Date)
- `Sport` (Select)
- `Duration (min)` (Number)
- `Distance (mi)` (Number)
- `Elevation (ft)` (Number)

### Strongly recommended

- `Strava URL` (URL)
- `Last Synced` (Date)

### Optional (metrics)

- `Avg HR` (Number)
- `Max HR` (Number)
- `Avg Pace (min/mi)` (Number)
- `Moving Time (min)` (Number)

### Optional (heart rate zones)

- `HR Zone 1 (min)`
- `HR Zone 2 (min)`
- `HR Zone 3 (min)`
- `HR Zone 4 (min)`
- `HR Zone 5 (min)`

### Optional (aerobic decoupling / drift)

- `HR Drift (%)` (Number)
- `HR 1st Half (bpm)` (Number)
- `HR 2nd Half (bpm)` (Number)
- `Speed 1st Half (mph)` (Number)
- `Speed 2nd Half (mph)` (Number)
- `Drift Eligible` (Checkbox)
- `HR Data Quality` (Select: Good, Partial, None)

### Optional (weather)

- `Temperature (°F)` (Number)
- `Weather Conditions` (Rich text) - Concise summary with temp, conditions, wind, and humidity

**Note:** Weather data is only fetched for outdoor activities (runs, rides, hikes, etc.). Indoor activities like weight training will not include weather information. Weather is fetched based on the activity's start location and time using Open-Meteo's historical weather API.

### Optional (ops / debugging)

- `Sync Status` (Select: created, updated)

If a property is missing, the sync will skip it and continue.

---

## Setup overview

One-time setup consists of three parts:

1. Authorize Strava API access (read-only)
2. Create a Notion integration and database
3. Store credentials as GitHub repository secrets

After that, the sync runs automatically.

---

## Strava API setup (once)

1. Go to https://www.strava.com/settings/api
2. Create a new application
3. Use:
   - Category: Analytics
   - Authorization callback domain: `localhost`
4. Save your **Client ID** and **Client Secret**

### Getting a refresh token

1. Visit (replace `YOUR_CLIENT_ID`):

https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=read,activity:read_all,profile:read_all
2. Approve access
3. Copy the `code` from the redirect URL
4. Exchange it for tokens:
```bash
curl -X POST https://www.strava.com/oauth/token \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET \
  -d code=CODE_FROM_URL \
  -d grant_type=authorization_code

  	5.	Save the refresh_token

Access tokens expire. Refresh tokens do not (unless revoked).

⸻

## Required secrets (names to paste into GitHub / local env)

| Name | Purpose | Where to find it |
| --- | --- | --- |
| `STRAVA_CLIENT_ID` | Strava app client ID | Strava Settings → API |
| `STRAVA_CLIENT_SECRET` | Strava app client secret | Strava Settings → API |
| `STRAVA_REFRESH_TOKEN` | Long-lived token to get access tokens | Exchange auth code at `https://www.strava.com/oauth/token` |
| `NOTION_TOKEN` | Notion Internal Integration Token | Notion → My Integrations → your integration |
| `NOTION_DATABASE_ID` | The Workouts DB ID | In the Notion DB URL (32-char hex before the `?`) |

For local use, set these as environment variables. For GitHub Actions, create repository secrets with these exact names.

⸻

GitHub Actions automation
	•	The sync runs once per day on GitHub’s infrastructure
	•	You can trigger it manually from the Actions tab
	•	Re-running the job is safe and idempotent

Required secrets
	•	STRAVA_CLIENT_ID
	•	STRAVA_CLIENT_SECRET
	•	STRAVA_REFRESH_TOKEN
	•	NOTION_TOKEN
	•	NOTION_DATABASE_ID

⸻

How the sync behaves
	1.	Refreshes Strava access token
	2.	Fetches recent activities
	3.	Optionally fetches HR streams to compute time in HR zones
    	4.	Queries Notion for existing Activity IDs
	5.	Creates or updates rows accordingly
	6.	Logs counts (fetched, created, updated, failed)

If too many activities fail, the job exits loudly instead of silently corrupting data.

⸻

## Best practices for running this workflow

- **Start with a small window**
  - Keep the default 30‑day window while you validate behavior.
  - Once you trust it, you can extend history by temporarily increasing the `days` argument in `sync_strava_to_notion()` and running locally.

- **Stabilize your Notion schema early**
  - Create and name properties exactly as listed above, then avoid renaming them.
  - If you do change names, expect one “transition run” where some fields may not backfill.

- **Let GitHub Actions be the primary runner**
  - Use local runs for debugging or one‑off backfills.
  - For day‑to‑day usage, rely on the scheduled workflow so you don’t have overlapping manual + scheduled jobs fighting over the same data.

- **Watch logs after any change**
  - After you change Notion properties, Strava app settings, or secrets, watch the next GitHub Actions run.
  - Confirm:
    - Activities fetched count is non‑zero
    - Created/updated numbers look sane
    - Failed count is low, and any errors are understandable (e.g., schema mismatch you just introduced).

- **Treat secrets as production credentials**
  - Rotate Strava and Notion credentials if you ever suspect they’ve leaked.
  - Prefer GitHub Actions secrets over storing tokens in local `.env` files on multiple machines.

- **Don’t worry about occasional failures**
  - The script has retry/backoff and a failure threshold; transient 429/5xx errors will usually self‑heal.
  - If a run fails hard, fix the root cause and re‑run—the logic is idempotent, so you won’t create duplicates.

- **Validate HR‑related metrics on a few key workouts**
  - Pick 2–3 benchmark sessions (steady long run, tempo, easy run).
  - Compare:
    - Average HR and pace in Strava vs Notion
    - HR zone minutes vs your intuition for that workout
    - Drift (%) vs whether the run “felt” decoupled or not.
  - Once they line up, you can generally trust the metrics for day‑to‑day use.

⸻

Design principles
	•	Stability over completeness
	•	Derived metrics, not raw streams
	•	Clear ownership of fields (system vs human)
	•	Notion as a thinking layer, not a data warehouse

This is meant to fade into the background and support better decisions over time.

⸻

Possible future extensions (verified feasible)
	•	Longer history backfills
	•	Weekly or monthly rollups
	•	AI-generated summaries written into Notion
	•	Flags for sudden load increases
	•	Strava webhooks for near-real-time sync (requires hosted endpoint)

   
