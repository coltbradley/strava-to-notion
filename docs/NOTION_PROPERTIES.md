# Complete List of Notion Property Names

This document lists **all** Notion database property names used by the sync script. Property names are case-sensitive and must match exactly.

## Required Properties

1. `Name` (Title)
2. `Activity ID` (Rich text)
3. `Date` (Date)
4. `Sport` (Select)
5. `Duration (min)` (Number)
6. `Distance (mi)` (Number)
7. `Elevation (ft)` (Number)

## Strongly Recommended

8. `Strava URL` (URL)
9. `Last Synced` (Date)

## Optional Metrics

10. `Avg HR` (Number)
11. `Max HR` (Number)
12. `Avg Pace (min/mi)` (Number)
13. `Moving Time (min)` (Number)

## Optional Heart Rate Zones

14. `HR Zone 1 (min)` (Number)
15. `HR Zone 2 (min)` (Number)
16. `HR Zone 3 (min)` (Number)
17. `HR Zone 4 (min)` (Number)
18. `HR Zone 5 (min)` (Number)

## Optional Aerobic Decoupling / Drift

19. `HR Drift (%)` (Number)
20. `HR 1st Half (bpm)` (Number)
21. `HR 2nd Half (bpm)` (Number)
22. `Speed 1st Half (mph)` (Number)
23. `Speed 2nd Half (mph)` (Number)
24. `Drift Eligible` (Checkbox)
25. `HR Data Quality` (Select) - Options: "Good", "Partial", "None"

## Optional Weather

**Important:** Weather data is written **ONLY** to the Workouts Database. Weather is not written to Daily Summary or Athlete Metrics databases.

26. `Temperature (°F)` (Number) - **Note:** Includes degree symbol (°) and parentheses
   - Contains: Temperature in Fahrenheit (rounded to 1 decimal place)
   - Example: `72.5`

27. `Weather Conditions` (Rich text) - **Note:** Exact capitalization matters (both words capitalized)
   - Contains: Concise weather summary string
   - Format: `"{temp}°F, {conditions}, {wind} mph wind, {humidity}% humidity"`
   - Example: `"72°F, clear, 5 mph wind, 65% humidity"`

**When weather is written:**
- Only for outdoor activities (not strength training, indoor cycling, etc.)
- Only if the activity has location data (`start_latitude`, `start_longitude`)
- Weather may be unavailable for very recent activities (depends on API delay)

## Optional Operations / Debugging

28. `Sync Status` (Select) - Options: "created", "updated"

## Important Notes

- Property names are **case-sensitive** and **must match exactly**
- Special characters matter: `Temperature (°F)` includes the degree symbol and parentheses
- If a property doesn't exist in your database, the sync will skip it gracefully
- All properties except Required ones are optional - the sync works with just the required set


