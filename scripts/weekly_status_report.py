#!/usr/bin/env python3
"""
Weekly Status Report Generator

Generates a markdown report summarizing sync stats from the past week.
Reads from stats/run_stats.json (populated by sync.py after each run).
Also verifies database access and reports last activity weather.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Add parent directory to path to import from sync.py
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
    NOTION_AVAILABLE = True
except ImportError:
    NOTION_AVAILABLE = False


def load_run_stats(stats_file: Path) -> List[Dict[str, Any]]:
    """Load run stats from JSON file."""
    if not stats_file.exists():
        return []
    
    try:
        with open(stats_file, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            else:
                # Legacy format (single dict)
                return [data] if data else []
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading stats file: {e}", file=sys.stderr)
        return []


def filter_weekly_stats(all_stats: List[Dict[str, Any]], days: int = 7) -> List[Dict[str, Any]]:
    """Filter stats to last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [
        s for s in all_stats
        if datetime.fromisoformat(s["timestamp"]) >= cutoff
    ]


def aggregate_stats(weekly_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate statistics from multiple runs."""
    if not weekly_stats:
        return {
            "total_runs": 0,
            "workouts": {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 0},
            "daily_summary": {"enabled": False, "total_days": 0, "total_failed": 0},
            "athlete_metrics": {"enabled": False, "total_upserted": 0, "total_failed": 0},
            "total_warnings": 0,
            "total_errors": 0,
            "error_fingerprints": {},
        }
    
    agg = {
        "total_runs": len(weekly_stats),
        "workouts": {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 0},
        "daily_summary": {"enabled": False, "total_days": 0, "total_failed": 0},
        "athlete_metrics": {"enabled": False, "total_upserted": 0, "total_failed": 0},
        "total_warnings": 0,
        "total_errors": 0,
        "error_fingerprints": defaultdict(int),
    }
    
    for run in weekly_stats:
        # Workouts
        w = run.get("workouts", {})
        agg["workouts"]["fetched"] += w.get("fetched", 0)
        agg["workouts"]["created"] += w.get("created", 0)
        agg["workouts"]["updated"] += w.get("updated", 0)
        agg["workouts"]["skipped"] += w.get("skipped", 0)
        agg["workouts"]["failed"] += w.get("failed", 0)
        
        # Daily Summary
        ds = run.get("daily_summary", {})
        if ds.get("enabled"):
            agg["daily_summary"]["enabled"] = True
            agg["daily_summary"]["total_days"] += ds.get("days_processed", 0)
            agg["daily_summary"]["total_failed"] += ds.get("failed", 0)
        
        # Athlete Metrics
        am = run.get("athlete_metrics", {})
        if am.get("enabled"):
            agg["athlete_metrics"]["enabled"] = True
            if am.get("upserted"):
                agg["athlete_metrics"]["total_upserted"] += 1
            if am.get("failed"):
                agg["athlete_metrics"]["total_failed"] += 1
        
        # Warnings and errors
        agg["total_warnings"] += len(run.get("warnings", []))
        agg["total_errors"] += len(run.get("errors", []))
        
        # Error fingerprints (simple: count unique error message prefixes)
        for err in run.get("errors", []):
            # Extract first 50 chars as fingerprint
            fingerprint = err[:50] if isinstance(err, str) else str(err)[:50]
            agg["error_fingerprints"][fingerprint] += 1
    
    return agg


def format_error_fingerprints(error_fingerprints: Dict[str, int], max_display: int = 10) -> str:
    """Format error fingerprints for display."""
    if not error_fingerprints:
        return "  * None"
    
    sorted_errors = sorted(error_fingerprints.items(), key=lambda x: x[1], reverse=True)
    lines = []
    for fingerprint, count in sorted_errors[:max_display]:
        lines.append(f"  * `{fingerprint}` ({count} occurrence(s))")
    
    if len(sorted_errors) > max_display:
        lines.append(f"  * ... and {len(sorted_errors) - max_display} more")
    
    return "\n".join(lines)


def verify_database_access(
    notion_token: str,
    workouts_db_id: Optional[str],
    daily_summary_db_id: Optional[str],
    athlete_metrics_db_id: Optional[str],
) -> Dict[str, Any]:
    """
    Verify access to all configured Notion databases and get schema info.
    
    Returns dict with access status and schema counts for each database.
    """
    results = {
        "workouts": {"accessible": False, "schema_count": 0, "error": None},
        "daily_summary": {"accessible": False, "schema_count": 0, "error": None},
        "athlete_metrics": {"accessible": False, "schema_count": 0, "error": None},
    }
    
    if not NOTION_AVAILABLE:
        results["workouts"]["error"] = "notion-client not available"
        return results
    
    try:
        client = Client(auth=notion_token)
    except Exception as e:
        results["workouts"]["error"] = f"Failed to initialize Notion client: {e}"
        return results
    
    # Check Workouts database
    if workouts_db_id:
        try:
            db = client.databases.retrieve(database_id=workouts_db_id)
            props = db.get("properties", {})
            results["workouts"]["accessible"] = True
            results["workouts"]["schema_count"] = len(props)
        except APIResponseError as e:
            results["workouts"]["error"] = f"API error: {e}"
        except Exception as e:
            results["workouts"]["error"] = f"Error: {e}"
    
    # Check Daily Summary database
    if daily_summary_db_id:
        try:
            db = client.databases.retrieve(database_id=daily_summary_db_id)
            props = db.get("properties", {})
            results["daily_summary"]["accessible"] = True
            results["daily_summary"]["schema_count"] = len(props)
        except APIResponseError as e:
            results["daily_summary"]["error"] = f"API error: {e}"
        except Exception as e:
            results["daily_summary"]["error"] = f"Error: {e}"
    
    # Check Athlete Metrics database
    if athlete_metrics_db_id:
        try:
            db = client.databases.retrieve(database_id=athlete_metrics_db_id)
            props = db.get("properties", {})
            results["athlete_metrics"]["accessible"] = True
            results["athlete_metrics"]["schema_count"] = len(props)
        except APIResponseError as e:
            results["athlete_metrics"]["error"] = f"API error: {e}"
        except Exception as e:
            results["athlete_metrics"]["error"] = f"Error: {e}"
    
    return results


def get_last_activity_weather(
    notion_token: str,
    workouts_db_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Get the weather data from the most recent activity in Notion.
    
    Returns dict with activity info and weather, or None if not available.
    """
    if not NOTION_AVAILABLE or not workouts_db_id:
        return None
    
    try:
        client = Client(auth=notion_token)
        
        # Import schema constants for property names
        from sync import NOTION_SCHEMA
        
        # Query for most recent activity (sorted by Date descending, limit 1)
        response = client.databases.query(
            database_id=workouts_db_id,
            sorts=[
                {
                    "property": NOTION_SCHEMA["date"],  # "Date" - use schema constant for consistency
                    "direction": "descending"
                }
            ],
            page_size=1
        )
        
        if not response.get("results"):
            return None
        
        page = response["results"][0]
        props = page.get("properties", {})
        
        # Extract activity info
        activity_info = {
            "page_id": page["id"],
            "name": None,
            "date": None,
            "activity_id": None,
            "temperature": None,
            "weather_conditions": None,
        }
        
        # Get Name (Title)
        name_prop = props.get(NOTION_SCHEMA["name"])  # "Name"
        if name_prop and name_prop.get("title"):
            activity_info["name"] = name_prop["title"][0].get("plain_text", "")
        
        # Get Date
        date_prop = props.get(NOTION_SCHEMA["date"])  # "Date"
        if date_prop and date_prop.get("date"):
            activity_info["date"] = date_prop["date"].get("start", "")
        
        # Get Activity ID
        activity_id_prop = props.get(NOTION_SCHEMA["activity_id"])  # "Activity ID"
        if activity_id_prop and activity_id_prop.get("rich_text"):
            activity_info["activity_id"] = activity_id_prop["rich_text"][0].get("plain_text", "")
        
        # Get Temperature (°F)
        temp_prop = props.get(NOTION_SCHEMA["temperature_f"])  # "Temperature (°F)"
        if temp_prop and temp_prop.get("number") is not None:
            activity_info["temperature"] = temp_prop["number"]
        
        # Get Weather Conditions
        weather_prop = props.get(NOTION_SCHEMA["weather_conditions"])  # "Weather Conditions"
        if weather_prop and weather_prop.get("rich_text"):
            activity_info["weather_conditions"] = weather_prop["rich_text"][0].get("plain_text", "")
        
        return activity_info
        
    except Exception as e:
        # Return None on any error (non-fatal)
        return None


def format_health_status(value: bool, enabled: bool = True) -> str:
    """Format health status with emoji."""
    if not enabled:
        return "⚪ Not configured (optional)"
    return "✅ Working" if value else "❌ Not accessible"


def format_error_explanation(error_fingerprint: str) -> str:
    """Provide human-readable explanation and fix guidance for common errors."""
    error_lower = error_fingerprint.lower()
    
    if "property" in error_lower and ("doesn't exist" in error_lower or "not a property" in error_lower):
        if "weather conditions" in error_lower or "temperature" in error_lower:
            return "**Issue:** Weather properties missing in Notion database\n\n**Fix:** Add these properties to your Workouts database:\n- `Temperature (°F)` (Number type)\n- `Weather Conditions` (Rich text type)\n\nSee `docs/NOTION_PROPERTIES.md` for exact property names and types."
        elif "load (pts)" in error_lower or "load" in error_lower:
            return "**Issue:** Load (pts) property missing in Notion database\n\n**Fix:** Add `Load (pts)` (Number type) to your Workouts database, or this is optional and can be ignored if you don't need load points."
        else:
            return "**Issue:** A property is missing in your Notion database\n\n**Fix:** Check the error details and add the missing property. See `docs/NOTION_PROPERTIES.md` for a complete list of properties."
    
    if "refresh token" in error_lower or "401" in error_lower:
        return "**Issue:** Strava authentication failed\n\n**Fix:** Regenerate your refresh token:\n1. Go to https://www.strava.com/settings/apps\n2. Revoke access to your app\n3. Re-authorize with scope: `read,activity:read_all,profile:read_all`\n4. Update `STRAVA_REFRESH_TOKEN` in GitHub Secrets"
    
    if "notion" in error_lower and ("token" in error_lower or "unauthorized" in error_lower):
        return "**Issue:** Notion authentication failed\n\n**Fix:** Check your Notion integration:\n1. Verify `NOTION_TOKEN` in GitHub Secrets is correct\n2. Ensure the integration has access to your database\n3. Check integration permissions in Notion Settings → Connections"
    
    if "schema" in error_lower and "0 properties" in error_lower:
        return "**Issue:** Could not read Notion database schema\n\n**Fix:** Check database permissions:\n1. Ensure the Notion integration has access to the database\n2. Verify the database ID is correct in GitHub Secrets\n3. Check that the integration hasn't been revoked"
    
    return "**Issue:** Unexpected error occurred\n\n**Fix:** Check the full error logs in GitHub Actions for details."


def generate_report(
    aggregated: Dict[str, Any],
    week_end: datetime,
    commit_sha: str = "unknown",
    db_access: Optional[Dict[str, Any]] = None,
    last_activity_weather: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate markdown report."""
    week_end_str = week_end.strftime("%Y-%m-%d")
    
    # Calculate health indicators
    workouts_healthy = aggregated['workouts']['failed'] == 0 and aggregated['workouts']['fetched'] > 0
    daily_summary_healthy = not aggregated['daily_summary']['enabled'] or aggregated['daily_summary']['total_failed'] == 0
    athlete_metrics_healthy = not aggregated['athlete_metrics']['enabled'] or aggregated['athlete_metrics']['total_failed'] == 0
    overall_healthy = workouts_healthy and daily_summary_healthy and athlete_metrics_healthy and aggregated['total_errors'] == 0
    
    report = f"""# 📊 Strava → Notion Sync Weekly Status Report

**Week ending:** {week_end_str}  
**Repository:** strava-to-notion  
**Commit:** `{commit_sha}`  

---

## 🎯 Quick Summary

{"✅ **Everything is working well!** All systems operational." if overall_healthy else "⚠️ **Attention needed** - See details below"}

**Sync Status:** {"🟢 Healthy" if workouts_healthy else "🔴 Issues detected"}  
**Errors this week:** {aggregated['total_errors']}  
**Failed activities:** {aggregated['workouts']['failed']}  

---

## 📈 Activity Sync Summary

**Total sync runs this week:** {aggregated['total_runs']}

### Workouts Database {"✅" if workouts_healthy else "❌"}

- **Activities fetched from Strava:** {aggregated['workouts']['fetched']}
- **New activities created:** {aggregated['workouts']['created']}
- **Existing activities updated:** {aggregated['workouts']['updated']}
- **Activities skipped:** {aggregated['workouts']['skipped']}
- **Activities failed:** {aggregated['workouts']['failed']}

{f"⚠️ **Warning:** {aggregated['workouts']['failed']} activities failed to sync. Check error details below." if aggregated['workouts']['failed'] > 0 else ""}

### Daily Summary Database {format_health_status(daily_summary_healthy, aggregated['daily_summary']['enabled'])}

{"" if not aggregated['daily_summary']['enabled'] else f"""- **Days processed:** {aggregated['daily_summary']['total_days']}
- **Failed updates:** {aggregated['daily_summary']['total_failed']}
{chr(10) + f"⚠️ **Warning:** {aggregated['daily_summary']['total_failed']} days failed to update. Check your Daily Summary database configuration." if aggregated['daily_summary']['total_failed'] > 0 else ""}"""}

### Athlete Metrics Database {format_health_status(athlete_metrics_healthy, aggregated['athlete_metrics']['enabled'])}

{"" if not aggregated['athlete_metrics']['enabled'] else f"""- **Successful updates:** {aggregated['athlete_metrics']['total_upserted']}
- **Failed updates:** {aggregated['athlete_metrics']['total_failed']}
{chr(10) + f"⚠️ **Warning:** Athlete metrics updates are failing. Check your Athlete Metrics database configuration." if aggregated['athlete_metrics']['total_failed'] > 0 else ""}"""}

---

## 🔍 Database Access Check

"""
    
    # Add database access section
    if db_access:
        w = db_access["workouts"]
        if w["accessible"]:
            report += f"✅ **Workouts Database:** Accessible ({w['schema_count']} properties found)\n"
        else:
            report += f"❌ **Workouts Database:** Cannot access database\n"
            if w["error"]:
                error_msg = w["error"]
                if "unauthorized" in error_msg.lower() or "token" in error_msg.lower():
                    report += "  \n**Fix:** Check your `NOTION_TOKEN` in GitHub Secrets and ensure the integration has access to the database.\n"
                elif "not found" in error_msg.lower() or "404" in error_msg.lower():
                    report += "  \n**Fix:** Verify your `NOTION_DATABASE_ID` in GitHub Secrets is correct.\n"
                else:
                    report += f"  \n**Error details:** {error_msg}\n"
        
        ds = db_access["daily_summary"]
        if ds["accessible"]:
            report += f"✅ **Daily Summary Database:** Accessible ({ds['schema_count']} properties found)\n"
        elif ds.get("error"):
            report += f"❌ **Daily Summary Database:** Cannot access\n"
            report += f"  \n**Fix:** Check `NOTION_DAILY_SUMMARY_DATABASE_ID` in GitHub Secrets, or remove this secret if you don't use Daily Summary.\n"
        else:
            report += "⚪ **Daily Summary Database:** Not configured (optional - add `NOTION_DAILY_SUMMARY_DATABASE_ID` secret to enable)\n"
        
        am = db_access["athlete_metrics"]
        if am["accessible"]:
            report += f"✅ **Athlete Metrics Database:** Accessible ({am['schema_count']} properties found)\n"
        elif am.get("error"):
            report += f"❌ **Athlete Metrics Database:** Cannot access\n"
            report += f"  \n**Fix:** Check `NOTION_ATHLETE_METRICS_DATABASE_ID` in GitHub Secrets, or remove this secret if you don't use Athlete Metrics.\n"
        else:
            report += "⚪ **Athlete Metrics Database:** Not configured (optional - add `NOTION_ATHLETE_METRICS_DATABASE_ID` secret to enable)\n"
    else:
        report += "⚠️ **Database check skipped** (Notion token not available in report generation)\n"
    
    report += "\n---\n\n"
    
    # Add last activity weather section
    report += "## 🌤️ Weather Data Check\n\n"
    if last_activity_weather:
        report += "**Most recent activity:** "
        if last_activity_weather.get("name"):
            report += f"*{last_activity_weather['name']}*"
        if last_activity_weather.get("date"):
            report += f" ({last_activity_weather['date'][:10]})"  # Just the date part
        report += "\n\n"
        
        temp = last_activity_weather.get("temperature")
        weather = last_activity_weather.get("weather_conditions")
        
        if temp is not None and weather:
            report += f"✅ **Weather data is working:** {temp}°F, {weather}\n"
        elif temp is not None:
            report += f"⚠️ **Partial weather data:** Temperature ({temp}°F) found, but weather conditions missing\n"
            report += "  \n**Fix:** Check that `Weather Conditions` property exists in your Workouts database (Rich text type)\n"
        elif weather:
            report += f"⚠️ **Partial weather data:** Weather conditions found, but temperature missing\n"
            report += "  \n**Fix:** Check that `Temperature (°F)` property exists in your Workouts database (Number type)\n"
        else:
            report += "⚠️ **No weather data found**\n\n"
            report += "**Possible reasons:**\n"
            report += "- Activity is indoor (no location data)\n"
            report += "- Weather properties don't exist in database (add `Temperature (°F)` and `Weather Conditions`)\n"
            report += "- Weather API failed or activity is too recent\n"
            report += "  \n**To fix:** See `docs/NOTION_PROPERTIES.md` for property setup instructions.\n"
    else:
        report += "⚠️ **Could not check weather** (Notion token or database ID not available)\n"
    
    report += "\n---\n\n"
    
    # Errors section
    report += "## ❌ Errors & Issues\n\n"
    
    if aggregated['total_errors'] == 0:
        report += "✅ **No errors this week!** Everything is running smoothly.\n\n"
    else:
        report += f"⚠️ **{aggregated['total_errors']} error(s) occurred this week.**\n\n"
        
        if aggregated['error_fingerprints']:
            report += "### Most Common Issues:\n\n"
            sorted_errors = sorted(aggregated['error_fingerprints'].items(), key=lambda x: x[1], reverse=True)
            for i, (fingerprint, count) in enumerate(sorted_errors[:5], 1):
                report += f"#### {i}. Occurred {count} time(s):\n\n"
                report += f"```\n{fingerprint[:200]}\n```\n\n"
                explanation = format_error_explanation(fingerprint)
                report += f"{explanation}\n\n"
            
            if len(sorted_errors) > 5:
                report += f"*... and {len(sorted_errors) - 5} more error pattern(s)*\n\n"
    
    # Warnings section
    if aggregated['total_warnings'] > 0:
        report += f"---\n\n## ⚠️ Warnings\n\n"
        report += f"**Total warnings:** {aggregated['total_warnings']}\n\n"
        report += "*Check GitHub Actions logs for detailed warning messages.*\n\n"
    
    # Footer
    report += "---\n\n## 📝 Next Steps\n\n"
    
    if not overall_healthy:
        report += "**Action items:**\n\n"
        if aggregated['workouts']['failed'] > 0:
            report += f"1. 🔴 Fix {aggregated['workouts']['failed']} failed activity sync(s) - see error details above\n"
        if db_access and not db_access['workouts']['accessible']:
            report += "2. 🔴 Fix Workouts database access - check authentication and database ID\n"
        if last_activity_weather and not last_activity_weather.get("temperature") and not last_activity_weather.get("weather_conditions"):
            report += "3. ⚠️ Add weather properties to Workouts database (optional but recommended)\n"
        if aggregated['daily_summary']['enabled'] and aggregated['daily_summary']['total_failed'] > 0:
            report += f"4. ⚠️ Fix {aggregated['daily_summary']['total_failed']} failed Daily Summary update(s)\n"
        if aggregated['athlete_metrics']['enabled'] and aggregated['athlete_metrics']['total_failed'] > 0:
            report += f"5. ⚠️ Fix Athlete Metrics update failures\n"
        report += "\n"
    else:
        report += "✅ **No action needed** - everything is working correctly!\n\n"
    
    report += "---\n\n"
    report += "## 📚 Additional Resources\n\n"
    report += "- **Full logs:** Check GitHub Actions → Workflows → Sync Strava to Notion\n"
    report += "- **Property reference:** See `docs/NOTION_PROPERTIES.md` for database setup\n"
    report += "- **Troubleshooting:** See README.md troubleshooting section\n"
    report += "- **Repository:** https://github.com/coltbradley/strava-to-notion\n\n"
    report += "---\n\n"
    report += "*This is an automated status report. It summarizes operational statistics only and does not provide training analysis or recommendations.*\n"
    
    return report


def main():
    """Main entry point."""
    import os
    
    # Determine paths (scripts/ is in repo root, stats/ is also in repo root)
    repo_root = Path(__file__).parent.parent
    stats_file = repo_root / "stats" / "run_stats.json"
    
    # Load and filter stats
    all_stats = load_run_stats(stats_file)
    weekly_stats = filter_weekly_stats(all_stats, days=7)
    
    # Aggregate
    aggregated = aggregate_stats(weekly_stats)
    
    # Get commit SHA from environment (set by GitHub Actions) or default
    commit_sha = os.getenv("GITHUB_SHA", "local")
    if commit_sha and len(commit_sha) > 7:
        commit_sha = commit_sha[:7]
    
    # Verify database access (if Notion token available)
    notion_token = os.getenv("NOTION_TOKEN")
    db_access = None
    last_activity_weather = None
    
    if notion_token:
        workouts_db_id = os.getenv("NOTION_DATABASE_ID")
        daily_summary_db_id = os.getenv("NOTION_DAILY_SUMMARY_DATABASE_ID")
        athlete_metrics_db_id = os.getenv("NOTION_ATHLETE_METRICS_DATABASE_ID")
        
        print("Verifying database access...")
        db_access = verify_database_access(
            notion_token,
            workouts_db_id,
            daily_summary_db_id,
            athlete_metrics_db_id,
        )
        
        # Get last activity weather
        if workouts_db_id:
            print("Retrieving last activity weather...")
            last_activity_weather = get_last_activity_weather(notion_token, workouts_db_id)
    else:
        print("NOTION_TOKEN not available, skipping database verification")
    
    # Generate report
    week_end = datetime.now(timezone.utc)
    report = generate_report(
        aggregated,
        week_end,
        commit_sha,
        db_access,
        last_activity_weather,
    )
    
    # Write to file (in repo root)
    output_file = repo_root / "weekly_status.md"
    with open(output_file, "w") as f:
        f.write(report)
    
    # Also write JSON version (include new data)
    json_file = repo_root / "weekly_status.json"
    output_data = {
        "week_end": week_end.isoformat(),
        "commit_sha": commit_sha,
        "aggregated": aggregated,
        "database_access": db_access,
        "last_activity_weather": last_activity_weather,
    }
    with open(json_file, "w") as f:
        json.dump(output_data, f, indent=2)
    
    print(f"Report generated: {output_file}")
    print(f"JSON version: {json_file}")
    sys.exit(0)


if __name__ == "__main__":
    main()

