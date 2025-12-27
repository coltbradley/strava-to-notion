#!/usr/bin/env python3
"""
Update Weather Data for All Past Activities

This script queries all activities from the Notion Workouts database and updates
weather data for any outdoor activities that are missing weather information.

It uses the activity's date/time and location (start_latitude/start_longitude) from
Strava to fetch historical weather data.

Usage:
    python scripts/update_weather.py [--days N] [--dry-run]

Options:
    --days N: Only update activities from the last N days (default: all activities, but still limited to 90 most recent)
    --dry-run: Show what would be updated without actually updating Notion

Note: By default, this script processes the 90 most recent activities to balance API usage and coverage.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent directory to path to import from sync.py
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
    NOTION_AVAILABLE = True
except ImportError:
    print("Error: notion-client not available. Install with: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

from sync import (
    NOTION_SCHEMA,
    WeatherClient,
    NotionClient,
    INDOOR_SPORTS,
    _notion_database_query_http,
    logger,
)


def get_all_activities(
    notion_token: str,
    workouts_db_id: str,
    max_days: Optional[int] = None,
    max_activities: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Get activities from Notion database, optionally filtered by date and limited by count.
    
    Returns list of activity page dicts with properties.
    """
    activities = []
    start_cursor = None
    
    # Build date filter if max_days specified
    date_filter = None
    if max_days:
        after_date = (datetime.now(timezone.utc) - timedelta(days=max_days)).date().isoformat()
        date_filter = {
            "property": NOTION_SCHEMA["date"],
            "date": {
                "on_or_after": after_date
            }
        }
    
    logger.info(f"Fetching activities from Notion (max_days={max_days or 'all'}, max_activities={max_activities or 'all'})...")
    
    while True:
        # Stop if we've reached the max_activities limit
        if max_activities and len(activities) >= max_activities:
            break
        query_params: Dict[str, Any] = {
            "sorts": [
                {
                    "property": NOTION_SCHEMA["date"],
                    "direction": "descending"  # Most recent first
                }
            ],
        }
        
        if date_filter:
            query_params["filter"] = date_filter
        
        if start_cursor:
            query_params["start_cursor"] = start_cursor
        
        try:
            response = _notion_database_query_http(
                notion_token,
                workouts_db_id,
                **query_params
            )
            
            for page in response.get("results", []):
                activities.append(page)
                # Stop if we've reached the max_activities limit
                if max_activities and len(activities) >= max_activities:
                    break
            
            if not response.get("has_more"):
                break
            
            start_cursor = response.get("next_cursor")
            
        except Exception as e:
            logger.error(f"Error querying Notion database: {e}")
            break
    
    # Truncate to max_activities if we exceeded it (safety check)
    if max_activities and len(activities) > max_activities:
        activities = activities[:max_activities]
    
    logger.info(f"Found {len(activities)} activities in Notion")
    return activities


def extract_activity_info(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract relevant info from a Notion page for weather lookup.
    
    Returns dict with activity_id, name, date, sport, lat, lng, existing_weather
    or None if activity is indoor or missing required data.
    """
    props = page.get("properties", {})
    
    # Get sport type
    sport_prop = props.get(NOTION_SCHEMA["sport"])
    if not sport_prop:
        return None
    
    sport_select = sport_prop.get("select")
    if not sport_select:
        return None
    
    sport_type = sport_select.get("name")
    if not sport_type or sport_type in INDOOR_SPORTS:
        # Skip indoor activities
        return None
    
    # Get date
    date_prop = props.get(NOTION_SCHEMA["date"])
    if not date_prop or not date_prop.get("date"):
        return None
    
    date_str = date_prop["date"].get("start")
    if not date_str:
        return None
    
    # Get activity ID and name (for logging)
    activity_id_prop = props.get(NOTION_SCHEMA["activity_id"])
    activity_id = None
    if activity_id_prop and activity_id_prop.get("rich_text"):
        activity_id = activity_id_prop["rich_text"][0].get("plain_text", "")
    
    name_prop = props.get(NOTION_SCHEMA["name"])
    name = None
    if name_prop and name_prop.get("title"):
        name = name_prop["title"][0].get("plain_text", "")
    
    # Check existing weather
    temp_prop = props.get(NOTION_SCHEMA["temperature_f"])
    weather_prop = props.get(NOTION_SCHEMA["weather_conditions"])
    has_weather = (
        temp_prop and temp_prop.get("number") is not None
    ) or (
        weather_prop and weather_prop.get("rich_text") and 
        len(weather_prop["rich_text"]) > 0
    )
    
    # For location, we need to fetch from Strava (since Notion doesn't store lat/lng)
    # We'll use the activity_id to fetch from Strava API
    return {
        "page_id": page["id"],
        "activity_id": activity_id,
        "name": name,
        "date_str": date_str,
        "sport": sport_type,
        "has_weather": has_weather,
    }


def fetch_location_from_strava(
    activity_id: str,
    strava_client: Any,  # StravaClient instance (reused across calls)
) -> Optional[Tuple[float, float]]:
    """
    Fetch activity location (start_latitude, start_longitude) from Strava.
    
    Args:
        activity_id: Strava activity ID
        strava_client: StravaClient instance (reused to avoid token refresh overhead)
    
    Returns (lat, lng) tuple or None if not available.
    """
    # Import here to avoid circular imports
    from sync import http_request_with_retries
    
    try:
        # Fetch single activity using Strava API
        url = f"https://www.strava.com/api/v3/activities/{activity_id}"
        headers = {"Authorization": f"Bearer {strava_client.access_token}"}
        
        response = http_request_with_retries("GET", url, headers=headers)
        activity = response.json()
        
        if not activity:
            return None
        
        # Strava API uses start_latlng (array format [lat, lng]) as the primary field
        # Check start_latlng first (this is the standard field in Strava API)
        start_latlng = activity.get("start_latlng")
        if start_latlng and len(start_latlng) >= 2 and start_latlng[0] and start_latlng[1]:
            return (float(start_latlng[0]), float(start_latlng[1]))
        
        # Fallback to separate fields (some API versions might use these)
        lat = activity.get("start_latitude")
        lng = activity.get("start_longitude")
        if lat and lng:
            return (float(lat), float(lng))
        
        return None
    except Exception as e:
        logger.warning(f"Error fetching location from Strava for activity {activity_id}: {e}")
        return None


def update_activity_weather(
    notion_client: NotionClient,
    page_id: str,
    activity_id: str,
    name: str,
    date_str: str,
    latitude: float,
    longitude: float,
    dry_run: bool = False,
) -> bool:
    """
    Fetch weather for an activity and update its Notion page.
    
    Returns True if successful, False otherwise.
    """
    try:
        # Parse date
        # Handle both ISO format and date-only format
        if "T" in date_str:
            start_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            # Date-only format, assume UTC midnight
            start_date = datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
        
        # Fetch weather
        weather_client = WeatherClient(os.getenv("WEATHER_API_KEY"))
        logger.info(f"Fetching weather for activity {activity_id} ({name}) at ({latitude}, {longitude}) on {start_date.date()}")
        
        weather = weather_client.get_weather_for_activity(latitude, longitude, start_date)
        
        if not weather:
            logger.warning(f"No weather data returned for activity {activity_id}")
            return False
        
        # Build properties dict - but filter against schema first
        all_properties = {}
        
        temp_f = weather.get("temp_f")
        if temp_f is not None:
            all_properties[NOTION_SCHEMA["temperature_f"]] = {"number": round(temp_f, 1)}
        
        weather_summary = WeatherClient.make_weather_summary(weather)
        if weather_summary:
            all_properties[NOTION_SCHEMA["weather_conditions"]] = {
                "rich_text": [{"text": {"content": weather_summary}}]
            }
        
        if not all_properties:
            logger.warning(f"No weather properties to update for activity {activity_id}")
            return False
        
        # Filter properties against schema (only write properties that exist in the database)
        allowed_properties = notion_client._ensure_schema_loaded()
        properties = {}
        
        if allowed_properties is not None:
            # Schema-aware filtering: only include properties that exist in the database
            for prop_name, prop_value in all_properties.items():
                if prop_name in allowed_properties:
                    properties[prop_name] = prop_value
                else:
                    logger.debug(f"Skipping property '{prop_name}' - not in database schema")
        else:
            # Schema loading failed or returned 0 properties - don't try to write
            # This prevents 400 errors when properties don't exist
            logger.warning(
                f"Schema loading failed or found 0 properties for activity {activity_id}. "
                "Skipping weather update to avoid property errors. "
                "Please ensure your Notion database has 'Temperature (°F)' and 'Weather Conditions' properties."
            )
            return False
        
        if not properties:
            logger.warning(f"No weather properties to write after schema filtering for activity {activity_id}")
            return False
        
        if dry_run:
            logger.info(f"[DRY RUN] Would update activity {activity_id} with weather: {weather_summary}")
            return True
        
        # Update Notion page (use NotionClient's retry wrapper)
        try:
            from notion_client.errors import APIResponseError
            notion_client._notion_call_with_retries(
                notion_client.client.pages.update,
                page_id=page_id,
                properties=properties,
            )
        except APIResponseError as e:
            error_msg = str(e)
            status = getattr(e, "status", None)
            # Handle 400 errors that indicate a missing property
            if status == 400:
                if "property" in error_msg.lower() and ("doesn't exist" in error_msg.lower() or "not a property" in error_msg.lower()):
                    logger.warning(
                        f"Property doesn't exist in database for activity {activity_id}: {error_msg}. "
                        "Please ensure your Notion database has 'Temperature (°F)' (Number) and "
                        "'Weather Conditions' (Rich text) properties."
                    )
                    return False
            # Re-raise other API errors
            raise
        
        logger.info(f"Updated weather for activity {activity_id}: {weather_summary}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating weather for activity {activity_id}: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Update weather data for the 3 most recent activities in Notion"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only update activities from the last N days (default: all activities, but still limited to 90 most recent)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without actually updating Notion",
    )
    
    args = parser.parse_args()
    
    # Get environment variables
    notion_token = os.getenv("NOTION_TOKEN")
    workouts_db_id = os.getenv("NOTION_DATABASE_ID")
    strava_client_id = os.getenv("STRAVA_CLIENT_ID")
    strava_client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    strava_refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")
    
    if not notion_token:
        print("Error: NOTION_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    if not workouts_db_id:
        print("Error: NOTION_DATABASE_ID environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    if not all([strava_client_id, strava_client_secret, strava_refresh_token]):
        print("Error: Strava credentials not set (STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN)", file=sys.stderr)
        sys.exit(1)
    
    # Get activities (limit to 90 most recent)
    activities = get_all_activities(notion_token, workouts_db_id, max_days=args.days, max_activities=90)
    
    if not activities:
        logger.info("No activities found")
        sys.exit(0)
    
    # Initialize Notion client
    notion_client = NotionClient(notion_token, workouts_db_id)
    
    # Initialize Strava client once (reuse to avoid token refresh overhead)
    from sync import StravaClient
    strava_client = StravaClient(strava_client_id, strava_client_secret, strava_refresh_token)
    
    # Process activities
    stats = {
        "total": len(activities),
        "outdoor": 0,
        "missing_location": 0,
        "updated": 0,
        "failed": 0,
        "skipped_has_weather": 0,
    }
    
    logger.info(f"Processing {stats['total']} activities...")
    
    for page in activities:
        activity_info = extract_activity_info(page)
        
        if not activity_info:
            continue
        
        stats["outdoor"] += 1
        
        # Skip if already has weather (unless --force, but we don't have that yet)
        if activity_info["has_weather"]:
            stats["skipped_has_weather"] += 1
            logger.debug(f"Skipping activity {activity_info['activity_id']} - already has weather")
            continue
        
        # Fetch location from Strava
        if not activity_info["activity_id"]:
            stats["missing_location"] += 1
            logger.warning(f"Activity {activity_info['name']} has no Activity ID, cannot fetch location")
            continue
        
        location = fetch_location_from_strava(
            activity_info["activity_id"],
            strava_client,  # Reuse the same client instance
        )
        
        if not location:
            stats["missing_location"] += 1
            logger.warning(f"Activity {activity_info['activity_id']} has no location data in Strava")
            continue
        
        lat, lng = location
        
        # Update weather
        success = update_activity_weather(
            notion_client,
            activity_info["page_id"],
            activity_info["activity_id"],
            activity_info["name"],
            activity_info["date_str"],
            lat,
            lng,
            dry_run=args.dry_run,
        )
        
        if success:
            stats["updated"] += 1
        else:
            stats["failed"] += 1
    
    # Print summary
    logger.info("=" * 60)
    logger.info("Weather Update Summary:")
    logger.info(f"  Total activities: {stats['total']}")
    logger.info(f"  Outdoor activities: {stats['outdoor']}")
    logger.info(f"  Already have weather: {stats['skipped_has_weather']}")
    logger.info(f"  Missing location: {stats['missing_location']}")
    if args.dry_run:
        logger.info(f"  Would update: {stats['updated']}")
        logger.info(f"  Would fail: {stats['failed']}")
    else:
        logger.info(f"  Updated: {stats['updated']}")
        logger.info(f"  Failed: {stats['failed']}")
    logger.info("=" * 60)
    
    sys.exit(0)


if __name__ == "__main__":
    main()

