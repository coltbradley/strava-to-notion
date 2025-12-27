"""Tests for unit conversion logic."""
import pytest
from sync import (
    METERS_TO_MILES,
    METERS_TO_FEET,
    METERS_PER_SECOND_TO_MPH,
    SECONDS_PER_MINUTE,
)


def test_meters_to_miles():
    """Test meters to miles conversion."""
    # 1609.34 meters = 1 mile
    assert abs(1609.34 * METERS_TO_MILES - 1.0) < 0.001


def test_meters_to_feet():
    """Test meters to feet conversion."""
    # 1 meter = 3.28084 feet
    assert abs(METERS_TO_FEET - 3.28084) < 0.001
    assert abs(1.0 * METERS_TO_FEET - 3.28084) < 0.001


def test_mps_to_mph():
    """Test meters per second to miles per hour conversion."""
    # 1 m/s = 2.23694 mph
    assert abs(METERS_PER_SECOND_TO_MPH - 2.236936) < 0.001


def test_seconds_per_minute():
    """Test seconds per minute constant."""
    assert SECONDS_PER_MINUTE == 60

