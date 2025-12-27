# Ship Readiness Summary

## Changes Made

### 1. Stats Persistence ✅

**File:** `strava-notion-sync/sync.py`

- Added stats persistence after each sync run
- Writes to `stats/run_stats.json` (git-ignored)
- Stores: workouts stats, daily summary stats, athlete metrics stats, warnings, errors
- Automatically prunes entries older than 30 days
- Non-fatal: failures to persist don't abort the sync

### 2. Weekly Status Report Script ✅

**File:** `strava-notion-sync/scripts/weekly_status_report.py`

- Reads from `stats/run_stats.json`
- Aggregates stats from last 7 days
- Generates markdown report (`weekly_status.md`)
- Generates JSON report (`weekly_status.json`)
- Includes error fingerprinting (top error patterns)

### 3. Email Sender Script ✅

**File:** `strava-notion-sync/scripts/send_status_email.py`

- Sends weekly report via SMTP
- Uses environment variables for configuration
- Basic markdown-to-HTML conversion
- Plain text and HTML versions

### 4. GitHub Actions Workflow ✅

**File:** `.github/workflows/weekly-status.yml`

- Runs every Sunday at 12:00 UTC (~8 AM America/New_York)
- Can be triggered manually (`workflow_dispatch`)
- Steps:
  1. Checkout repository
  2. Setup Python 3.11
  3. Generate weekly status report
  4. Send email (non-fatal if fails)
  5. Upload report artifacts

### 5. Unit Tests ✅

**File:** `strava-notion-sync/tests/test_weekly_report.py`

- Tests for aggregation logic
- Tests for filtering by date range
- Tests for error fingerprint formatting
- Tests for empty stats handling

### 6. Documentation Updates ✅

**File:** `strava-notion-sync/README.md`

- Added "Weekly Status Report (Optional)" section
- Includes:
  - What it is and what it's not
  - Setup instructions
  - Required secrets list
  - Example report
  - How to run locally
- Updated repository structure diagram
- Updated project files explanation

### 7. Git Ignore Updates ✅

**File:** `.gitignore`

- Added `run_stats.json`
- Added `weekly_status.md`
- Added `weekly_status.json`
- Added `stats/` directory

## Repository Tree Changes

```
strava-to-notion/
├── .github/
│   └── workflows/
│       ├── sync.yml              # (existing)
│       └── weekly-status.yml     # (new)
├── strava-notion-sync/
│   ├── scripts/                  # (new)
│   │   ├── weekly_status_report.py
│   │   └── send_status_email.py
│   ├── tests/
│   │   └── test_weekly_report.py  # (new)
│   └── stats/                    # (created at runtime, git-ignored)
│       └── run_stats.json        # (created at runtime)
├── .gitignore                    # (updated)
└── strava-notion-sync/README.md  # (updated)
```

## Required GitHub Secrets

To enable weekly email reports, add these secrets to your repository:

| Secret Name | Required | Description |
|-------------|----------|-------------|
| `SMTP_HOST` | Yes | SMTP server hostname |
| `SMTP_PORT` | Yes | SMTP port (usually 587) |
| `SMTP_USERNAME` | Yes | SMTP username (your email) |
| `SMTP_PASSWORD` | Yes | SMTP password or app password |
| `STATUS_EMAIL_FROM` | Yes | Sender email address |
| `STATUS_EMAIL_TO` | Yes | Recipient email address |
| `STATUS_EMAIL_SUBJECT_PREFIX` | No | Email subject prefix (default: `[Strava→Notion]`) |

## Commands to Run

### Quality Gates (in CI)

```bash
# Linting
ruff check strava-notion-sync/

# Formatting check
ruff format --check strava-notion-sync/

# Tests
pytest strava-notion-sync/tests/ -v
```

### Generate Report Locally

```bash
cd strava-notion-sync
python scripts/weekly_status_report.py
```

This creates:
- `weekly_status.md`
- `weekly_status.json`

### Run Tests

```bash
cd strava-notion-sync
pytest tests/ -v
```

## Workflow Behavior

1. **Main Sync Workflow** (`.github/workflows/sync.yml`):
   - Runs hourly
   - Executes `sync.py`
   - Persists stats to `stats/run_stats.json`

2. **Weekly Status Workflow** (`.github/workflows/weekly-status.yml`):
   - Runs every Sunday at 12:00 UTC
   - Generates report from `stats/run_stats.json`
   - Sends email (non-fatal if fails)
   - Uploads artifacts

## Remaining Risks / Edge Cases

1. **Stats file location**: Currently stored relative to `sync.py`. In GitHub Actions, this is fine, but if running locally from a different directory, path resolution might differ.

2. **Error tracking**: Currently only tracks errors from optional DB syncs. Main sync errors are logged but not systematically collected in stats. This could be enhanced in the future.

3. **Email delivery**: If SMTP fails, the workflow continues (non-fatal) but the user must check artifacts manually.

4. **Timezone handling**: Weekly report uses UTC for "today". This is consistent but may not match user's local timezone for "week ending" date.

5. **Stats file size**: Stats are pruned to last 30 days, but with hourly runs this could still be ~720 entries. JSON file size should remain manageable (<100KB typically).

6. **Markdown-to-HTML conversion**: The email script uses a basic converter. Complex markdown may not render perfectly in email.

## Testing Checklist

- [ ] Run sync locally - verify `stats/run_stats.json` is created
- [ ] Run `weekly_status_report.py` locally - verify report generation
- [ ] Test email sending locally (with test SMTP credentials)
- [ ] Run tests: `pytest tests/test_weekly_report.py`
- [ ] Trigger weekly workflow manually in GitHub Actions
- [ ] Verify email is received (if secrets configured)
- [ ] Verify artifacts are uploaded

## Notes

- All changes are backward-compatible
- Weekly report is completely optional (workflow can be disabled)
- Stats persistence is non-fatal (sync continues even if stats write fails)
- No new dependencies required (uses stdlib: json, smtplib, email)


