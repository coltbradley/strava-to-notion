# Strava → Notion Workout Sync

Automatically sync your Strava workouts into a Notion database. This tool pulls your recent activities from Strava, extracts key metrics (duration, distance, heart rate, weather, etc.), and creates or updates rows in your Notion database.

**Designed to be reliable, explicit, and boring in the best way.** Once set up, it runs unattended.

---

## Table of Contents

- [What This Does](#what-this-does)
- [What This Does Not Do](#what-this-does-not-do)
- [Repository Structure](#repository-structure)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Setup Guide](#setup-guide)
- [Running the Sync](#running-the-sync)
- [Weekly Status Report (Optional)](#weekly-status-report-optional)
- [Sync Behavior](#sync-behavior)
- [Notion Database Setup](#notion-database-setup)
- [Troubleshooting](#troubleshooting)
- [Project Files Explained](#project-files-explained)

---

## What This Does

- **Pulls recent activities** from Strava (default: last 30 days)
- **Creates one row per activity** in your Notion database
- **Prevents duplicates** using Strava Activity ID as the unique key
- **Updates existing rows** when activities change (e.g., you edit the name in Strava)
- **Writes only system fields** (metrics, links, sync metadata)
- **Preserves user data** (your notes, reflections, ratings in Notion are never overwritten)
- **Runs automatically** on a schedule via GitHub Actions (hourly by default)

**Strava remains the source of truth. Notion is where interpretation happens.**

---

## What This Does Not Do

By design, this project avoids:

- ❌ Real-time syncing (webhooks)
- ❌ Storing second-by-second GPS or HR data in Notion
- ❌ Replacing Strava/Garmin analytics
- ❌ Advanced training load modeling (CTL / ATL / TSB)
- ❌ Automatically creating or modifying your Notion schema
- ❌ Deleting activities from Notion (even if deleted on Strava)

**The goal is clarity and stability, not maximal data ingestion.**

---

## Repository Structure

```
strava-to-notion/
├── strava-notion-sync/          # Main project directory
│   ├── sync.py                   # Main sync script (entry point)
│   ├── requirements.txt          # Python dependencies
│   ├── README.md                 # This file
│   ├── NOTION_PROPERTIES.md      # Complete list of Notion property names
│   ├── README_DEVELOPMENT.md     # Development guide (testing, linting)
│   ├── pyproject.toml            # Ruff configuration (linting/formatting)
│   ├── scripts/                  # Utility scripts
│   │   ├── weekly_status_report.py  # Weekly report generator
│   │   └── send_status_email.py     # Email sender
│   ├── tests/                    # Test suite
│   │   ├── __init__.py
│   │   ├── test_unit_conversions.py
│   │   ├── test_schema.py
│   │   ├── test_hr_zones.py
│   │   └── test_weekly_report.py
│   └── stats/                    # Runtime stats (auto-generated, git-ignored)
│       └── run_stats.json        # Per-run statistics
├── .github/
│   └── workflows/
│       ├── sync.yml              # Main sync workflow (hourly)
│       └── weekly-status.yml     # Weekly status report workflow (Sundays)
├── .gitignore                    # Git ignore patterns
└── README.md                     # Root README (minimal)
```

---

## Quick Start

1. **Set up Strava API access** → Get Client ID, Client Secret, and Refresh Token
2. **Create Notion integration** → Get Integration Token and Database ID
3. **Configure GitHub Actions secrets** → Add all credentials as repository secrets
4. **Create Notion database** → Set up the Workouts database with required properties
5. **Run the workflow** → Either manually trigger or wait for scheduled run

The sync will run automatically every hour. See [Setup Guide](#setup-guide) for detailed instructions.

---

## Installation

### Prerequisites

- Python 3.11 or higher
- A GitHub account (for automated runs via GitHub Actions)
- A Strava account
- A Notion account with workspace permissions

### Local Installation

```bash
# Clone the repository
git clone <your-repo-url>
cd strava-to-notion/strava-notion-sync

# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

The project requires:
- `requests` (>=2.31.0) - HTTP client for Strava and weather APIs
- `notion-client` (>=2.2.1) - Official Notion API client

Optional development dependencies (see `README_DEVELOPMENT.md`):
- `pytest` - For running tests
- `ruff` - For linting and formatting

---

## Setup Guide

### Step 1: Strava API Setup

1. Go to [Strava API Settings](https://www.strava.com/settings/api)
2. Click "Create App"
3. Fill in:
   - **Application Name**: e.g., "Notion Sync"
   - **Category**: Analytics
   - **Authorization Callback Domain**: `localhost`
   - **Website**: (optional)
4. Click "Create"
5. **Save your Client ID and Client Secret**

#### Getting a Refresh Token

1. Visit this URL (replace `YOUR_CLIENT_ID` with your actual Client ID):

   ```
   https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=read,activity:read_all,profile:read_all
   ```

2. Authorize the application
3. You'll be redirected to `http://localhost/?code=CODE_HERE`
4. Copy the `code` value from the URL
5. Exchange the code for tokens:

   ```bash
   curl -X POST https://www.strava.com/oauth/token \
     -d client_id=YOUR_CLIENT_ID \
     -d client_secret=YOUR_CLIENT_SECRET \
     -d code=CODE_FROM_URL \
     -d grant_type=authorization_code
   ```

6. **Save the `refresh_token` from the response** (this is long-lived and doesn't expire)

**Important**: The `scope` includes `profile:read_all` which is required for HR zone data. Make sure you use the full authorization URL above.

### Step 2: Notion Integration Setup

1. Go to [Notion Integrations](https://www.notion.so/my-integrations)
2. Click "New integration"
3. Fill in:
   - **Name**: e.g., "Strava Sync"
   - **Associated workspace**: Select your workspace
   - **Type**: Internal
4. Under "Capabilities":
   - ✅ Read content
   - ✅ Update content
   - ✅ Insert content
5. Click "Submit"
6. **Copy the "Internal Integration Token"** (this is your `NOTION_TOKEN`)

### Step 3: Create Notion Database

1. In Notion, create a new database (or page with database view)
2. Name it "Workouts" (or any name you prefer)
3. **Share the database with your integration**:
   - Click "..." menu on the database
   - Select "Connections" → Your integration name
   - Click to connect
4. **Get the Database ID**:
   - Open the database in Notion
   - The URL looks like: `https://www.notion.so/YOUR_WORKSPACE/DATABASE_ID?v=...`
   - The `DATABASE_ID` is the 32-character hex string before the `?`
   - Copy this value (your `NOTION_DATABASE_ID`)

5. **Add the required properties** (see [Notion Database Setup](#notion-database-setup) below)

### Step 4: Configure GitHub Actions Secrets

1. In your GitHub repository, go to **Settings** → **Secrets and variables** → **Actions**
2. Click "New repository secret"
3. Add each of these secrets:

   | Secret Name | Value | Where to Find It |
   |------------|-------|------------------|
   | `STRAVA_CLIENT_ID` | Your Strava app Client ID | Strava Settings → API |
   | `STRAVA_CLIENT_SECRET` | Your Strava app Client Secret | Strava Settings → API |
   | `STRAVA_REFRESH_TOKEN` | The refresh token from Step 1 | From the token exchange response |
   | `NOTION_TOKEN` | Your Notion integration token | Notion → My Integrations → Integration Token |
   | `NOTION_DATABASE_ID` | The 32-character database ID | From the Notion database URL |
   | `WEATHER_API_KEY` | (Optional) WeatherAPI.com key | See [Weather Setup](#weather-setup-optional) below |
   | `NOTION_DAILY_SUMMARY_DATABASE_ID` | (Optional) Daily Summary DB ID | See [Optional: Daily Summary + Athlete Metrics](#optional-daily-summary--athlete-metrics) |
   | `NOTION_ATHLETE_METRICS_DATABASE_ID` | (Optional) Athlete Metrics DB ID | See [Optional: Daily Summary + Athlete Metrics](#optional-daily-summary--athlete-metrics) |
   | `ATHLETE_NAME` | (Optional) Athlete name | Default: "Athlete" |

4. **Important**: Secret names must match exactly (case-sensitive)

### Step 5: Weather Setup (Optional)

Weather data is fetched for outdoor activities. Two options:

**Option A: WeatherAPI.com (Recommended - minimal delay ~15 minutes)**
1. Sign up at [WeatherAPI.com](https://www.weatherapi.com/signup.aspx) (free tier available)
2. Get your API key
3. Add it as `WEATHER_API_KEY` in GitHub Actions secrets

**Option B: Open-Meteo (Fallback - 2-day delay)**
- No setup required
- Automatically used if `WEATHER_API_KEY` is not set
- Has a 2-day delay for historical data

---

## Notion Database Setup

Your Notion database must have specific properties with **exact names** (case-sensitive). See `NOTION_PROPERTIES.md` for a complete reference.

### Required Properties

Create these properties with these **exact names**:

| Property Name | Type | Description |
|--------------|------|-------------|
| `Name` | Title | Activity name |
| `Activity ID` | Rich text | Strava activity ID (unique key) |
| `Date` | Date | Activity start date and time |
| `Sport` | Select | Activity type (Run, Ride, etc.) |
| `Duration (min)` | Number | Activity duration in minutes |
| `Distance (mi)` | Number | Distance in miles |
| `Elevation (ft)` | Number | Elevation gain in feet |

### Strongly Recommended

| Property Name | Type | Description |
|--------------|------|-------------|
| `Strava URL` | URL | Link to activity on Strava |
| `Last Synced` | Date | When this row was last updated |

### Optional Properties

The sync will work with just the required properties. Add these for additional data:

**Basic Metrics:**
- `Avg HR` (Number)
- `Max HR` (Number)
- `Avg Pace (min/mi)` (Number) - Only for running/walking/hiking
- `Moving Time (min)` (Number)

**Heart Rate Zones:**
- `HR Zone 1 (min)` (Number)
- `HR Zone 2 (min)` (Number)
- `HR Zone 3 (min)` (Number)
- `HR Zone 4 (min)` (Number)
- `HR Zone 5 (min)` (Number)

**Aerobic Decoupling / HR Drift:**
- `HR Drift (%)` (Number)
- `HR 1st Half (bpm)` (Number)
- `HR 2nd Half (bpm)` (Number)
- `Speed 1st Half (mph)` (Number)
- `Speed 2nd Half (mph)` (Number)
- `Drift Eligible` (Checkbox)
- `HR Data Quality` (Select) - Options: "Good", "Partial", "None"

**Weather:**
- `Temperature (°F)` (Number) - Note: includes degree symbol and parentheses
- `Weather Conditions` (Rich text) - Note: exact capitalization

**Operations:**
- `Sync Status` (Select) - Options: "created", "updated"
- `Photo URL` (URL) - Primary activity photo

**Important Notes:**
- Property names are **case-sensitive** and must match exactly
- Special characters matter (e.g., `Temperature (°F)` includes the degree symbol)
- If a property doesn't exist, the sync will skip it gracefully
- You can safely add custom properties - the sync only writes to system properties

---

## Optional: Daily Summary + Athlete Metrics

The sync supports two optional Notion databases for aggregated metrics and trending:

1. **Daily Summary Database** - One row per day with aggregated totals and load points
2. **Athlete Metrics Database** - Single row with rolling 7-day and 28-day load metrics

These databases are **completely optional** - the main Workouts sync will work perfectly without them. Enable them if you want trend analysis and dashboards.

### Why These Exist

- **Daily Summary**: Aggregate activities by day for easier trend analysis and weekly/monthly reviews
- **Athlete Metrics**: Track rolling training load to monitor fitness trends and balance
- **Load Points**: Zone-weighted training load metric (Z1×1 + Z2×2 + Z3×3 + Z4×4 + Z5×5) derived from HR zones

### Setup

#### 1. Create Daily Summary Database (Optional)

1. In Notion, create a new database (e.g., "Daily Summary")
2. Share it with your integration (same as Workouts database)
3. Get the Database ID from the URL
4. Add it as `NOTION_DAILY_SUMMARY_DATABASE_ID` in GitHub Actions secrets

**Required Properties:**
- `Date` (Date) - The date (unique key for upsert)
- `Total Duration (min)` (Number)
- `Total Moving Time (min)` (Number)
- `Total Distance (mi)` (Number)
- `Total Elevation (ft)` (Number)
- `Session Count` (Number)
- `Load (pts)` (Number) - Zone-weighted load points
- `Load Confidence` (Select) - Options: "High", "Medium", "Low"
- `Notes` (Text) - Optional, for your notes

**Load Confidence Explanation:**
- **High**: All sessions that day had load computed (all had HR zones)
- **Medium**: At least one session had load computed
- **Low**: No sessions had load computed

#### 2. Create Athlete Metrics Database (Optional)

1. In Notion, create a new database (e.g., "Athlete Metrics")
2. Share it with your integration
3. Get the Database ID from the URL
4. Add it as `NOTION_ATHLETE_METRICS_DATABASE_ID` in GitHub Actions secrets
5. Optionally set `ATHLETE_NAME` secret (default: "Athlete")

**Required Properties:**
- `Name` (Title) - Athlete name (unique key for upsert)
- `Updated At` (Date) - Last update timestamp
- `Load 7d` (Number) - 7-day rolling load total
- `Load 28d` (Number) - 28-day rolling load total
- `Load Balance` (Number) - Ratio of 7d / 28d load
- `Notes` (Text) - Will contain "ETHR intentionally not implemented yet."

**ETHR Fields (Optional - Not Implemented Yet):**
The following fields are **not currently computed** but can exist in your database. They will be left blank:
- `Estimated Threshold HR (bpm)` (Number)
- `ETHR Confidence` (Select)
- `ETHR Sample Count` (Number)
- `Pace @ ETHR (min/mi)` (Number)
- `Pace @ ETHR Confidence` (Select)
- `Pace @ ETHR Sample Count` (Number)

**Note**: ETHR (Estimated Threshold Heart Rate) computation requires careful implementation with confidence guardrails. This is intentionally not implemented yet to avoid providing inaccurate data. The `Notes` field will indicate this.

#### 3. Load Points Formula

Load points are computed from HR zone minutes using a zone-weighted formula:

```
Load = (Z1 minutes × 1) + (Z2 minutes × 2) + (Z3 minutes × 3) + (Z4 minutes × 4) + (Z5 minutes × 5)
```

**Important Notes:**
- Load points are **only computed** when HR zone data exists for an activity
- Missing HR data results in no load points (not guessed or estimated)
- Daily load is the sum of all activity load points for that day
- Rolling loads (7d, 28d) are sums of daily loads within the window

#### 4. Environment Variables

Add these optional secrets to GitHub Actions (or set as env vars for local runs):

| Secret Name | Required | Default | Description |
|------------|----------|---------|-------------|
| `NOTION_DAILY_SUMMARY_DATABASE_ID` | No | - | Database ID for Daily Summary |
| `NOTION_ATHLETE_METRICS_DATABASE_ID` | No | - | Database ID for Athlete Metrics |
| `ATHLETE_NAME` | No | "Athlete" | Name used in Athlete Metrics database |

**If these are not set**, the sync will work normally but will skip Daily Summary and Athlete Metrics syncing.

### Behavior

- **Daily Summary**: Aggregates activities by local date (from `start_date_local` or `start_date`)
- **Athlete Metrics**: Computes rolling loads from Daily Summary data (preferred) or activities (fallback)
- **Idempotent**: Running multiple times updates existing rows; no duplicates
- **Fail-safe**: Errors in optional databases are logged but don't abort the main sync
- **Schema-aware**: All properties are filtered against the database schema

---

## Running the Sync

### Automated (GitHub Actions)

The sync runs automatically **every hour** via GitHub Actions. You can also trigger it manually:

1. Go to your GitHub repository
2. Click the **Actions** tab
3. Select "Sync Strava to Notion" workflow
4. Click "Run workflow" → "Run workflow"

**Schedule:** The workflow runs on cron schedule `0 * * * *` (every hour on the hour). You can modify this in `.github/workflows/sync.yml`.

### Manual (Local)

For testing or one-time backfills:

```bash
cd strava-notion-sync

# Set environment variables
export STRAVA_CLIENT_ID="your_client_id"
export STRAVA_CLIENT_SECRET="your_client_secret"
export STRAVA_REFRESH_TOKEN="your_refresh_token"
export NOTION_TOKEN="your_notion_token"
export NOTION_DATABASE_ID="your_database_id"
export WEATHER_API_KEY="your_weather_key"  # Optional
export NOTION_DAILY_SUMMARY_DATABASE_ID="your_daily_summary_db_id"  # Optional
export NOTION_ATHLETE_METRICS_DATABASE_ID="your_athlete_metrics_db_id"  # Optional
export ATHLETE_NAME="Your Name"  # Optional, default: "Athlete"

# Run the sync
python sync.py
```

Or use a `.env` file (not committed to git):

```bash
# .env file (create in strava-notion-sync/)
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_REFRESH_TOKEN=your_refresh_token
NOTION_TOKEN=your_notion_token
NOTION_DATABASE_ID=your_database_id
WEATHER_API_KEY=your_weather_key
```

Then load it:

```bash
# On macOS/Linux
export $(cat .env | xargs)
python sync.py

# Or use python-dotenv (install: pip install python-dotenv)
# and modify sync.py to load .env files
```

---

## Weekly Status Report (Optional)

The sync script automatically persists run statistics after each execution. A weekly email report can be configured to aggregate and send these statistics.

### What It Is

The weekly status report provides **operational/debug statistics only**:

- Total sync runs in the past week
- Counts: fetched/created/updated/skipped/failed activities
- Daily Summary and Athlete Metrics sync status
- Warning and error summaries
- Top error patterns

**It does not provide training analysis or recommendations**—only system health metrics.

### How to Enable

1. **Create the workflow** (already included in `.github/workflows/weekly-status.yml`)

2. **Add GitHub Secrets** (in your repository settings):

   | Secret Name | Description | Example |
   |-------------|-------------|---------|
   | `SMTP_HOST` | SMTP server hostname | `smtp.gmail.com` |
   | `SMTP_PORT` | SMTP port (usually 587 for TLS) | `587` |
   | `SMTP_USERNAME` | SMTP username (your email) | `your-email@gmail.com` |
   | `SMTP_PASSWORD` | SMTP password or app-specific password | `your-password` |
   | `STATUS_EMAIL_FROM` | Email sender address | `sync@yourdomain.com` |
   | `STATUS_EMAIL_TO` | Email recipient address | `you@example.com` |
   | `STATUS_EMAIL_SUBJECT_PREFIX` | (Optional) Email subject prefix | `[Strava→Notion]` |

3. **Configure SMTP**:

   - **Gmail**: Use an [App Password](https://support.google.com/accounts/answer/185833) (not your regular password)
   - **Other providers**: Use your SMTP credentials

4. **The workflow runs automatically** every Sunday at 8 AM (America/New_York timezone, ~12:00 UTC)

   You can also trigger it manually via GitHub Actions → "Weekly Status Report" → "Run workflow"

### Example Report

```
# Strava → Notion Sync Status Report

**Week ending:** 2024-01-14
**Repository:** strava-to-notion
**Commit:** a1b2c3d

## Run Stats

**Total sync runs in past 7 days:** 168

### Workouts Database

- **Fetched from Strava:** 45
- **Created in Notion:** 5
- **Updated in Notion:** 40
- **Skipped:** 0
- **Failed:** 0

### Daily Summary Database

- **Enabled:** true
- **Days processed:** 7
- **Failed upserts:** 0

### Athlete Metrics Database

- **Enabled:** true
- **Successful upserts:** 168
- **Failed upserts:** 0

## Warnings

**Total warnings:** 0

## Errors

**Total errors:** 0

**Top error patterns:**

  * None
```

### Running the Report Locally

To generate the report locally (without sending email):

```bash
cd strava-notion-sync
python scripts/weekly_status_report.py
```

This creates:
- `weekly_status.md` (markdown report)
- `weekly_status.json` (structured data)

The report is generated from `stats/run_stats.json`, which is automatically created after each sync run.

### Report Artifacts

Even if email sending fails, the workflow uploads the report as a GitHub Actions artifact named `weekly-status-report`, available for download from the workflow run page.

---

## Sync Behavior

### Idempotency

**The sync is idempotent** - running it multiple times is safe and will not create duplicates.

- Uses Strava Activity ID as the unique key
- Checks for existing activities before creating new ones
- Updates existing rows instead of creating duplicates
- Safe to re-run if a run fails partway through

### Update Rules

**System fields** (synced from Strava) are always updated:
- Activity name, date, sport type
- Metrics (duration, distance, elevation, HR, pace)
- URLs, sync timestamps
- Weather data (updated on each run)
- HR zones, drift metrics (recalculated each run)

**User fields** (your custom data) are **never overwritten**:
- Notes, reflections, comments
- Custom properties you add
- Ratings, tags, or other metadata you enter

The sync **only writes to properties it knows about** - it won't touch properties that don't exist in the schema.

### Sync Process

Each sync run:

1. **Refreshes Strava access token** (using refresh token)
2. **Fetches recent activities** from Strava (default: last 30 days)
3. **Queries Notion** for existing activities in that date range
4. **For each activity:**
   - Checks if it exists in Notion (by Activity ID)
   - Fetches HR streams if needed (for zones/drift)
   - Fetches weather data if outdoor activity
   - Updates existing row OR creates new row
5. **Logs summary**: fetched, created, updated, failed counts
6. **Exits with error** if failure rate > 20% (configurable)

### Failure Handling

- **Individual activity failures** are logged but don't stop the sync
- **API rate limits** are handled with exponential backoff and retries
- **Network errors** are retried up to 3 times
- **Schema mismatches** (missing properties) are logged and skipped
- **If >20% of activities fail**, the sync exits with error code 1

---

## Troubleshooting

### Common Issues

#### "Failed to initialize Strava client: 'access_token'"

**Problem:** Invalid or expired refresh token.

**Solution:**
1. Verify your `STRAVA_REFRESH_TOKEN` secret is correct in GitHub Actions
2. Check that you used the full OAuth scope: `read,activity:read_all,profile:read_all`
3. Regenerate a new refresh token using the authorization flow in [Setup Guide](#step-1-strava-api-setup)

#### "No weather data returned for activity"

**Problem:** Weather API failed or activity has no location data.

**Solutions:**
- Check that the activity has GPS data (start_latitude, start_longitude)
- If using WeatherAPI.com, verify your API key is valid
- Recent activities (last 2 days) may not have data yet with Open-Meteo fallback
- Check logs for specific error messages

#### "Properties filtered out (not in schema)"

**Problem:** Property names don't match exactly.

**Solution:**
1. Check the property name in Notion (case-sensitive, special characters matter)
2. Compare with `NOTION_PROPERTIES.md`
3. Rename the property in Notion to match exactly (copy/paste recommended)

#### "Error querying Notion database"

**Problem:** Notion API access issue.

**Solutions:**
- Verify `NOTION_TOKEN` is correct
- Verify `NOTION_DATABASE_ID` is correct (32-character hex)
- Check that the database is shared with your integration
- Verify the integration has "Read content" and "Update content" capabilities

#### Duplicate activities created

**Problem:** Activity ID lookup failed.

**Solutions:**
- This is rare, but can happen if the batch query fails
- The script has fallback per-activity lookup, but edge cases exist
- If duplicates occur, manually delete them in Notion - future runs won't recreate them
- Check logs for "Error querying Notion database" warnings

#### HR zones not appearing

**Problem:** Missing HR data or zones not configured.

**Solutions:**
- Verify HR zones are set up in Strava (Settings → Heart Rate & Power Zones)
- Check that the activity has heart rate data in Strava
- Ensure you used the full OAuth scope including `profile:read_all`
- Check logs for "Strava HR zones not available" messages

#### Activities not syncing

**Problem:** Date range or pagination issue.

**Solutions:**
- Check the default sync window (30 days) - older activities won't sync
- Verify activities exist in Strava for the date range
- Check logs for "Fetched page X: Y activities" to see if activities are being retrieved
- For older activities, temporarily increase the `days` parameter in `sync.py` and run locally

### Debug Mode

To see more detailed logs, modify `sync.py`:

```python
# Change logging level from INFO to DEBUG
logging.basicConfig(
    level=logging.DEBUG,  # Changed from INFO
    format='%(asctime)s - %(levelname)s - %(message)s'
)
```

This will show detailed step-by-step information for each activity.

### Checking Logs

**GitHub Actions:**
1. Go to repository → Actions tab
2. Click on a workflow run
3. Click on the "Run sync script" step to see logs

**Local:**
- Logs print to stdout
- Redirect to file: `python sync.py > sync.log 2>&1`

### Validating Setup

After setup, verify:

1. ✅ GitHub Actions workflow runs without errors
2. ✅ Activities appear in Notion
3. ✅ Required properties are populated
4. ✅ No duplicate activities
5. ✅ Re-running doesn't create duplicates
6. ✅ Activity updates in Strava appear in Notion on next sync

---

## Project Files Explained

### Core Files

**`sync.py`**
- Main sync script (entry point)
- Contains all sync logic, API clients, and data transformation
- Run with: `python sync.py`
- Single-file design for simplicity (no complex module structure)

**`requirements.txt`**
- Python package dependencies
- Install with: `pip install -r requirements.txt`
- Only includes runtime dependencies (no dev tools)

### Documentation

**`README.md`** (this file)
- Complete user guide and documentation
- Installation, setup, troubleshooting
- Human-readable reference

**`NOTION_PROPERTIES.md`**
- Complete list of all Notion property names
- Used as reference when setting up your database
- Property names are centralized in code (see `NOTION_SCHEMA` in `sync.py`)

**`README_DEVELOPMENT.md`**
- Guide for developers contributing to the project
- Testing, linting, code quality checks
- Only relevant if you're modifying the code

### Configuration

**`.github/workflows/sync.yml`**
- GitHub Actions workflow definition
- Defines when/how the sync runs automatically
- Currently set to run hourly (`0 * * * *`)
- Can be modified to change schedule or add steps

**`pyproject.toml`**
- Ruff configuration (linting and code formatting)
- Used by developers for code quality
- Not needed for running the sync

**`.gitignore`**
- Tells git which files to ignore
- Prevents committing `__pycache__/`, `.env`, IDE files, etc.

### Scripts

**`scripts/` directory**
- `weekly_status_report.py` - Generates weekly status report from run stats
- `send_status_email.py` - Sends status report via SMTP

### Tests

**`tests/` directory**
- Unit tests for critical logic
- `test_unit_conversions.py` - Validates unit conversion constants
- `test_schema.py` - Validates Notion schema constants
- `test_hr_zones.py` - Validates HR zone calculation logic
- `test_weekly_report.py` - Validates weekly report aggregation logic
- `test_schema.py` - Validates schema constants
- `test_hr_zones.py` - Tests HR zone calculation logic
- Run with: `pytest tests/`

---

## Design Principles

This project follows these principles:

- **Stability over completeness** - Reliable sync of core metrics, not everything possible
- **Derived metrics, not raw streams** - Summary data only (e.g., HR zone minutes, not second-by-second HR)
- **Clear ownership** - System fields vs user fields are well-defined
- **Notion as thinking layer** - Not a data warehouse, but a place for reflection
- **Explicit over implicit** - Clear behavior, no magic
- **Idempotent** - Safe to re-run
- **Observable** - Good logging and error messages

**This is meant to fade into the background and support better decisions over time.**

---

## Support

If you encounter issues:

1. Check the [Troubleshooting](#troubleshooting) section above
2. Review GitHub Actions logs for error messages
3. Verify all secrets are set correctly
4. Ensure your Notion database schema matches exactly
5. Check that your Strava OAuth scope includes all required permissions

For code issues or feature requests, please open an issue in the repository.

---

## License

This project is provided as-is for personal use. Modify and use as you see fit.
