# Specification Compliance Summary

## Changes Made to Match Exact Spec

### 1. Cardio Sports Filtering ✅

- **Added `CARDIO_SPORTS` constant** with explicit sports: `{"Run", "Hike", "StairStepper", "TrailRun", "Walk", "VirtualRun"}`
- **Load computation gated by cardio sport**: Only workouts where `Sport ∈ CARDIO_SPORTS` can contribute load points
- Non-cardio sports (WeightTraining, RockClimbing, Surfing, Workout, etc.) do NOT contribute to load

### 2. HR Data Quality Gating ✅

- **Load computation requires HR Data Quality == "Good"**:
  - `HR Data Quality == "None"` → no load (workout does not contribute)
  - `HR Data Quality == "Partial"` → no load (conservative, no guessing)
  - `HR Data Quality == "Good"` → compute load using zone formula
- Load computation code checks: `sport_type in CARDIO_SPORTS AND hr_data_quality == "Good"`

### 3. Per-Activity Load (pts) in Workouts DB ✅

- **Added `"load_pts": "Load (pts)"` to `NOTION_SCHEMA`**
- **Added to `SYSTEM_OWNED_FIELDS`**
- **Writes per-activity load** in `_convert_activity_to_properties()` if:
  - `_load_pts` exists and > 0
  - Property exists in schema (schema-filtered)
- If property doesn't exist in Workouts DB, load is still computed internally for aggregation

### 4. Load Confidence Rules ✅

**Updated `aggregate_daily_summaries()` to track:**
- `eligible_cardio_count`: Count of cardio workouts on that date
- `load_workouts_count`: Count of cardio workouts with load computed (Good HR + load_pts > 0)

**Load Confidence logic:**
- **High**: `eligible_cardio_count > 0` AND `load_workouts_count == eligible_cardio_count` (all eligible produced load)
- **Medium**: `load_workouts_count > 0` but `load_workouts_count < eligible_cardio_count` (some produced load)
- **Low**: `load_workouts_count == 0` (no eligible workouts produced load)

### 5. Daily Summary Aggregation ✅

- **Load points only counted for cardio + Good HR**: In `aggregate_daily_summaries()`, load is only added if:
  - `sport_type in CARDIO_SPORTS`
  - `hr_data_quality == "Good"`
  - `load_pts > 0`
- All other metrics (duration, distance, elevation) aggregate for ALL activities (not just cardio)

### 6. Rolling Window Date Ranges ✅

**`compute_rolling_loads()`:**
- **7d**: Sums load for dates in `[today-6, today]` (7 days total, inclusive)
- **28d**: Sums load for dates in `[today-27, today]` (28 days total, inclusive)
- Dates calculated using `days_ago = (today_date - date_obj).days`
- Treats null/empty `total_load_pts` as 0.0 in the sum

### 7. Load Balance Computation ✅

- **`Load_Balance = Load_7d / Load_28d`** (if `Load_28d > 0`)
- **If `Load_28d == 0` or missing**: Set to `None` (no divide by zero)
- Per spec: "Do not silently divide by zero or return arbitrary sentinel values"

### 8. Schema-Aware Behavior ✅

- All three databases (Workouts, Daily Summary, Athlete Metrics) use `NotionSchemaCache` for schema filtering
- Properties are filtered before writes to only include keys that exist in the DB schema
- Missing properties are logged but don't abort the sync

### 9. Error Handling ✅

- Optional DB failures are logged as warnings
- Main sync continues even if Daily Summary or Athlete Metrics sync fails
- Clear error messages include which DB and which properties are missing

## Implementation Details

### Load Computation Flow

1. **During activity processing:**
   - HR zones computed if available
   - HR Data Quality set ("None", "Partial", or "Good")
   - Load computed ONLY if:
     - `sport_type in CARDIO_SPORTS`
     - `hr_data_quality == "Good"`
     - HR zones exist and can be computed
   - Load stored as `activity["_load_pts"]`

2. **Writing to Workouts DB:**
   - If `Load (pts)` property exists in schema and `_load_pts > 0`, write it
   - Schema-filtered, so missing property is skipped gracefully

3. **Daily Summary aggregation:**
   - All activities aggregated by local date
   - Load only counted for cardio + Good HR
   - Load Confidence computed based on eligible vs load workouts

4. **Athlete Metrics:**
   - Rolling loads computed from Daily Summary
   - Load Balance computed (with divide-by-zero protection)

## Edge Cases Handled

- ✅ Non-cardio sports: Do not contribute load, but still counted in session_count and other metrics
- ✅ Partial HR data: Treated as "no load" (conservative)
- ✅ Missing HR zones: No load computed
- ✅ Missing properties: Schema-filtered, logged, but sync continues
- ✅ Empty daily summaries: Handled gracefully (no crashes)
- ✅ Divide by zero: Load Balance returns None if Load_28d is 0
- ✅ Rolling windows with no data: Returns 0.0 (treated as 0 in sums)

## Specification Compliance Checklist

- [x] CARDIO_SPORTS defined explicitly
- [x] Load gated by cardio sport + HR Data Quality == "Good"
- [x] Per-activity Load (pts) written to Workouts DB if property exists
- [x] Daily Summary Load Confidence uses eligible vs load workouts logic
- [x] Rolling windows: [today-6, today] for 7d, [today-27, today] for 28d
- [x] Load Balance with divide-by-zero protection
- [x] Schema-aware property filtering for all databases
- [x] Error handling that logs but doesn't abort optional syncs
- [x] Non-cardio sports excluded from load but included in other metrics


