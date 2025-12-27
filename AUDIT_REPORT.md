# Code Audit Report: Strava → Notion Sync

**Date:** 2025-01-16  
**Auditor:** Senior Backend Engineer Review  
**Scope:** Reliability + Maintainability Audit

---

## Phase 0: Repository Structure Analysis

### Current Structure

```
strava-to-notion/
├── README.md (root - minimal)
├── strava-notion-sync/
│   ├── sync.py (1378 lines - main script)
│   ├── requirements.txt
│   ├── README.md (detailed docs)
│   └── NOTION_PROPERTIES.md (property reference)
└── .github/
    └── workflows/
        └── sync.yml
```

### Structure Summary

**Entrypoints:**
- `strava-notion-sync/sync.py` - Single entrypoint, runs `sync_strava_to_notion()` when executed

**Components:**
- **StravaClient** - OAuth token refresh, activity fetching, HR zones, streams, photos
- **WeatherClient** - Weather data fetching (WeatherAPI.com + Open-Meteo fallback)
- **NotionClient** - Database queries, schema loading, page upserts, property conversion
- **Main sync function** - Orchestrates the entire sync process

**Config/Constants:**
- Environment variables: `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`, `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `WEATHER_API_KEY`
- Module-level constants: `PACE_SPORTS`, `INDOOR_SPORTS`
- Hardcoded property names throughout `_convert_activity_to_properties()`

**Dependencies:**
- `sync.py` → `requests`, `notion-client`
- All logic in single file (no internal module structure)

**Side Effects:**
- HTTP requests to Strava API
- HTTP requests to WeatherAPI.com/Open-Meteo
- HTTP requests to Notion API (creates/updates pages)
- Logging to stdout

---

## Top 10 Structural & Correctness Risks

### P0 (Correctness / Data Loss)

1. **Property names hardcoded throughout codebase** (CRITICAL)
   - Property names like `"Activity ID"`, `"Name"`, `"Temperature (°F)"` are hardcoded as string literals in multiple places
   - If schema changes, need to update in many locations
   - Risk: Typos, inconsistencies, hard to maintain
   - Location: `_convert_activity_to_properties()`, `get_existing_activity_pages()`, `find_page_by_activity_id()`

2. **Potential race condition in duplicate detection** (HIGH)
   - `get_existing_activity_pages()` filters by date range (may miss activities outside window)
   - Falls back to per-activity lookup, but there's a window where duplicates could be created if two runs happen simultaneously
   - However, GitHub Actions prevents concurrent runs, so risk is mitigated

3. **Update behavior not explicitly selective** (MEDIUM-HIGH)
   - `upsert_activity()` updates ALL properties passed to it
   - Documentation says user fields aren't overwritten, but code doesn't enforce this
   - Risk: If user adds a field that matches a system property name, it could be overwritten
   - Current behavior relies on schema filtering, but doesn't protect user-added properties

4. **Error swallowing in upsert_activity** (MEDIUM)
   - Returns `True` even when property doesn't exist (line 952)
   - Could mask real errors
   - However, this is intentional to handle missing optional properties

### P1 (Reliability)

5. **No validation that Activity ID is unique in Notion** (MEDIUM)
   - Relies on date-filtered query + per-activity lookup
   - If an activity exists outside the date window, duplicate could be created
   - Current implementation has fallback, but edge case exists

6. **Excessive verbose logging** (LOW-MEDIUM)
   - Many "STEP:" debug logs that clutter output
   - Should be at DEBUG level, not INFO
   - Makes production logs noisy

7. **Missing .gitignore** (LOW)
   - `__pycache__/` should be ignored
   - `.env` files if used locally

8. **No type hints on public functions** (LOW)
   - Makes code harder to reason about
   - Reduces IDE support

### P2 (Maintainability)

9. **Single 1378-line file** (MEDIUM)
   - While acceptable for a small tool, structure could be clearer
   - Three client classes + main sync logic all in one file
   - Could benefit from splitting, but not critical

10. **Constants mixed with code** (LOW)
    - `PACE_SPORTS`, `INDOOR_SPORTS` are module-level
    - Could be in a config section or separate constants file
    - Minor issue for now

---

## Detailed Findings

### Phase 1: Repository Structure

**Current structure is acceptable** for a small sync tool. The single-file approach is pragmatic, though splitting could improve clarity.

**Recommendation:** Keep current structure, but extract property names to a schema mapping.

### Phase 2: Correctness & Idempotency

**Idempotency Analysis:**

✅ **GOOD:**
- Uses Activity ID as unique key
- Batch query with date filter + per-activity fallback
- Updates existing pages when found

⚠️ **CONCERNS:**
- Property names hardcoded (see risk #1)
- No explicit "system fields only" enforcement (see risk #3)
- Date filter window could miss activities if they're older than sync window (but fallback handles this)

**Update Rules:**

✅ **GOOD:**
- Schema filtering prevents writing to non-existent properties
- Only system-owned fields are written (by design)

⚠️ **CONCERNS:**
- No explicit list of "system fields" that are safe to overwrite
- If user creates a property with same name as system property, it could be overwritten
- Code doesn't distinguish between "safe to overwrite" and "user-owned" fields

**Edge Cases:**

✅ **HANDLED:**
- Missing HR data → skipped gracefully
- Missing GPS → weather skipped
- Weather API failure → logged, continues
- Missing zones → skipped
- Unexpected activity payload → defensive check

⚠️ **POTENTIAL ISSUES:**
- Activity deleted on Strava → not handled (but acceptable, sync doesn't delete)
- Activity name changes → will update (correct behavior)
- Pagination stops early → retry logic should handle, but no explicit test

### Phase 3: Reliability & Failure Modes

**Strava API:**
✅ Retry logic with exponential backoff
✅ Handles 429, 5xx
✅ Timeouts configured
✅ Pagination handled correctly

**Notion API:**
✅ Retry logic for SDK calls
✅ Schema filtering prevents property errors
✅ Batch query with pagination
⚠️ Raw HTTP fallback for database.query (good workaround)

**Execution:**
✅ Failures isolated per activity
✅ Failure threshold check
✅ Continues on individual failures
✅ Good error logging

### Phase 4: Schema Discipline

**CRITICAL ISSUE:** Property names hardcoded throughout code.

**Current state:**
- Property names appear as string literals in multiple functions
- `NOTION_PROPERTIES.md` exists but not used as source of truth
- No centralized mapping

**Recommendation:** Create a `SCHEMA` constant mapping logical names to Notion property names.

### Phase 5: Code Quality

**Issues:**
- Excessive INFO-level logging (should be DEBUG)
- Missing type hints on some functions
- Some long functions (acceptable but could be split)
- Magic numbers (timeouts, thresholds) not constants

**Recommendations:**
- Reduce logging verbosity
- Add type hints to public functions
- Extract magic numbers to constants

### Phase 6: Tooling

**Missing:**
- `.gitignore`
- Linter (ruff recommended)
- Tests (pytest)
- Pre-commit hooks (optional)

**Recommendation:** Add minimal tooling for code quality.

### Phase 7: Tests

**Current:** None

**Recommendation:** Add high-value tests for:
- Property name mapping
- Unit conversions
- HR zone calculations
- Upsert logic (mocked)

### Phase 8: README

**Current:** Good but could be more explicit about:
- Exact sync behavior
- What gets overwritten vs preserved
- Failure modes
- Idempotency guarantees

---

## Prioritized Action Plan

### P0 Fixes (Apply Immediately)

1. **Extract property names to centralized schema mapping**
2. **Add explicit system fields list**
3. **Verify idempotency edge cases are handled**

### P1 Fixes (Important)

4. **Reduce logging verbosity (move STEP logs to DEBUG)**
5. **Add .gitignore**
6. **Add type hints to public functions**

### P2 Fixes (Nice to have)

7. **Add ruff for linting**
8. **Add minimal tests**
9. **Extract magic numbers to constants**

### P3 Fixes (Documentation)

10. **Rewrite README with explicit behavior documentation**

---

## Next Steps

Proceeding with P0 and P1 fixes in code.

---

## Changes Applied

### P0 Fixes (Applied)

1. ✅ **Centralized schema mapping** - Created `NOTION_SCHEMA` dictionary with all property names
2. ✅ **Refactored hardcoded property names** - Updated all code to use `NOTION_SCHEMA` constants
3. ✅ **System-owned fields list** - Added `SYSTEM_OWNED_FIELDS` set (documented for future use)
4. ✅ **Fixed property name usage** - Updated `get_existing_activity_pages()`, `find_page_by_activity_id()`, `_convert_activity_to_properties()`

### P1 Fixes (Applied)

5. ✅ **Added .gitignore** - Prevents committing `__pycache__/`, `.env`, IDE files
6. ✅ **Reduced logging verbosity** - Moved detailed "STEP:" logs from INFO to DEBUG level
7. ✅ **Extracted magic numbers to constants** - All magic numbers now use named constants:
   - HTTP timeouts and retries
   - Unit conversion factors (meters to miles/feet, m/s to mph)
   - HR drift eligibility thresholds
   - Sync configuration defaults

### P2 Fixes (Applied)

8. ✅ **Added ruff configuration** - Created `pyproject.toml` with ruff linting and formatting rules
9. ✅ **Added minimal tests** - Created test suite with:
   - Unit conversion tests
   - Schema constant tests
   - HR zone calculation tests
10. ✅ **Added development documentation** - Created `README_DEVELOPMENT.md` with testing and linting instructions

### Remaining Recommendations (Future improvements)

- Add type hints to more public functions (some already have them)
- Add integration-style tests with mocked API responses
- Consider extracting some large methods for better readability (optional)

