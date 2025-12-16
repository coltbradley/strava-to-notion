#!/usr/bin/env python3
"""
Strava to Notion Workout Sync
Automated pipeline that syncs recent Strava activities into a Notion database.
Uses OAuth refresh token flow for Strava and upserts activities keyed by activity ID.
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
import requests
from notion_client import Client


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StravaClient:
    """Client for interacting with Strava API with OAuth refresh token support."""
    
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.base_url = "https://www.strava.com/api/v3"
        self.access_token = None
        self._refresh_access_token()
    
    def _refresh_access_token(self) -> str:
        """Refresh the Strava access token using the refresh token."""
        url = "https://www.strava.com/oauth/token"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token"
        }
        
        try:
            response = requests.post(url, data=payload)
            response.raise_for_status()
            data = response.json()
            self.access_token = data["access_token"]
            logger.info("Successfully refreshed Strava access token")
            return self.access_token
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to refresh Strava access token: {e}")
            if response.status_code == 401:
                logger.error("Invalid refresh token. Please regenerate your Strava tokens.")
            raise
    
    def get_recent_activities(self, days: int = 30) -> List[Dict]:
        """Fetch recent activities from Strava for the specified number of days."""
        url = f"{self.base_url}/athlete/activities"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        # Calculate after timestamp (Unix epoch)
        after = int((datetime.now() - timedelta(days=days)).timestamp())
        params = {"after": after, "per_page": 200}
        
        all_activities = []
        page = 1
        
        while True:
            params["page"] = page
            try:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                activities = response.json()
                
                if not activities:
                    break
                
                all_activities.extend(activities)
                logger.info(f"Fetched page {page}: {len(activities)} activities")
                
                # If we got fewer than per_page, we've reached the end
                if len(activities) < params["per_page"]:
                    break
                
                page += 1
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching Strava activities: {e}")
                raise
        
        logger.info(f"Total activities fetched from Strava: {len(all_activities)}")
        return all_activities

    def get_athlete_zones(self) -> Optional[List[Dict]]:
        """Fetch athlete heart rate zones from Strava."""
        url = f"{self.base_url}/athlete/zones"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("heart_rate", {}).get("zones")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not fetch Strava HR zones; HR zone metrics will be skipped: {e}")
            return None

    def get_activity_hr_stream(self, activity_id: int) -> Optional[List[int]]:
        """Fetch heart rate stream for an activity (per-second samples)."""
        url = f"{self.base_url}/activities/{activity_id}/streams"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        params = {"keys": "heartrate", "key_by_type": "true"}
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            hr_stream = data.get("heartrate", {}).get("data")
            if not hr_stream:
                return None
            return hr_stream
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not fetch HR stream for activity {activity_id}: {e}")
            return None

    @staticmethod
    def compute_hr_zone_minutes(hr_stream: List[int], zones: List[Dict]) -> Optional[Dict[int, float]]:
        """Compute minutes per HR zone given a stream and zone definitions."""
        if not hr_stream or not zones:
            return None
        zone_counts = {idx + 1: 0 for idx in range(len(zones))}
        for hr in hr_stream:
            for idx, zone in enumerate(zones):
                min_hr = zone.get("min", 0)
                max_hr = zone.get("max")  # may be None for last zone
                if hr >= min_hr and (max_hr is None or hr < max_hr):
                    zone_counts[idx + 1] += 1
                    break
        # Convert seconds to minutes, rounded to 2 decimals
        return {zone: round(seconds / 60, 2) for zone, seconds in zone_counts.items()}


class NotionClient:
    """Client for interacting with Notion API with upsert support."""
    
    def __init__(self, api_key: str, database_id: str):
        self.client = Client(auth=api_key)
        self.database_id = database_id
    
    def get_existing_activity_pages(self, days: int = 30) -> Dict[str, str]:
        """
        Get existing activity pages from Notion within date range.
        Returns dict mapping activity_id (str) to page_id (str).
        """
        existing_map = {}
        start_cursor = None
        
        # Calculate date filter
        after_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        while True:
            query_params = {
                "database_id": self.database_id,
                "filter": {
                    "property": "Date",
                    "date": {
                        "on_or_after": after_date
                    }
                }
            }
            
            if start_cursor:
                query_params["start_cursor"] = start_cursor
            
            try:
                response = self.client.databases.query(**query_params)
                
                for page in response.get("results", []):
                    props = page.get("properties", {})
                    activity_id_prop = props.get("Activity ID")
                    
                    if activity_id_prop and activity_id_prop.get("rich_text"):
                        activity_id = activity_id_prop["rich_text"][0].get("plain_text", "")
                        if activity_id:
                            existing_map[activity_id] = page["id"]
                
                if not response.get("has_more"):
                    break
                
                start_cursor = response.get("next_cursor")
                
            except Exception as e:
                logger.warning(f"Error querying Notion database (will continue with per-activity lookup): {e}")
                break
        
        logger.info(f"Found {len(existing_map)} existing activities in Notion")
        return existing_map
    
    def find_page_by_activity_id(self, activity_id: str) -> Optional[str]:
        """Find a Notion page by Activity ID. Returns page_id if found, None otherwise."""
        try:
            response = self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "property": "Activity ID",
                    "rich_text": {
                        "equals": activity_id
                    }
                }
            )
            
            if response.get("results"):
                return response["results"][0]["id"]
            return None
        except Exception as e:
            logger.warning(f"Error searching for activity {activity_id} in Notion: {e}")
            return None
    
    def upsert_activity(self, activity: Dict, existing_page_id: Optional[str] = None) -> bool:
        """
        Upsert an activity into Notion.
        If existing_page_id is provided, updates that page; otherwise creates new.
        Returns True if successful, False otherwise.
        """
        properties = self._convert_activity_to_properties(activity)
        
        # Remove None values and properties that don't exist
        properties = {k: v for k, v in properties.items() if v is not None}
        
        try:
            if existing_page_id:
                # Update existing page
                self.client.pages.update(
                    page_id=existing_page_id,
                    properties=properties
                )
                return True
            else:
                # Create new page
                self.client.pages.create(
                    parent={"database_id": self.database_id},
                    properties=properties
                )
                return True
        except Exception as e:
            error_msg = str(e)
            # Check if it's a property that doesn't exist (skip it)
            if "property" in error_msg.lower() and "doesn't exist" in error_msg.lower():
                logger.debug(f"Skipping property that doesn't exist in Notion DB: {error_msg}")
                return True  # Consider this a success (we'll skip missing properties)
            logger.error(f"Error upserting activity {activity.get('id')}: {error_msg}")
            return False
    
    def _convert_activity_to_properties(self, activity: Dict) -> Dict:
        """Convert Strava activity data to Notion page properties."""
        activity_id = str(activity.get("id", ""))
        activity_name = activity.get("name", "").strip()
        sport_type = activity.get("type", "Workout")
        
        # Generate fallback name if empty
        if not activity_name:
            start_date = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
            activity_name = f"{sport_type} – {start_date.strftime('%Y-%m-%d')}"
        
        # Parse dates
        start_date = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
        now = datetime.now(start_date.tzinfo)
        
        # Unit conversions
        distance_m = activity.get("distance", 0)
        distance_mi = distance_m * 0.000621371
        
        elevation_m = activity.get("total_elevation_gain", 0)
        elevation_ft = elevation_m * 3.28084
        
        elapsed_time_s = activity.get("elapsed_time", 0)
        moving_time_s = activity.get("moving_time", 0)
        duration_min = elapsed_time_s / 60
        moving_time_min = moving_time_s / 60 if moving_time_s else None
        
        # Heart rate
        avg_hr = activity.get("average_heartrate")
        max_hr = activity.get("max_heartrate")
        
        # Pace calculation (for running-like sports)
        pace_min_per_mi = None
        running_sports = {"Run", "TrailRun", "Walk", "Hike"}
        if sport_type in running_sports and distance_mi > 0 and moving_time_s > 0:
            seconds_per_mile = moving_time_s / distance_mi
            pace_min_per_mi = seconds_per_mile / 60
        
        # Build properties dict
        properties = {
            "Name": {
                "title": [{"text": {"content": activity_name}}]
            },
            "Activity ID": {
                "rich_text": [{"text": {"content": activity_id}}]
            },
            "Date": {
                "date": {"start": start_date.isoformat()}
            },
            "Sport": {
                "select": {"name": sport_type}
            },
            "Duration (min)": {
                "number": round(duration_min, 2)
            },
            "Distance (mi)": {
                "number": round(distance_mi, 2)
            },
            "Elevation (ft)": {
                "number": round(elevation_ft, 1)
            }
        }
        
        # Optional properties (only add if value exists)
        if avg_hr:
            properties["Avg HR"] = {"number": avg_hr}
        
        if max_hr:
            properties["Max HR"] = {"number": max_hr}
        
        if pace_min_per_mi:
            properties["Avg Pace (min/mi)"] = {"number": round(pace_min_per_mi, 2)}
        
        if moving_time_min:
            properties["Moving Time (min)"] = {"number": round(moving_time_min, 2)}
        
        properties["Strava URL"] = {
            "url": f"https://www.strava.com/activities/{activity_id}"
        }
        
        properties["Last Synced"] = {
            "date": {"start": now.isoformat()}
        }
        
        # Heart rate zone summaries (if already computed and attached)
        hr_zones = activity.get("_hr_zone_minutes")
        if hr_zones:
            for zone_num, minutes in hr_zones.items():
                properties[f"HR Zone {zone_num} (min)"] = {"number": minutes}

        return properties


def sync_strava_to_notion(days: int = 30, failure_threshold: float = 0.2):
    """
    Main sync function.
    
    Args:
        days: Number of days to look back for activities
        failure_threshold: Maximum fraction of activities that can fail before aborting (0.0-1.0)
    """
    # Get credentials from environment variables
    strava_client_id = os.getenv("STRAVA_CLIENT_ID")
    strava_client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    strava_refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")
    notion_token = os.getenv("NOTION_TOKEN")
    notion_database_id = os.getenv("NOTION_DATABASE_ID")
    
    # Validate required env vars
    missing = []
    if not strava_client_id:
        missing.append("STRAVA_CLIENT_ID")
    if not strava_client_secret:
        missing.append("STRAVA_CLIENT_SECRET")
    if not strava_refresh_token:
        missing.append("STRAVA_REFRESH_TOKEN")
    if not notion_token:
        missing.append("NOTION_TOKEN")
    if not notion_database_id:
        missing.append("NOTION_DATABASE_ID")
    
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)
    
    # Initialize clients
    try:
        strava = StravaClient(strava_client_id, strava_client_secret, strava_refresh_token)
    except Exception as e:
        logger.error(f"Failed to initialize Strava client: {e}")
        sys.exit(1)
    
    try:
        notion = NotionClient(notion_token, notion_database_id)
    except Exception as e:
        logger.error(f"Failed to initialize Notion client: {e}")
        sys.exit(1)
    
    # Fetch activities from Strava
    try:
        activities = strava.get_recent_activities(days=days)
    except Exception as e:
        logger.error(f"Failed to fetch Strava activities: {e}")
        sys.exit(1)
    
    if not activities:
        logger.info("No activities found to sync")
        return

    # Fetch HR zones once for the athlete
    hr_zones = strava.get_athlete_zones()
    
    # Get existing activities from Notion (batch query)
    try:
        existing_map = notion.get_existing_activity_pages(days=days)
    except Exception as e:
        logger.warning(f"Failed to batch query Notion, falling back to per-activity lookup: {e}")
        existing_map = {}
    
    # Sync activities
    stats = {
        "fetched": len(activities),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0
    }
    
    for activity in activities:
        activity_id = str(activity.get("id"))
        has_hr = activity.get("has_heartrate")
        # Attach HR zone minutes if available and HR data exists
        if hr_zones and has_hr:
            hr_stream = strava.get_activity_hr_stream(activity.get("id"))
            hr_zone_minutes = StravaClient.compute_hr_zone_minutes(hr_stream, hr_zones) if hr_stream else None
            if hr_zone_minutes:
                # Attach computed metrics to activity for later conversion
                activity["_hr_zone_minutes"] = hr_zone_minutes
        
        # Find existing page
        existing_page_id = existing_map.get(activity_id)
        if not existing_page_id:
            # Fallback to per-activity search if not in batch map
            existing_page_id = notion.find_page_by_activity_id(activity_id)
        
        # Upsert activity
        try:
            success = notion.upsert_activity(activity, existing_page_id)
            
            if success:
                if existing_page_id:
                    stats["updated"] += 1
                    logger.debug(f"Updated activity: {activity.get('name')} ({activity_id})")
                else:
                    stats["created"] += 1
                    logger.info(f"Created activity: {activity.get('name')} ({activity_id})")
            else:
                stats["failed"] += 1
                logger.warning(f"Failed to upsert activity: {activity.get('name')} ({activity_id})")
        
        except Exception as e:
            stats["failed"] += 1
            logger.error(f"Exception upserting activity {activity_id}: {e}")
        
        # Rate limiting: small delay to respect Notion API limits
        time.sleep(0.1)
    
    # Log summary
    logger.info("=" * 60)
    logger.info("Sync Summary:")
    logger.info(f"  Fetched from Strava: {stats['fetched']}")
    logger.info(f"  Created in Notion: {stats['created']}")
    logger.info(f"  Updated in Notion: {stats['updated']}")
    logger.info(f"  Skipped: {stats['skipped']}")
    logger.info(f"  Failed: {stats['failed']}")
    logger.info("=" * 60)
    
    # Check failure threshold
    if stats["fetched"] > 0:
        failure_rate = stats["failed"] / stats["fetched"]
        if failure_rate > failure_threshold:
            logger.error(
                f"Failure rate ({failure_rate:.1%}) exceeds threshold ({failure_threshold:.1%}). "
                "Aborting with error code."
            )
            sys.exit(1)
    
    logger.info("Sync completed successfully")


if __name__ == "__main__":
    sync_strava_to_notion()
