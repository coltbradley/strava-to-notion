# Ship Readiness Baseline

## Repository Structure

```
strava-to-notion/
├── .github/
│   └── workflows/
│       └── sync.yml              # Main sync workflow (hourly)
├── strava-notion-sync/
│   ├── sync.py                   # Main entrypoint (2022 lines)
│   ├── requirements.txt          # Dependencies (requests, notion-client)
│   ├── pyproject.toml            # Ruff linting/formatting config
│   ├── README.md                 # Main user documentation
│   ├── README_DEVELOPMENT.md     # Dev guide (testing, linting)
│   ├── NOTION_PROPERTIES.md      # Property reference
│   └── tests/
│       ├── __init__.py
│       ├── test_hr_zones.py      # HR zone calculation tests
│       ├── test_schema.py        # Schema constant tests
│       └── test_unit_conversions.py  # Unit conversion tests
├── .gitignore                    # Python, IDE, env files
└── [Various .md files]           # Documentation files
```

## Entrypoints

- **Main**: `strava-notion-sync/sync.py` (runs via `python sync.py`)
- **No CLI arguments**: Simple script, uses env vars for config

## Test Setup

- **Framework**: pytest (mentioned in requirements comments)
- **Tests**: 3 test files in `tests/` directory
- **Coverage**: Unit conversions, schema constants, HR zones
- **No integration tests**: No mocked API calls visible

## Lint/Format Config

- **Ruff**: Configured in `pyproject.toml`
  - Linting: E, W, F, I, B, C4 rules
  - Formatting: double quotes, 100 char line length
  - Python 3.11 target
- **No mypy**: Not configured
- **No pre-commit**: Not configured

## Workflows

- **`.github/workflows/sync.yml`**:
  - Runs hourly (`0 * * * *`)
  - Manual trigger (`workflow_dispatch`)
  - Steps: checkout → setup Python 3.11 → install deps → run sync.py

## README Status

- **Main README**: `strava-notion-sync/README.md` (718 lines)
  - Comprehensive setup guide
  - Database schema documentation
  - Troubleshooting section
- **No .env.example**: Environment variables documented in README only

## Top 10 Risks (Before Quality Gates)

1. **No test persistence**: Stats/logs only printed, not persisted for reporting
2. **Error tracking**: Errors logged but not aggregated/analyzed
3. **No status reporting**: No weekly email or status aggregation
4. **Schema cache**: Class-level cache could leak between tests (not thread-safe)
5. **Timezone handling**: Rolling loads use UTC "today" vs local dates from Strava
6. **Load computation**: Must verify cardio + HR quality gating is correct
7. **Rate limiting**: Small delay (0.1s) may not be sufficient under load
8. **Missing property warnings**: Logged but not counted/aggregated
9. **Optional DB failures**: Logged as warnings but no aggregate stats
10. **No dry-run mode**: Cannot test sync without writing to Notion

