#!/usr/bin/env python3
"""
Weekly Status Report Generator

Generates a markdown report summarizing sync stats from the past week.
Reads from stats/run_stats.json (populated by sync.py after each run).
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any


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


def generate_report(aggregated: Dict[str, Any], week_end: datetime, commit_sha: str = "unknown") -> str:
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

## Notes

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
    
    # Generate report
    week_end = datetime.now(timezone.utc)
    report = generate_report(aggregated, week_end, commit_sha)
    
    # Write to file (in repo root)
    output_file = repo_root / "weekly_status.md"
    with open(output_file, "w") as f:
        f.write(report)
    
    # Also write JSON version
    json_file = repo_root / "weekly_status.json"
    output_data = {
        "week_end": week_end.isoformat(),
        "commit_sha": commit_sha,
        "aggregated": aggregated,
    }
    with open(json_file, "w") as f:
        json.dump(output_data, f, indent=2)
    
    print(f"Report generated: {output_file}")
    print(f"JSON version: {json_file}")
    sys.exit(0)


if __name__ == "__main__":
    main()

