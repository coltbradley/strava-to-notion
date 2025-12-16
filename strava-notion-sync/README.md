# Strava → Notion Workout Sync

Automated pipeline that syncs recent Strava activities into a Notion database for AI-powered training insights.

## Features

- ✅ OAuth refresh token flow for Strava (no manual token renewal)
- ✅ Upsert logic (creates new activities or updates existing ones)
- ✅ Idempotent syncs (no duplicates)
- ✅ Batch query optimization for efficiency
- ✅ Comprehensive error handling and logging
- ✅ Runs unattended via GitHub Actions (daily schedule + manual trigger)
- ✅ Unit conversions (meters → miles/feet, pace calculations)
- ✅ Graceful handling of missing optional Notion properties

## What This Does (Plain English)

- Pulls your recent Strava activities (default: last 30 days).
- For each activity, it either **creates** a new row in your Notion “Workouts” database or **updates** the existing one that matches the Strava Activity ID.
- It writes only the “system fields” (metrics and links). Your own notes/reflections in Notion stay untouched.
- No second-by-second GPS/HR stream data—only high-level activity metrics.
- Runs on a schedule via GitHub Actions, so no manual MFA or logins after setup.

### Data that moves from Strava → Notion
- Activity ID, name, date, sport type
- Duration (elapsed time), moving time, distance (converted to miles), elevation gain (converted to feet)
- Average/max heart rate (if present), pace for running/ walking/ hiking (if distance > 0)
- Strava activity URL
- Last synced timestamp, basic sync status (created/updated) if you add that property

## Setup

### 1. Strava API Setup

1. Go to https://www.strava.com/settings/api
2. Click "Create App" or use an existing application
3. Fill in:
   - **Application Name**: e.g., "Notion Sync"
   - **Category**: Analytics
   - **Website**: Your website (can be placeholder)
   - **Authorization Callback Domain**: `localhost` (for local testing)
4. Note your **Client ID** and **Client Secret**

#### Obtaining a Refresh Token

To get a refresh token, you need to complete the OAuth flow:

1. Visit this URL (replace `YOUR_CLIENT_ID` with your actual Client ID):
   ```
   https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=activity:read_all
   ```
2. Authorize the application
3. You'll be redirected to `http://localhost/?code=...`
4. Copy the `code` parameter from the URL
5. Exchange the code for tokens using curl or Postman:
   ```bash
   curl -X POST https://www.strava.com/oauth/token \
     -d client_id=YOUR_CLIENT_ID \
     -d client_secret=YOUR_CLIENT_SECRET \
     -d code=CODE_FROM_URL \
     -d grant_type=authorization_code
   ```
6. Save the `refresh_token` from the response (this is what you'll use in the sync script)

**Note**: The refresh token doesn't expire, but access tokens do. The script automatically refreshes access tokens using your refresh token.

### 2. Notion API Setup

1. Go to https://www.notion.so/my-integrations
2. Click "+ New integration"
3. Give it a name (e.g., "Strava Sync")
4. Select your workspace
5. Click "Submit" and copy the **Internal Integration Token** (this is your `NOTION_TOKEN`)

#### Creating the Notion Database

1. Create a new database in Notion called "Workouts"
2. Add the following properties (exact names matter):

   **Required Properties:**
   - `Name` (Title)
   - `Activity ID` (Rich text) - This is the unique key
   - `Date` (Date)
   - `Sport` (Select) - Options: Run, Ride, Walk, Hike, etc.
   - `Duration (min)` (Number)
   - `Distance (mi)` (Number)
   - `Elevation (ft)` (Number)

   **Optional Properties** (add if you want them):
   - `Avg HR` (Number)
   - `Max HR` (Number)
   - `Avg Pace (min/mi)` (Number)
   - `Moving Time (min)` (Number)
   - `Strava URL` (URL)
   - `Last Synced` (Date)
   - `Sync Status` (Select) - Options: created, updated

3. **Share the database with your integration:**
   - Open the database
   - Click "..." menu → "Connections"
   - Find your integration and connect it

4. **Get the Database ID:**
   - Open the database in Notion
   - The URL looks like: `https://www.notion.so/workspace/DATABASE_ID?v=...`
   - Copy the `DATABASE_ID` (the 32-character hex string between the workspace name and the `?`)

### 3. Local Development

1. **Create a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies:**
   ```bash
   cd strava-notion-sync
   pip install -r requirements.txt
   ```

3. **Set environment variables:**
   ```bash
   export STRAVA_CLIENT_ID="your_client_id"
   export STRAVA_CLIENT_SECRET="your_client_secret"
   export STRAVA_REFRESH_TOKEN="your_refresh_token"
   export NOTION_TOKEN="your_notion_token"
   export NOTION_DATABASE_ID="your_database_id"
   ```

   Or create a `.env` file (don't commit it!) and use `python-dotenv`:
   ```bash
   pip install python-dotenv
   ```
   Then load it in your script or use `export $(cat .env | xargs)`

4. **Run the sync script:**
   ```bash
   python sync.py
   ```

### 3.5 Secrets You Need (and where to get them)

| Secret name | Where to get it | What it is |
| --- | --- | --- |
| `STRAVA_CLIENT_ID` | Strava settings → API → your app | The numeric client ID of your Strava app |
| `STRAVA_CLIENT_SECRET` | Strava settings → API → your app | The client secret of your Strava app |
| `STRAVA_REFRESH_TOKEN` | Exchange auth code at `https://www.strava.com/oauth/token` (see steps above) | Long-lived refresh token used to get access tokens automatically |
| `NOTION_TOKEN` | Notion → My Integrations → your integration → Internal Integration Token | Auth token for the Notion API |
| `NOTION_DATABASE_ID` | In your Notion database URL (32-char hex before the `?`) | The ID of your “Workouts” database |

### 4. GitHub Actions Setup

1. **Add GitHub Secrets:**
   - Go to your repository → Settings → Secrets and variables → Actions
   - Click "New repository secret" and add:
     - `STRAVA_CLIENT_ID`
     - `STRAVA_CLIENT_SECRET`
     - `STRAVA_REFRESH_TOKEN`
     - `NOTION_TOKEN`
     - `NOTION_DATABASE_ID`

2. **Verify the workflow:**
   - Go to Actions tab in your repository
   - The workflow runs daily at 6 AM UTC
   - You can also trigger it manually via "Run workflow"

## How It Works

1. **Token Refresh**: The script automatically refreshes your Strava access token using the refresh token
2. **Fetch Activities**: Retrieves activities from Strava for the last 30 days (configurable)
3. **Batch Query**: Queries Notion once to get existing activities in the date range
4. **Upsert Logic**: For each Strava activity:
   - If Activity ID exists in Notion → Update the page
   - If not → Create a new page
5. **Error Handling**: Continues processing even if individual activities fail, but aborts if failure rate exceeds 20%

## Logging

The script provides structured logging:
- `Fetched from Strava`: Total activities retrieved
- `Created in Notion`: New pages created
- `Updated in Notion`: Existing pages updated
- `Failed`: Activities that couldn't be synced

## Troubleshooting

### "Failed to refresh Strava access token"
- Your refresh token may have been revoked
- Regenerate tokens using the OAuth flow (see Step 1)

### "Property doesn't exist" warnings
- The script skips properties that don't exist in your Notion database
- This is expected if you haven't added optional properties
- Check the logs to see which properties were skipped

### "Failed to initialize Notion client"
- Verify your `NOTION_TOKEN` is correct
- Ensure the integration is connected to your database

### Activities not syncing
- Check that your Notion database has the required properties with exact names
- Verify the database is shared with your integration
- Check the Activity logs in GitHub Actions for detailed error messages

## Notes

- **Idempotency**: Running the sync multiple times won't create duplicates. Activities are keyed by Strava Activity ID.
- **User Data Protection**: The script only updates system fields. User-entered fields (like reflection notes) are preserved.
- **Rate Limiting**: The script includes small delays to respect Notion API rate limits.
- **Date Range**: By default, syncs last 30 days. Adjust the `days` parameter in `sync.py` if needed.

## Non-Goals (V1)

- Webhooks / real-time syncing
- Second-by-second GPS/HR streams
- Advanced analytics (CTL/ATL/TSB, HRV trends)
- Auto-creating Notion database schema
