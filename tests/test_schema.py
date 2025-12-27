"""Tests for Notion schema constants."""
import pytest
from sync import NOTION_SCHEMA, SYSTEM_OWNED_FIELDS


def test_schema_constants_exist():
    """Test that all required schema constants are defined."""
    required_keys = [
        "name",
        "activity_id",
        "date",
        "sport",
        "duration_min",
        "distance_mi",
        "elevation_ft",
        "strava_url",
        "last_synced",
        "avg_hr",
        "max_hr",
        "temperature_f",
        "weather_conditions",
    ]
    
    for key in required_keys:
        assert key in NOTION_SCHEMA, f"Missing schema key: {key}"


def test_system_owned_fields_include_all_schema():
    """Test that SYSTEM_OWNED_FIELDS includes all system properties."""
    # All schema properties except user-added ones should be in SYSTEM_OWNED_FIELDS
    system_keys = [
        "name",
        "activity_id",
        "date",
        "sport",
        "duration_min",
        "distance_mi",
        "elevation_ft",
        "strava_url",
        "last_synced",
        "avg_hr",
        "max_hr",
        "temperature_f",
        "weather_conditions",
    ]
    
    for key in system_keys:
        property_name = NOTION_SCHEMA[key]
        assert property_name in SYSTEM_OWNED_FIELDS, f"System property {property_name} not in SYSTEM_OWNED_FIELDS"


def test_hr_zone_schema_format():
    """Test that HR zone schema template can be formatted correctly."""
    from sync import NOTION_SCHEMA
    
    zone_prop = NOTION_SCHEMA["hr_zone_min"]
    formatted = zone_prop.format(zone=1)
    assert formatted == "HR Zone 1 (min)"
    
    # Test all zones
    for zone in range(1, 6):
        formatted = zone_prop.format(zone=zone)
        assert formatted == f"HR Zone {zone} (min)"


