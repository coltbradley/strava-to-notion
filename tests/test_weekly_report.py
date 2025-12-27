"""Unit tests for weekly status report generation."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Add repo root to path to import scripts
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from scripts.weekly_status_report import (
    aggregate_stats,
    filter_weekly_stats,
    format_error_fingerprints,
)


def test_aggregate_stats_empty():
    """Test aggregation with no stats."""
    result = aggregate_stats([])
    assert result["total_runs"] == 0
    assert result["workouts"]["fetched"] == 0
    assert result["daily_summary"]["enabled"] is False
    assert result["athlete_metrics"]["enabled"] is False


def test_aggregate_stats_single_run():
    """Test aggregation with a single run."""
    stats = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workouts": {
                "fetched": 10,
                "created": 5,
                "updated": 3,
                "skipped": 2,
                "failed": 0,
            },
            "daily_summary": {
                "enabled": True,
                "days_processed": 7,
                "failed": 1,
            },
            "athlete_metrics": {
                "enabled": True,
                "upserted": True,
                "failed": False,
            },
            "warnings": ["Warning 1", "Warning 2"],
            "errors": [],
        }
    ]
    
    result = aggregate_stats(stats)
    assert result["total_runs"] == 1
    assert result["workouts"]["fetched"] == 10
    assert result["workouts"]["created"] == 5
    assert result["workouts"]["updated"] == 3
    assert result["daily_summary"]["enabled"] is True
    assert result["daily_summary"]["total_days"] == 7
    assert result["daily_summary"]["total_failed"] == 1
    assert result["athlete_metrics"]["enabled"] is True
    assert result["athlete_metrics"]["total_upserted"] == 1
    assert result["total_warnings"] == 2


def test_aggregate_stats_multiple_runs():
    """Test aggregation with multiple runs."""
    now = datetime.now(timezone.utc)
    stats = [
        {
            "timestamp": (now - timedelta(days=i)).isoformat(),
            "workouts": {
                "fetched": 10,
                "created": 5,
                "updated": 3,
                "skipped": 2,
                "failed": 0,
            },
            "daily_summary": {"enabled": False, "days_processed": 0, "failed": 0},
            "athlete_metrics": {"enabled": False, "upserted": False, "failed": False},
            "warnings": [],
            "errors": ["Error pattern A", "Error pattern B"],
        }
        for i in range(3)
    ]
    
    result = aggregate_stats(stats)
    assert result["total_runs"] == 3
    assert result["workouts"]["fetched"] == 30
    assert result["workouts"]["created"] == 15
    assert result["total_errors"] == 6
    assert "Error pattern A" in result["error_fingerprints"]


def test_filter_weekly_stats():
    """Test filtering stats to last 7 days."""
    now = datetime.now(timezone.utc)
    stats = [
        {"timestamp": (now - timedelta(days=i)).isoformat(), "workouts": {"fetched": 1}}
        for i in range(10)
    ]
    
    filtered = filter_weekly_stats(stats, days=7)
    assert len(filtered) == 7  # Today + 6 days ago = 7 days


def test_format_error_fingerprints():
    """Test error fingerprint formatting."""
    fingerprints = {
        "Error pattern A": 5,
        "Error pattern B": 3,
        "Error pattern C": 1,
    }
    
    result = format_error_fingerprints(fingerprints, max_display=2)
    assert "Error pattern A" in result
    assert "5 occurrence(s)" in result
    assert "and 1 more" in result


def test_format_error_fingerprints_empty():
    """Test formatting with no errors."""
    result = format_error_fingerprints({})
    assert "None" in result


