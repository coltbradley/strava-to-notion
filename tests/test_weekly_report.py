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


def test_filter_weekly_stats_exact_boundary():
    """Test filtering at exact 7-day boundary."""
    now = datetime.now(timezone.utc)
    # Create stats exactly 7 days ago and 8 days ago
    stats = [
        {"timestamp": (now - timedelta(days=7)).isoformat(), "workouts": {"fetched": 1}},  # Should be included
        {"timestamp": (now - timedelta(days=8)).isoformat(), "workouts": {"fetched": 1}},  # Should be excluded
        {"timestamp": (now - timedelta(days=6, hours=23)).isoformat(), "workouts": {"fetched": 1}},  # Should be included
    ]
    
    filtered = filter_weekly_stats(stats, days=7)
    assert len(filtered) == 2


def test_filter_weekly_stats_timezone_aware():
    """Test filtering with timezone-aware datetimes."""
    now = datetime.now(timezone.utc)
    # Mix of UTC and non-UTC timestamps (simulating edge cases)
    stats = [
        {"timestamp": (now - timedelta(days=1)).isoformat(), "workouts": {"fetched": 1}},
        {"timestamp": (now - timedelta(days=8)).isoformat(), "workouts": {"fetched": 1}},
    ]
    
    filtered = filter_weekly_stats(stats, days=7)
    assert len(filtered) == 1


def test_aggregate_stats_missing_fields():
    """Test aggregation handles missing fields gracefully."""
    stats = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            # Missing workouts field
            "daily_summary": {"enabled": False},
            "warnings": [],
            "errors": [],
        },
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workouts": {
                "fetched": 5,
                # Missing created, updated, skipped, failed
            },
            "daily_summary": {"enabled": True, "days_processed": 3},
            # Missing athlete_metrics
            "warnings": [],
            "errors": [],
        },
    ]
    
    result = aggregate_stats(stats)
    assert result["total_runs"] == 2
    assert result["workouts"]["fetched"] == 5  # Second run has fetched
    assert result["workouts"]["created"] == 0  # Missing, defaults to 0
    assert result["daily_summary"]["enabled"] is True
    assert result["daily_summary"]["total_days"] == 3


def test_aggregate_stats_with_authentication_errors():
    """Test aggregation properly captures authentication errors."""
    stats = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workouts": {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 1},
            "daily_summary": {"enabled": False},
            "athlete_metrics": {"enabled": False},
            "warnings": [],
            "errors": ["401: Unauthorized - Invalid refresh token"],
        }
    ]
    
    result = aggregate_stats(stats)
    assert result["workouts"]["failed"] == 1
    assert result["total_errors"] == 1
    assert "401: Unauthorized - Invalid refresh token" in str(result["error_fingerprints"])


def test_aggregate_stats_with_notion_errors():
    """Test aggregation properly captures Notion API errors."""
    stats = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workouts": {"fetched": 5, "created": 2, "updated": 0, "skipped": 0, "failed": 3},
            "daily_summary": {"enabled": False},
            "athlete_metrics": {"enabled": False},
            "warnings": [],
            "errors": [
                "Property 'Temperature (°F)' doesn't exist",
                "Property 'Temperature (°F)' doesn't exist",
                "Notion API error: 401 Unauthorized",
            ],
        }
    ]
    
    result = aggregate_stats(stats)
    assert result["workouts"]["failed"] == 3
    assert result["total_errors"] == 3
    assert "Property 'Temperature (°F)' doesn't exist"[:50] in result["error_fingerprints"]


def test_aggregate_stats_long_error_messages():
    """Test error fingerprinting with long error messages."""
    long_error = "A" * 200  # Very long error message
    stats = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workouts": {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 1},
            "daily_summary": {"enabled": False},
            "athlete_metrics": {"enabled": False},
            "warnings": [],
            "errors": [long_error],
        }
    ]
    
    result = aggregate_stats(stats)
    # Should truncate to 50 chars for fingerprint
    for fingerprint in result["error_fingerprints"].keys():
        assert len(fingerprint) <= 50


def test_aggregate_stats_non_string_errors():
    """Test aggregation handles non-string error objects."""
    stats = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workouts": {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 1},
            "daily_summary": {"enabled": False},
            "athlete_metrics": {"enabled": False},
            "warnings": [],
            "errors": [{"error": "Something went wrong"}, 500, None],
        }
    ]
    
    result = aggregate_stats(stats)
    assert result["total_errors"] == 3
    # Should handle non-string errors by converting to string
    assert len(result["error_fingerprints"]) > 0


def test_filter_weekly_stats_empty_list():
    """Test filtering empty stats list."""
    filtered = filter_weekly_stats([], days=7)
    assert filtered == []


def test_filter_weekly_stats_all_old():
    """Test filtering when all stats are older than cutoff."""
    now = datetime.now(timezone.utc)
    stats = [
        {"timestamp": (now - timedelta(days=10)).isoformat(), "workouts": {"fetched": 1}},
        {"timestamp": (now - timedelta(days=20)).isoformat(), "workouts": {"fetched": 1}},
    ]
    
    filtered = filter_weekly_stats(stats, days=7)
    assert len(filtered) == 0


def test_filter_weekly_stats_all_recent():
    """Test filtering when all stats are within window."""
    now = datetime.now(timezone.utc)
    stats = [
        {"timestamp": (now - timedelta(days=1)).isoformat(), "workouts": {"fetched": 1}},
        {"timestamp": (now - timedelta(days=3)).isoformat(), "workouts": {"fetched": 1}},
        {"timestamp": (now - timedelta(days=6)).isoformat(), "workouts": {"fetched": 1}},
    ]
    
    filtered = filter_weekly_stats(stats, days=7)
    assert len(filtered) == 3


def test_aggregate_stats_daily_summary_disabled_then_enabled():
    """Test aggregation when daily summary is disabled in some runs, enabled in others."""
    now = datetime.now(timezone.utc)
    stats = [
        {
            "timestamp": (now - timedelta(days=3)).isoformat(),
            "workouts": {"fetched": 5},
            "daily_summary": {"enabled": False, "days_processed": 0, "failed": 0},
            "athlete_metrics": {"enabled": False},
            "warnings": [],
            "errors": [],
        },
        {
            "timestamp": (now - timedelta(days=1)).isoformat(),
            "workouts": {"fetched": 5},
            "daily_summary": {"enabled": True, "days_processed": 7, "failed": 0},
            "athlete_metrics": {"enabled": False},
            "warnings": [],
            "errors": [],
        },
    ]
    
    result = aggregate_stats(stats)
    assert result["daily_summary"]["enabled"] is True  # Should be True if any run enabled it
    assert result["daily_summary"]["total_days"] == 7


def test_aggregate_stats_athlete_metrics_mixed_results():
    """Test aggregation with mixed athlete metrics success/failure."""
    now = datetime.now(timezone.utc)
    stats = [
        {
            "timestamp": (now - timedelta(days=2)).isoformat(),
            "workouts": {"fetched": 5},
            "daily_summary": {"enabled": False},
            "athlete_metrics": {"enabled": True, "upserted": True, "failed": False},
            "warnings": [],
            "errors": [],
        },
        {
            "timestamp": (now - timedelta(days=1)).isoformat(),
            "workouts": {"fetched": 5},
            "daily_summary": {"enabled": False},
            "athlete_metrics": {"enabled": True, "upserted": False, "failed": True},
            "warnings": [],
            "errors": [],
        },
    ]
    
    result = aggregate_stats(stats)
    assert result["athlete_metrics"]["enabled"] is True
    assert result["athlete_metrics"]["total_upserted"] == 1
    assert result["athlete_metrics"]["total_failed"] == 1


def test_load_run_stats_missing_file(tmp_path):
    """Test loading stats when file doesn't exist."""
    from scripts.weekly_status_report import load_run_stats
    
    missing_file = tmp_path / "nonexistent.json"
    result = load_run_stats(missing_file)
    assert result == []


def test_load_run_stats_invalid_json(tmp_path):
    """Test loading stats with invalid JSON."""
    from scripts.weekly_status_report import load_run_stats
    
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("{ invalid json }")
    
    # Should return empty list and print error (non-fatal)
    result = load_run_stats(invalid_file)
    assert result == []


def test_load_run_stats_legacy_format(tmp_path):
    """Test loading legacy format (single dict instead of list)."""
    from scripts.weekly_status_report import load_run_stats
    
    legacy_file = tmp_path / "legacy.json"
    legacy_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workouts": {"fetched": 5},
    }
    legacy_file.write_text(json.dumps(legacy_data))
    
    result = load_run_stats(legacy_file)
    assert len(result) == 1
    assert result[0]["workouts"]["fetched"] == 5


def test_load_run_stats_empty_file(tmp_path):
    """Test loading empty stats file."""
    from scripts.weekly_status_report import load_run_stats
    
    empty_file = tmp_path / "empty.json"
    empty_file.write_text("{}")
    
    result = load_run_stats(empty_file)
    # Empty dict should return empty list
    assert result == []


