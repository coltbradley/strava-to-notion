# Implementation Plan: Daily Summary & Athlete Metrics

## Current System Analysis

- **Structure**: Single-file `sync.py` (~1458 lines) - still manageable
- **NotionClient**: Handles one database with per-instance schema caching
- **Sync Flow**: Activities → Notion Workouts DB (required)
- **Schema Management**: `allowed_properties` stored per NotionClient instance

## Implementation Strategy

### 1. Multi-Database Schema Caching
- Refactor `NotionClient` to support multiple databases
- Store schema cache as `Dict[str, Optional[set]]` mapping database_id -> properties
- Create a shared `NotionSchemaManager` class to handle schema caching across multiple DBs

### 2. Load Points Computation
- Add `compute_zone_weighted_load_points(zone_minutes: Dict[int, float]) -> float`
- Formula: Z1*1 + Z2*2 + Z3*3 + Z4*4 + Z5*5
- Only compute if HR zones exist

### 3. Daily Summary Database
- Aggregate activities by local date
- Compute daily totals (duration, distance, elevation, session count)
- Compute load points per activity, then sum per day
- Compute Load Confidence (High/Medium/Low)
- Upsert by Date (idempotent)

### 4. Athlete Metrics Database
- Single row per athlete
- Compute rolling loads (7d, 28d) from Daily Summary (preferred) or activities (fallback)
- Compute Load Balance (7d / 28d)
- ETHR fields: leave blank with note "ETHR intentionally not implemented yet"

### 5. Backward Compatibility
- All new features are optional (env vars)
- Original sync works exactly as before
- Failures in optional DBs are logged but don't abort main sync

## Key Implementation Details

### Date Handling
- Use `start_date_local` from Strava if available, else `start_date` (UTC)
- Parse to local date string (YYYY-MM-DD) for bucketing

### Load Points
- Only compute when HR zone minutes exist
- Treat missing HR as "no load" (don't guess)
- Sum per day for Daily Summary

### Schema Management
- Each database gets its own schema cache
- Schema is loaded lazily on first write
- Missing properties are filtered out (no crashes)

### Error Handling
- Optional DB failures are logged as warnings
- Main sync continues even if optional syncs fail
- Clear error messages for troubleshooting

