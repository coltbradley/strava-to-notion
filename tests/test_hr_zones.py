"""Tests for HR zone calculations."""
import pytest
from sync import StravaClient, SECONDS_PER_MINUTE


def test_compute_hr_zone_minutes_basic():
    """Test basic HR zone calculation."""
    zones = [
        {"min": 0, "max": 100},
        {"min": 100, "max": 150},
        {"min": 150, "max": None},
    ]
    
    # Create a simple HR stream: 60 seconds at 110 bpm (zone 2), 60 seconds at 160 bpm (zone 3)
    hr_stream = {
        "hr": [110, 110, 160, 160],
        "time": [0, 30, 60, 90],
    }
    
    result = StravaClient.compute_hr_zone_minutes(hr_stream, zones)
    
    assert result is not None
    assert result[1] == 0.0  # Zone 1: 0 seconds
    assert abs(result[2] - 1.0) < 0.01  # Zone 2: 60 seconds = 1 minute
    assert abs(result[3] - 0.5) < 0.01  # Zone 3: 30 seconds = 0.5 minutes


def test_compute_hr_zone_minutes_empty_stream():
    """Test HR zone calculation with empty stream."""
    zones = [{"min": 0, "max": 100}]
    hr_stream = {"hr": [], "time": []}
    
    result = StravaClient.compute_hr_zone_minutes(hr_stream, zones)
    assert result is None


def test_compute_hr_zone_minutes_mismatched_lengths():
    """Test HR zone calculation with mismatched stream lengths."""
    zones = [{"min": 0, "max": 100}]
    hr_stream = {"hr": [100, 100, 100], "time": [0, 30]}  # Mismatched
    
    result = StravaClient.compute_hr_zone_minutes(hr_stream, zones)
    # Should handle gracefully by truncating to min length
    assert result is not None


