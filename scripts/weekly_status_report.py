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


def generate_report(
    aggregated: Dict[str, Any],
    week_end: datetime,
    commit_sha: str = "unknown",
    db_access: Optional[Dict[str, Any]] = None,
    last_activity_weather: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate markdown report."""
    week_end_str = week_end.strftime("%Y-%m-%d")
    
    report = f"""# Strava → Notion Sync Status Report

**Week ending:** {week_end_str}  
**Repository:** strava-to-notion  
**Commit:** {commit_sha}  

---

## Run Stats

**Total sync runs in past 7 days:** {aggregated['total_runs']}

### Workouts Database

- **Fetched from Strava:** {aggregated['workouts']['fetched']}
- **Created in Notion:** {aggregated['workouts']['created']}
- **Updated in Notion:** {aggregated['workouts']['updated']}
- **Skipped:** {aggregated['workouts']['skipped']}
- **Failed:** {aggregated['workouts']['failed']}

### Daily Summary Database

- **Enabled:** {aggregated['daily_summary']['enabled']}
- **Days processed:** {aggregated['daily_summary']['total_days']}
- **Failed upserts:** {aggregated['daily_summary']['total_failed']}

### Athlete Metrics Database

- **Enabled:** {aggregated['athlete_metrics']['enabled']}
- **Successful upserts:** {aggregated['athlete_metrics']['total_upserted']}
- **Failed upserts:** {aggregated['athlete_metrics']['total_failed']}

---

## Warnings

**Total warnings:** {aggregated['total_warnings']}

---

## Errors

**Total errors:** {aggregated['total_errors']}

**Top error patterns:**

{format_error_fingerprints(aggregated['error_fingerprints'])}

---

## Database Access Verification

"""
    
    # Add database access section
    if db_access:
        report += "### Workouts Database\n\n"
        w = db_access["workouts"]
        if w["accessible"]:
            report += f"- ✅ **Accessible** ({w['schema_count']} properties)\n"
        else:
            report += f"- ❌ **Not accessible**\n"
            if w["error"]:
                report += f"  - Error: `{w['error']}`\n"
        
        report += "\n### Daily Summary Database\n\n"
        ds = db_access["daily_summary"]
        if ds["accessible"]:
            report += f"- ✅ **Accessible** ({ds['schema_count']} properties)\n"
        elif ds.get("error"):
            report += f"- ❌ **Not accessible**\n"
            report += f"  - Error: `{ds['error']}`\n"
        else:
            report += "- ⚠️ **Not configured** (optional)\n"
        
        report += "\n### Athlete Metrics Database\n\n"
        am = db_access["athlete_metrics"]
        if am["accessible"]:
            report += f"- ✅ **Accessible** ({am['schema_count']} properties)\n"
        elif am.get("error"):
            report += f"- ❌ **Not accessible**\n"
            report += f"  - Error: `{am['error']}`\n"
        else:
            report += "- ⚠️ **Not configured** (optional)\n"
    else:
        report += "*Database access verification skipped (Notion token not available)*\n"
    
    report += "\n---\n\n"
    
    # Add last activity weather section
    report += "## Last Activity Weather Check\n\n"
    if last_activity_weather:
        report += "**Most recent activity in Notion:**\n\n"
        if last_activity_weather.get("name"):
            report += f"- **Name:** {last_activity_weather['name']}\n"
        if last_activity_weather.get("date"):
            report += f"- **Date:** {last_activity_weather['date']}\n"
        if last_activity_weather.get("activity_id"):
            report += f"- **Activity ID:** {last_activity_weather['activity_id']}\n"
        
        temp = last_activity_weather.get("temperature")
        weather = last_activity_weather.get("weather_conditions")
        
        if temp is not None:
            report += f"- **Temperature:** {temp}°F\n"
        if weather:
            report += f"- **Weather Conditions:** {weather}\n"
        
        if temp is None and not weather:
            report += "- ⚠️ **No weather data** (activity may be indoor or weather fetch failed)\n"
    else:
        report += "*Could not retrieve last activity weather (Notion token or database ID not available)*\n"
    
    report += "\n---\n\n"
    
    report += """## Notes

This report summarizes operational statistics only. It does not provide training analysis or recommendations.

For detailed logs, see GitHub Actions workflow runs.

"""
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

