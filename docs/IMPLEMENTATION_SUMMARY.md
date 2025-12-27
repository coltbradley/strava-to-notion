# Implementation Summary: Daily Summary & Athlete Metrics

## Overview

Successfully implemented two optional Notion databases (Daily Summary and Athlete Metrics) with zone-weighted load points computation, while maintaining full backward compatibility with the existing sync system.

## Key Changes

### 1. Multi-Database Schema Caching

**Refactored `NotionClient` schema management:**
- Created `NotionSchemaCache` class for shared schema caching across multiple databases
- Each database's schema is cached independently (keyed by `database_id`)
- Schema is loaded lazily on first access
- Prevents schema mismatches and enables safe writes to multiple databases

### 2. Zone-Weighted Load Points

**Implemented load computation:**
- Added `compute_zone_weighted_load_points()` function
- Formula: `Load = Z1×1 + Z2×2 + Z3×3 + Z4×4 + Z5×5`
- Only computed when HR zone minutes exist (no guessing for missing data)
- Computed per-activity during sync and stored as `_load_pts`

### 3. Daily Summary Database (Optional)

**Features:**
- One row per local date with aggregated metrics
- Aggregates: duration, moving time, distance, elevation, session count
- Sums load points per day
- Computes Load Confidence (High/Medium/Low based on HR zone coverage)
- Idempotent upsert by Date

**Properties:**
- `Date` (Date) - Unique key
- `Total Duration (min)` (Number)
- `Total Moving Time (min)` (Number)
- `Total Distance (mi)` (Number)
- `Total Elevation (ft)` (Number)
- `Session Count` (Number)
- `Load (pts)` (Number)
- `Load Confidence` (Select: High/Medium/Low)
- `Notes` (Text)

### 4. Athlete Metrics Database (Optional)

**Features:**
- Single row per athlete (keyed by `ATHLETE_NAME`)
- Rolling 7-day and 28-day load totals
- Load Balance (7d / 28d ratio)
- ETHR fields left blank with note "ETHR intentionally not implemented yet"

**Properties:**
- `Name` (Title) - Unique key
- `Updated At` (Date)
- `Load 7d` (Number)
- `Load 28d` (Number)
- `Load Balance` (Number)
- `Notes` (Text)
- ETHR fields (optional, not implemented)

### 5. Helper Functions Added

- `get_activity_local_date()` - Extract local date from activity (prefers `start_date_local`, falls back to `start_date`)
- `compute_zone_weighted_load_points()` - Compute load from HR zone minutes
- `aggregate_daily_summaries()` - Group activities by date and aggregate metrics
- `compute_rolling_loads()` - Compute 7d/28d rolling totals from daily summaries

### 6. Integration into Main Sync

**Flow:**
1. Activities are synced to Workouts database (as before)
2. Load points computed for each activity (if HR zones exist)
3. Daily summaries aggregated (if Daily Summary DB enabled)
4. Daily Summary rows upserted (if DB enabled)
5. Rolling loads computed from daily summaries
6. Athlete Metrics row upserted (if DB enabled)

**Error Handling:**
- Failures in optional databases are logged but don't abort main sync
- Schema filtering prevents crashes from missing properties
- All writes are idempotent

## New Environment Variables

**Optional:**
- `NOTION_DAILY_SUMMARY_DATABASE_ID` - Daily Summary database ID
- `NOTION_ATHLETE_METRICS_DATABASE_ID` - Athlete Metrics database ID
- `ATHLETE_NAME` - Name for athlete row (default: "Athlete")

## Backward Compatibility

✅ **Fully backward compatible:**
- Original sync works exactly as before if optional env vars are not set
- No breaking changes to existing functionality
- All new code is opt-in via environment variables
- Schema constants added but existing code unchanged

## Files Modified

1. **`sync.py`**:
   - Added schema constants for Daily Summary and Athlete Metrics
   - Refactored schema caching to support multiple databases
   - Added helper functions for date extraction, load computation, aggregation
   - Added `upsert_daily_summary()` and `upsert_athlete_metrics()` methods to `NotionClient`
   - Integrated optional syncs into main `sync_strava_to_notion()` function

2. **`.github/workflows/sync.yml`**:
   - Added optional environment variables for Daily Summary and Athlete Metrics

3. **`README.md`**:
   - Added comprehensive section on Daily Summary + Athlete Metrics
   - Documented all new properties, setup steps, and behavior
   - Updated secrets list

## Edge Cases Handled

1. **Timezone bucketing**: Uses `start_date_local` when available, falls back to UTC
2. **Partial HR coverage**: Load points only computed when HR zones exist
3. **Missing properties**: Schema filtering prevents crashes
4. **Empty daily summaries**: Handles gracefully (no crashes)
5. **Daily Summary DB not set but Athlete Metrics DB set**: Computes daily summaries internally for rolling loads
6. **ETHR not implemented**: Clearly documented and fields left blank

## Testing Recommendations

1. **Backward compatibility**: Run sync with only original env vars - should work identically
2. **Daily Summary**: Enable DB, verify rows created/updated by date
3. **Athlete Metrics**: Enable DB, verify single row with rolling loads
4. **Load points**: Verify computed only when HR zones exist
5. **Schema filtering**: Remove a property from DB, verify sync continues without error
6. **Error handling**: Temporarily break optional DB access, verify main sync continues

## Known Limitations

1. **ETHR computation**: Intentionally not implemented (requires careful validation)
2. **Daily Summary window**: Uses same `days` parameter as activity sync (could be made configurable)
3. **Rolling loads**: Only computed from daily summaries within sync window (not from historical Notion data)

## Next Steps (Future Enhancements)

1. Implement ETHR computation with confidence guardrails
2. Optionally compute rolling loads from historical Notion data (not just sync window)
3. Add more granular load confidence indicators
4. Support configurable daily summary window


