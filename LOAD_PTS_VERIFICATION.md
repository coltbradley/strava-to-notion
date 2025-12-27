# Load (pts) Property Verification

## Where Load (pts) is Written

### 1. **Workouts Database** (per-activity)
- **Property name**: `Load (pts)` (Number)
- **Schema constant**: `NOTION_SCHEMA["load_pts"]`
- **Location in code**: `_convert_activity_to_properties()` around line 1486
- **What it contains**: Per-activity load points for individual workouts
- **When written**: Only if the activity is:
  - Cardio sport (in `CARDIO_SPORTS`)
  - Has HR Data Quality == "Good"
  - Has computed load points > 0

### 2. **Daily Summary Database** (daily total)
- **Property name**: `Load (pts)` (Number)
- **Schema constant**: `DAILY_SUMMARY_SCHEMA["load_pts"]`
- **Location in code**: `upsert_daily_summary()` around line 1558
- **What it contains**: Daily aggregated total of all load points for that day
- **When written**: Sum of all per-activity load points for activities on that date

### 3. **Athlete Metrics Database** (NOT written here)
- **Does NOT have** `Load (pts)` property
- **Has**: `Load 7d`, `Load 28d`, `Load Balance` (which are computed from Daily Summary data)
- **This is correct** - Athlete Metrics stores rolling aggregates, not per-day or per-activity values

## Conclusion

**Load (pts) is correctly written to TWO databases:**
1. Workouts DB - per-activity load points
2. Daily Summary DB - daily aggregated load points

This is **correct behavior** per the specification. Both use the same property name because they're in different databases, so there's no conflict.

## If You're Seeing Issues

If `Load (pts)` is missing from Notion:
- **Workouts DB**: Make sure the property exists with exact name `Load (pts)` (case-sensitive)
- **Daily Summary DB**: Make sure the property exists with exact name `Load (pts)` (case-sensitive)
- Both properties should be Number type in Notion

