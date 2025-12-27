#!/usr/bin/env python3
"""
Strava to Notion Workout Sync
Automated pipeline that syncs recent Strava activities into a Notion database.
Uses OAuth refresh token flow for Strava and upserts activities keyed by activity ID.
"""

import os
import sys
import time
import json
import logging
import random
import hashlib
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Callable, Any
from pathlib import Path

import requests
from notion_client import Client
from notion_client.errors import APIResponseError


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Sports where pace / drift analysis makes sense
PACE_SPORTS = {"Run", "TrailRun", "Walk", "Hike", "VirtualRun"}

# Sports that are always indoors (skip weather lookup)
INDOOR_SPORTS = {"WeightTraining", "Workout", "Crossfit"}

# Cardio sports eligible for load computation (zone-weighted training load)
# Only workouts with Sport in this set can contribute load points
CARDIO_SPORTS = {"Run", "Hike", "StairStepper", "TrailRun", "Walk", "VirtualRun"}

# Constants for timeouts, retries, and backoff
HTTP_TIMEOUT_SECONDS = 30
HTTP_MAX_RETRIES = 3
HTTP_BACKOFF_FACTOR = 1.0
HTTP_BACKOFF_JITTER_MAX = 0.25
# Special backoff for rate limits (429) - longer delay since we've hit a limit
HTTP_RATE_LIMIT_BACKOFF_SECONDS = 60  # Wait 60 seconds before retrying rate limit errors
NOTION_RATE_LIMIT_DELAY_SECONDS = 0.1

# Constants for unit conversions
METERS_TO_MILES = 0.000621371
METERS_TO_FEET = 3.28084
METERS_PER_SECOND_TO_MPH = 2.236936
SECONDS_PER_MINUTE = 60

# Constants for HR drift eligibility
DRIFT_MIN_MOVING_TIME_MINUTES = 20
DRIFT_MIN_DISTANCE_MILES = 3.0
DRIFT_MIN_HR_SAMPLES = 120
DRIFT_MIN_DURATION_FRACTION = 0.8
DRIFT_MIN_DURATION_SECONDS_FALLBACK = 10 * 60
DRIFT_MIN_VELOCITY_THRESHOLD_MPS = 0.1

# Constants for sync configuration
DEFAULT_SYNC_DAYS = 30
DEFAULT_FAILURE_THRESHOLD = 0.2

# Notion database schema - property names
# Source of truth for all Notion property names used by the sync script
# See NOTION_PROPERTIES.md for complete documentation
NOTION_SCHEMA = {
    # Required properties
    "name": "Name",
    "activity_id": "Activity ID",
    "date": "Date",
    "sport": "Sport",
    "duration_min": "Duration (min)",
    "distance_mi": "Distance (mi)",
    "elevation_ft": "Elevation (ft)",
    # Strongly recommended
    "strava_url": "Strava URL",
    "last_synced": "Last Synced",
    # Optional metrics
    "avg_hr": "Avg HR",
    "max_hr": "Max HR",
    "avg_pace_min_per_mi": "Avg Pace (min/mi)",
    "moving_time_min": "Moving Time (min)",
    # Optional HR zones (dynamically generated)
    "hr_zone_min": "HR Zone {zone} (min)",  # Template: format with zone number
    # Optional drift metrics
    "hr_drift_pct": "HR Drift (%)",
    "hr_1st_half_bpm": "HR 1st Half (bpm)",
    "hr_2nd_half_bpm": "HR 2nd Half (bpm)",
    "speed_1st_half_mph": "Speed 1st Half (mph)",
    "speed_2nd_half_mph": "Speed 2nd Half (mph)",
    "drift_eligible": "Drift Eligible",
    "hr_data_quality": "HR Data Quality",
    # Optional weather
    "temperature_f": "Temperature (°F)",
    "weather_conditions": "Weather Conditions",
    # Optional ops
    "sync_status": "Sync Status",
    # Optional photos
    "photo_url": "Photo URL",
    # Optional load
    "load_pts": "Load (pts)",
}

# System-owned fields that are safe to overwrite on updates
# These fields are always synced from Strava and can be updated
# Note: HR zone fields are generated dynamically but are also system-owned
SYSTEM_OWNED_FIELDS = {
    NOTION_SCHEMA["name"],
    NOTION_SCHEMA["activity_id"],
    NOTION_SCHEMA["date"],
    NOTION_SCHEMA["sport"],
    NOTION_SCHEMA["duration_min"],
    NOTION_SCHEMA["distance_mi"],
    NOTION_SCHEMA["elevation_ft"],
    NOTION_SCHEMA["strava_url"],
    NOTION_SCHEMA["last_synced"],
    NOTION_SCHEMA["avg_hr"],
    NOTION_SCHEMA["max_hr"],
    NOTION_SCHEMA["avg_pace_min_per_mi"],
    NOTION_SCHEMA["moving_time_min"],
    NOTION_SCHEMA["hr_drift_pct"],
    NOTION_SCHEMA["hr_1st_half_bpm"],
    NOTION_SCHEMA["hr_2nd_half_bpm"],
    NOTION_SCHEMA["speed_1st_half_mph"],
    NOTION_SCHEMA["speed_2nd_half_mph"],
    NOTION_SCHEMA["drift_eligible"],
    NOTION_SCHEMA["hr_data_quality"],
    NOTION_SCHEMA["temperature_f"],
    NOTION_SCHEMA["weather_conditions"],
    NOTION_SCHEMA["sync_status"],
    NOTION_SCHEMA["photo_url"],
    NOTION_SCHEMA["load_pts"],
}
# HR zones are system-owned but generated dynamically - add them explicitly
for zone in range(1, 6):
    SYSTEM_OWNED_FIELDS.add(NOTION_SCHEMA["hr_zone_min"].format(zone=zone))

# Daily Summary database schema (optional)
DAILY_SUMMARY_SCHEMA = {
    "date": "Date",
    "total_duration_min": "Total Duration (min)",
    "total_moving_time_min": "Total Moving Time (min)",
    "total_distance_mi": "Total Distance (mi)",
    "total_elevation_ft": "Total Elevation (ft)",
    "session_count": "Session Count",
    "load_pts": "Load (pts)",
    "load_confidence": "Load Confidence",
    "notes": "Notes",
}

# Athlete Metrics database schema (optional)
ATHLETE_METRICS_SCHEMA = {
    "name": "Name",
    "updated_at": "Updated At",
    "load_7d": "Load 7d",
    "load_28d": "Load 28d",
    "load_balance": "Load Balance",
    "ethr_bpm": "Estimated Threshold HR (bpm)",
    "ethr_confidence": "ETHR Confidence",
    "ethr_sample_count": "ETHR Sample Count",
    "pace_ethr_min_per_mi": "Pace @ ETHR (min/mi)",
    "pace_ethr_confidence": "Pace @ ETHR Confidence",
    "pace_ethr_sample_count": "Pace @ ETHR Sample Count",
    "notes": "Notes",
}


def _token_fingerprint(token: str) -> str:
    """Return a short, non-reversible fingerprint for a token for debugging."""
    if not token:
        return "none"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]


def http_request_with_retries(
    method: str,
    url: str,
    *,
    max_retries: int = HTTP_MAX_RETRIES,
    backoff_factor: float = HTTP_BACKOFF_FACTOR,
    timeout: int = HTTP_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> requests.Response:
    """
    Make an HTTP request with basic retry + exponential backoff.

    Retries on:
      - 429
      - 5xx
      - timeouts / connection errors
    """
    retry_statuses = {429, 500, 502, 503, 504}
    attempts = 0
    last_exc: Optional[requests.exceptions.RequestException] = None

    while attempts <= max_retries:
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            status = response.status_code

            # Retryable statuses
            if status in retry_statuses:
                raise requests.exceptions.HTTPError(
                    f"Retryable HTTP error {status} for {method} {url}",
                    response=response,
                )

            # Non-retryable HTTP errors should fail fast
            if status >= 400:
                # Include a small snippet of the response for debugging
                snippet = (response.text or "").strip()
                if len(snippet) > 500:
                    snippet = snippet[:500] + "..."
                raise requests.exceptions.HTTPError(
                    f"Non-retryable HTTP error {status} for {method} {url}: {snippet}",
                    response=response,
                )

            return response
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError) as e:
            last_exc = e
            attempts += 1
            response = getattr(e, "response", None)
            status = response.status_code if response is not None else None

            # If this was a non-retryable HTTPError (e.g., 400/401/403), don't retry
            if isinstance(e, requests.exceptions.HTTPError) and status is not None and status not in retry_statuses:
                raise

            if attempts > max_retries:
                logger.error(
                    f"HTTP request failed after {attempts} attempts "
                    f"for {method} {url} (status={status})"
                )
                raise

            # Backoff with jitter
            # For rate limits (429), use longer fixed delays to avoid hitting limits repeatedly
            if status == 429:
                # Strava rate limits are typically 100 requests/15min or 1000/day
                # Use progressively longer delays: 60s, 120s, 180s
                sleep_seconds = HTTP_RATE_LIMIT_BACKOFF_SECONDS * attempts
                logger.warning(
                    f"Strava API rate limit (429) - waiting {sleep_seconds:.0f} seconds before retry "
                    f"{attempts}/{max_retries}. If this persists, you may have exceeded your daily/hourly "
                    f"rate limit. Consider reducing sync frequency or waiting before next run."
                )
            else:
                # For other retryable errors (5xx), use exponential backoff
                sleep_seconds = backoff_factor * (2 ** (attempts - 1))
                sleep_seconds += random.uniform(0, HTTP_BACKOFF_JITTER_MAX)
                logger.warning(
                    f"Retrying {method} {url} after error (attempt {attempts}/{max_retries}, "
                    f"status={status}): {e}"
                )
            time.sleep(sleep_seconds)
        except requests.exceptions.RequestException as e:
            # Non-retryable request exception
            raise

    # Should not reach here
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Unknown error making request to {url}")


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
            "grant_type": "refresh_token",
        }

        logger.info(
            "Refreshing Strava access token (client_id=%s, refresh_fingerprint=%s)",
            self.client_id,
            _token_fingerprint(self.refresh_token),
        )

        try:
            response = http_request_with_retries("POST", url, data=payload)
            data = response.json()

            access_token = data.get("access_token")
            if not access_token:
                # Strava commonly returns {"message":..., "errors":...} or {"error":..., "message":...}
                logger.error(
                    "Strava token refresh response did not include access_token. "
                    "Status=%s Response=%s",
                    getattr(response, "status_code", None),
                    data,
                )
                raise ValueError("Strava token refresh failed: missing access_token")

            self.access_token = access_token
            logger.info(
                "Successfully refreshed Strava access token "
                "(access_fingerprint=%s, scope=%s)",
                _token_fingerprint(self.access_token),
                data.get("scope"),
            )
            return self.access_token
        except requests.exceptions.RequestException as e:
            resp = getattr(e, "response", None)
            status = resp.status_code if resp is not None else None
            logger.error(
                f"Failed to refresh Strava access token "
                f"(status={status}): {e}"
            )
            if status == 401:
                logger.error(
                    "Received 401 from Strava token endpoint. "
                    "Your refresh token may be invalid or revoked."
                )
            raise
    
    def get_recent_activities(self, days: int = DEFAULT_SYNC_DAYS) -> List[Dict]:
        """Fetch recent activities from Strava for the specified number of days."""
        url = f"{self.base_url}/athlete/activities"
        headers = {"Authorization": f"Bearer {self.access_token}"}

        # Calculate 'after' timestamp in UTC (Unix epoch)
        after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        params = {"after": after, "per_page": 200}
        
        all_activities = []
        page = 1
        
        while True:
            params["page"] = page
            try:
                response = http_request_with_retries(
                    "GET", url, headers=headers, params=params
                )
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
                resp = getattr(e, "response", None)
                status = resp.status_code if resp is not None else None
                # Optional: refresh token once on mid-run 401 and retry this page
                if status == 401:
                    logger.warning(
                        "Received 401 while fetching activities; "
                        "refreshing access token and retrying page once."
                    )
                    self._refresh_access_token()
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    # One retry only; if it fails again, bubble up
                    response = http_request_with_retries(
                        "GET", url, headers=headers, params=params
                    )
                    activities = response.json()
                    if not activities:
                        break
                    all_activities.extend(activities)
                    logger.info(f"Fetched page {page}: {len(activities)} activities")
                    if len(activities) < params["per_page"]:
                        break
                    page += 1
                    continue

                logger.error(f"Error fetching Strava activities: {e}")
                raise
        
        logger.info(f"Total activities fetched from Strava: {len(all_activities)}")
        return all_activities

    def get_athlete_zones(self) -> Optional[List[Dict]]:
        """Fetch athlete heart rate zones from Strava."""
        url = f"{self.base_url}/athlete/zones"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            response = http_request_with_retries("GET", url, headers=headers)
            data = response.json()
            return data.get("heart_rate", {}).get("zones")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not fetch Strava HR zones; HR zone metrics will be skipped: {e}")
            return None

    def get_activity_hr_stream(self, activity_id: int) -> Optional[Dict[str, List[int]]]:
        """Fetch heart rate + time + velocity streams for an activity."""
        url = f"{self.base_url}/activities/{activity_id}/streams"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        # We request heartrate, time, and velocity_smooth (m/s) in a single call.
        params = {"keys": "heartrate,time,velocity_smooth", "key_by_type": "true"}
        try:
            response = http_request_with_retries("GET", url, headers=headers, params=params)
            data = response.json()
            hr_stream = data.get("heartrate", {}).get("data")
            time_stream = data.get("time", {}).get("data")
            vel_stream = data.get("velocity_smooth", {}).get("data")

            if not hr_stream or not time_stream:
                return None

            # Velocity is only required for drift; zones can still work without it.
            result: Dict[str, List[int]] = {"hr": hr_stream, "time": time_stream}
            if vel_stream:
                result["vel"] = vel_stream
            return result
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not fetch HR stream for activity {activity_id}: {e}")
            return None

    def get_activity_primary_photo_url(self, activity_id: int) -> Optional[str]:
        """
        Fetch the primary photo URL for an activity, if available.

        Note: Photos may only be available if your Strava privacy settings allow
        API access to images. This method returns a single representative URL.
        """
        url = f"{self.base_url}/activities/{activity_id}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        params = {
            "include_all_efforts": "false",
            "photo_sources": "true",
        }
        try:
            response = http_request_with_retries("GET", url, headers=headers, params=params)
            data = response.json()
            photos = data.get("photos") or {}
            primary = photos.get("primary") or {}
            urls = primary.get("urls") or {}
            # Prefer a higher-res key if present, otherwise any URL
            for key in ("1200", "600", "300"):
                if key in urls:
                    return urls[key]
            # Fallback: first URL in the dict, if any
            if urls:
                return next(iter(urls.values()))
            return None
        except requests.exceptions.RequestException as e:
            logger.debug("Could not fetch primary photo for activity %s: %s", activity_id, e)
            return None

    @staticmethod
    def compute_hr_zone_minutes(
        hr_stream: Dict[str, List[int]], zones: List[Dict]
    ) -> Optional[Dict[int, float]]:
        """
        Compute minutes per HR zone given HR + time streams and zone definitions.

        Uses time deltas between samples instead of assuming 1Hz sampling.
        """
        if not hr_stream or not zones:
            return None
        hr_values = hr_stream.get("hr") or []
        t_values = hr_stream.get("time") or []
        if not hr_values or not t_values:
            return None

        # Ensure equal length
        n = min(len(hr_values), len(t_values))
        if n < 2:
            return None
        hr_values = hr_values[:n]
        t_values = t_values[:n]

        zone_counts = {idx + 1: 0 for idx in range(len(zones))}
        # Accumulate seconds per zone
        for i in range(n - 1):
            hr = hr_values[i]
            dt = max(0, t_values[i + 1] - t_values[i])
            for idx, zone in enumerate(zones):
                min_hr = zone.get("min", 0)
                max_hr = zone.get("max")  # may be None for last zone
                if hr >= min_hr and (max_hr is None or hr < max_hr):
                    zone_counts[idx + 1] += dt
                    break

        # Convert seconds to minutes, rounded to 2 decimals
        return {zone: round(seconds / SECONDS_PER_MINUTE, 2) for zone, seconds in zone_counts.items()}

    @staticmethod
    def compute_hr_drift(
        hr_stream: Dict[str, List[int]],
        moving_time_s: int,
        distance_m: float,
    ) -> Optional[Dict[str, float]]:
        """
        Compute aerobic decoupling / HR drift metrics using HR + velocity streams.

        Uses time-weighted averages for first vs second half of the activity.
        Returns dict with:
          - drift_pct
          - avg_hr_1, avg_hr_2
          - avg_vel_1_mps, avg_vel_2_mps
        or None if metrics cannot be computed safely.
        """
        hr_values = hr_stream.get("hr") or []
        t_values = hr_stream.get("time") or []
        vel_values = hr_stream.get("vel") or hr_stream.get("velocity") or []

        n = min(len(hr_values), len(t_values), len(vel_values))
        if n < 2:
            return None

        hr_values = hr_values[:n]
        t_values = t_values[:n]
        vel_values = vel_values[:n]

        total_duration = t_values[-1] - t_values[0]
        if total_duration <= 0:
            return None

        midpoint = t_values[0] + total_duration / 2.0

        hr_sum_1 = hr_sum_2 = 0.0
        vel_sum_1 = vel_sum_2 = 0.0
        dt_1 = dt_2 = 0.0

        for i in range(n - 1):
            t0 = t_values[i]
            t1 = t_values[i + 1]
            hr = hr_values[i]
            vel = vel_values[i]

            if t1 <= t0:
                continue

            # Segment duration
            dt = t1 - t0

            # Entirely in first half
            if t1 <= midpoint:
                hr_sum_1 += hr * dt
                vel_sum_1 += vel * dt
                dt_1 += dt
            # Entirely in second half
            elif t0 >= midpoint:
                hr_sum_2 += hr * dt
                vel_sum_2 += vel * dt
                dt_2 += dt
            else:
                # Crosses midpoint: split segment
                dt_first = max(0.0, midpoint - t0)
                dt_second = max(0.0, t1 - midpoint)
                if dt_first > 0:
                    hr_sum_1 += hr * dt_first
                    vel_sum_1 += vel * dt_first
                    dt_1 += dt_first
                if dt_second > 0:
                    hr_sum_2 += hr * dt_second
                    vel_sum_2 += vel * dt_second
                    dt_2 += dt_second

        if dt_1 <= 0 or dt_2 <= 0:
            return None

        avg_hr_1 = hr_sum_1 / dt_1
        avg_hr_2 = hr_sum_2 / dt_2
        avg_vel_1 = vel_sum_1 / dt_1
        avg_vel_2 = vel_sum_2 / dt_2

        # Guard against unrealistic or zero velocities
        if avg_vel_1 <= DRIFT_MIN_VELOCITY_THRESHOLD_MPS or avg_vel_2 <= DRIFT_MIN_VELOCITY_THRESHOLD_MPS:
            return None

        eff_1 = avg_hr_1 / avg_vel_1
        eff_2 = avg_hr_2 / avg_vel_2
        if eff_1 <= 0:
            return None

        drift_pct = ((eff_2 - eff_1) / eff_1) * 100.0

        return {
            "drift_pct": drift_pct,
            "avg_hr_1": avg_hr_1,
            "avg_hr_2": avg_hr_2,
            "avg_vel_1_mps": avg_vel_1,
            "avg_vel_2_mps": avg_vel_2,
        }


def get_activity_local_date(activity: Dict) -> str:
    """
    Extract local date from activity as YYYY-MM-DD string.
    
    Uses start_date_local if available, otherwise falls back to start_date (UTC).
    
    Args:
        activity: Strava activity dict
        
    Returns:
        Date string in YYYY-MM-DD format
    """
    # Prefer local date if available
    start_date_local = activity.get("start_date_local")
    if start_date_local:
        # Parse ISO format datetime and extract date
        dt = datetime.fromisoformat(start_date_local.replace("Z", "+00:00"))
        return dt.date().isoformat()
    
    # Fallback to UTC start_date
    start_date = activity.get("start_date", "")
    if start_date:
        dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        return dt.date().isoformat()
    
    # Should not happen, but return today as fallback
    return datetime.now(timezone.utc).date().isoformat()


def compute_zone_weighted_load_points(zone_minutes: Dict[int, float]) -> Optional[float]:
    """
    Compute zone-weighted load points from HR zone minutes.
    
    Formula: Load = Z1*1 + Z2*2 + Z3*3 + Z4*4 + Z5*5
    
    Args:
        zone_minutes: Dict mapping zone number (1-5) to minutes spent in that zone
        
    Returns:
        Load points as float, or None if zone_minutes is empty/invalid
    """
    if not zone_minutes:
        return None
    
    total_load = 0.0
    for zone_num in range(1, 6):
        minutes = zone_minutes.get(zone_num, 0.0)
        total_load += minutes * zone_num
    
    return round(total_load, 2) if total_load > 0 else None


def aggregate_daily_summaries(
    activities: List[Dict], days: int
) -> Dict[str, Dict[str, Any]]:
    """
    Aggregate activities by local date into daily summaries.
    
    Args:
        activities: List of activity dicts (with computed _load_pts and _hr_data_quality)
        days: Number of days in the sync window (for context)
        
    Returns:
        Dict mapping date_iso (YYYY-MM-DD) to summary dict with:
        - total_duration_min
        - total_moving_time_min
        - total_distance_mi
        - total_elevation_ft
        - session_count
        - total_load_pts
        - eligible_cardio_count (count of cardio workouts on this day)
        - load_workouts_count (count of cardio workouts with load computed)
    """
    daily: Dict[str, Dict[str, Any]] = {}
    
    for activity in activities:
        date_iso = get_activity_local_date(activity)
        sport_type = activity.get("type", "")
        
        if date_iso not in daily:
            daily[date_iso] = {
                "total_duration_min": 0.0,
                "total_moving_time_min": 0.0,
                "total_distance_mi": 0.0,
                "total_elevation_ft": 0.0,
                "session_count": 0,
                "total_load_pts": 0.0,
                "eligible_cardio_count": 0,
                "load_workouts_count": 0,
            }
        
        day_summary = daily[date_iso]
        day_summary["session_count"] += 1
        
        # Duration (in minutes)
        elapsed_time_s = activity.get("elapsed_time", 0)
        if elapsed_time_s:
            day_summary["total_duration_min"] += elapsed_time_s / SECONDS_PER_MINUTE
        
        # Moving time (in minutes)
        moving_time_s = activity.get("moving_time", 0)
        if moving_time_s:
            day_summary["total_moving_time_min"] += moving_time_s / SECONDS_PER_MINUTE
        
        # Distance (in miles)
        distance_m = activity.get("distance", 0)
        if distance_m:
            day_summary["total_distance_mi"] += distance_m * METERS_TO_MILES
        
        # Elevation (in feet)
        elevation_m = activity.get("total_elevation_gain", 0)
        if elevation_m:
            day_summary["total_elevation_ft"] += elevation_m * METERS_TO_FEET
        
        # Track eligible cardio workouts and load
        if sport_type in CARDIO_SPORTS:
            day_summary["eligible_cardio_count"] += 1
            # Load points only count if HR Data Quality is "Good" and load_pts exists
            hr_data_quality = activity.get("_hr_data_quality", "None")
            load_pts = activity.get("_load_pts")
            if hr_data_quality == "Good" and load_pts is not None and load_pts > 0:
                day_summary["total_load_pts"] += load_pts
                day_summary["load_workouts_count"] += 1
    
    # Round aggregated values
    for date_iso, summary in daily.items():
        summary["total_duration_min"] = round(summary["total_duration_min"], 2)
        summary["total_moving_time_min"] = round(summary["total_moving_time_min"], 2)
        summary["total_distance_mi"] = round(summary["total_distance_mi"], 2)
        summary["total_elevation_ft"] = round(summary["total_elevation_ft"], 1)
        summary["total_load_pts"] = round(summary["total_load_pts"], 2)
    
    return daily


def compute_rolling_loads(
    daily_summaries: Dict[str, Dict[str, Any]], today: datetime
) -> Dict[str, Optional[float]]:
    """
    Compute 7-day and 28-day rolling load totals from daily summaries.
    
    Args:
        daily_summaries: Dict mapping date_iso (YYYY-MM-DD) to daily summary (with total_load_pts)
        today: Current datetime (used to get today's date for rolling window)
        
    Returns:
        Dict with "load_7d" and "load_28d" keys (always returns numbers, never None for simplicity)
        Note: Empty daily_summaries will return 0.0 for both
    """
    today_date = today.date()
    
    load_7d = 0.0
    load_28d = 0.0
    
    for date_iso, summary in daily_summaries.items():
        try:
            date_obj = datetime.fromisoformat(date_iso).date()
            days_ago = (today_date - date_obj).days
            
            load_pts = summary.get("total_load_pts", 0.0) or 0.0  # Treat None/empty as 0
            
            # Rolling windows: [today-6, today] for 7d (7 days total), [today-27, today] for 28d (28 days total)
            if 0 <= days_ago <= 6:  # today-6 through today (inclusive)
                load_7d += load_pts
            if 0 <= days_ago <= 27:  # today-27 through today (inclusive)
                load_28d += load_pts
        except (ValueError, TypeError):
            continue
    
    result: Dict[str, float] = {
        "load_7d": round(load_7d, 2),
        "load_28d": round(load_28d, 2),
    }
    
    return result


class WeatherClient:
    """
    Client for fetching historical weather data using WeatherAPI.com.
    
    WeatherAPI.com provides weather data with minimal delay (~15 minutes) and includes
    historical data. Requires a free API key from https://www.weatherapi.com/
    
    Falls back to Open-Meteo archive API if WeatherAPI key is not provided.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.weatherapi_base = "https://api.weatherapi.com/v1/history.json"
        self.openmeteo_base = "https://archive-api.open-meteo.com/v1/archive"
        self.use_weatherapi = api_key is not None
    
    def get_weather_for_activity(
        self, latitude: float, longitude: float, start_time: datetime
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch weather data for a specific location and time.
        
        Args:
            latitude: Activity start latitude
            longitude: Activity start longitude
            start_time: Activity start datetime (timezone-aware)
        
        Returns:
            Dict with temp_f, conditions, wind_mph, humidity, or None if unavailable
        """
        if self.use_weatherapi:
            return self._get_weather_weatherapi(latitude, longitude, start_time)
        else:
            return self._get_weather_openmeteo(latitude, longitude, start_time)
    
    def _get_weather_weatherapi(
        self, latitude: float, longitude: float, start_time: datetime
    ) -> Optional[Dict[str, Any]]:
        """Fetch weather using WeatherAPI.com (minimal delay, ~15 minutes)."""
        try:
            date_str = start_time.strftime("%Y-%m-%d")
            hour = start_time.hour
            
            params = {
                "key": self.api_key,
                "q": f"{latitude},{longitude}",
                "dt": date_str,
            }
            
            # Log params without API key for security
            safe_params = {k: v if k != "key" else "***" for k, v in params.items()}
            logger.debug(f"Making WeatherAPI.com request to {self.weatherapi_base} with params: {safe_params}")
            response = http_request_with_retries("GET", self.weatherapi_base, params=params)
            logger.debug(f"WeatherAPI.com response status: {response.status_code}")
            data = response.json()
            logger.debug(f"STEP: WeatherAPI.com response keys: {list(data.keys())}")
            
            # Check for API errors
            if "error" in data:
                error_msg = data.get("error", {}).get("message", "Unknown error")
                logger.warning(f"STEP: WeatherAPI.com API error: {error_msg}")
                return None
            
            # Get forecastday (should be one day)
            forecastday = data.get("forecast", {}).get("forecastday", [])
            if not forecastday:
                logger.warning(f"STEP: No forecastday data in WeatherAPI.com response")
                return None
            
            day_data = forecastday[0]
            hours = day_data.get("hour", [])
            
            if not hours:
                logger.warning(f"STEP: No hourly data in WeatherAPI.com response")
                return None
            
            # Find the hour that matches the activity start time
            # WeatherAPI.com returns hours as list, each with "time" field like "2024-01-01 14:00"
            matching_hour = None
            for h in hours:
                hour_time_str = h.get("time", "")
                # Parse hour from time string (format: "2024-01-01 14:00")
                try:
                    hour_time = datetime.fromisoformat(hour_time_str.replace(" ", "T"))
                    if hour_time.hour == hour:
                        matching_hour = h
                        break
                except (ValueError, AttributeError):
                    continue
            
            # If exact hour not found, use closest hour
            if not matching_hour and hours:
                # Find closest hour
                min_diff = float('inf')
                for h in hours:
                    hour_time_str = h.get("time", "")
                    try:
                        hour_time = datetime.fromisoformat(hour_time_str.replace(" ", "T"))
                        diff = abs((hour_time.hour - hour) % 24)
                        if diff < min_diff:
                            min_diff = diff
                            matching_hour = h
                    except (ValueError, AttributeError):
                        continue
            
            if not matching_hour:
                logger.warning(f"STEP: Could not find matching hour {hour} in WeatherAPI.com data")
                return None
            
            temp_f = matching_hour.get("temp_f")
            condition = matching_hour.get("condition", {}).get("text", "Unknown")
            wind_mph = matching_hour.get("wind_mph", 0.0)
            humidity = matching_hour.get("humidity", 0.0)
            
            if temp_f is None:
                logger.warning(f"STEP: Missing temperature in WeatherAPI.com response")
                return None
            
            result = {
                "temp_f": temp_f,
                "conditions": condition,
                "wind_mph": wind_mph or 0.0,
                "humidity": humidity or 0.0,
            }
            logger.debug(f"Weather data successfully processed from WeatherAPI.com: {result}")
            return result
            
        except Exception as e:
            logger.warning(f"STEP: Exception in WeatherAPI.com fetch for ({latitude}, {longitude}) at {start_time}: {e}")
            import traceback
            logger.debug(f"STEP: Full traceback: {traceback.format_exc()}")
            return None
    
    def _get_weather_openmeteo(
        self, latitude: float, longitude: float, start_time: datetime
    ) -> Optional[Dict[str, Any]]:
        """Fetch weather using Open-Meteo archive API (fallback, has 2-day delay)."""
        try:
            date_str = start_time.strftime("%Y-%m-%d")
            
            params = {
                "latitude": latitude,
                "longitude": longitude,
                "start_date": date_str,
                "end_date": date_str,
                "hourly": "temperature_2m,weathercode,windspeed_10m,relativehumidity_2m",
                "temperature_unit": "fahrenheit",
                "windspeed_unit": "mph",
            }
            
            logger.debug(f"Making Open-Meteo API request to {self.openmeteo_base} with params: {params}")
            response = http_request_with_retries("GET", self.openmeteo_base, params=params)
            logger.debug(f"Open-Meteo API response status: {response.status_code}")
            data = response.json()
            logger.debug(f"STEP: Open-Meteo API response keys: {list(data.keys())}")
            
            # Check for API errors
            if "error" in data or "reason" in data:
                error_msg = data.get('reason', data.get('error', 'Unknown error'))
                logger.warning(f"STEP: Open-Meteo API error: {error_msg}")
                logger.warning(f"STEP: Full error response: {data}")
                return None
            
            hourly = data.get("hourly", {})
            logger.debug(f"STEP: Hourly data keys: {list(hourly.keys()) if hourly else 'None'}")
            temps = hourly.get("temperature_2m", [])
            weathercodes = hourly.get("weathercode", [])
            windspeeds = hourly.get("windspeed_10m", [])
            humidities = hourly.get("relativehumidity_2m", [])
            
            logger.debug(f"STEP: Extracted arrays - temps: {len(temps) if temps else 0}, codes: {len(weathercodes) if weathercodes else 0}, winds: {len(windspeeds) if windspeeds else 0}, humidity: {len(humidities) if humidities else 0}")
            
            if not temps or not weathercodes:
                logger.warning(f"STEP: No weather data available for {date_str} at ({latitude}, {longitude}) - temps: {len(temps) if temps else 0}, codes: {len(weathercodes) if weathercodes else 0}")
                return None
            
            # Find the hour that matches the activity start time
            activity_hour = start_time.hour
            logger.debug(f"STEP: Activity hour: {activity_hour}, available hours: {len(temps)}")
            if activity_hour >= len(temps):
                activity_hour = len(temps) - 1
                logger.debug(f"STEP: Adjusted activity hour to last available: {activity_hour}")
            
            temp_f = temps[activity_hour] if activity_hour < len(temps) else None
            weathercode = weathercodes[activity_hour] if activity_hour < len(weathercodes) else None
            wind_mph = windspeeds[activity_hour] if activity_hour < len(windspeeds) else None
            humidity = humidities[activity_hour] if activity_hour < len(humidities) else None
            
            logger.debug(f"STEP: Extracted values for hour {activity_hour} - temp_f: {temp_f}, weathercode: {weathercode}, wind_mph: {wind_mph}, humidity: {humidity}")
            
            if temp_f is None or weathercode is None:
                logger.warning(f"STEP: Missing required weather data - temp_f: {temp_f}, weathercode: {weathercode}")
                return None
            
            # Convert WMO weather code to human-readable conditions
            conditions = self._weathercode_to_text(weathercode)
            logger.debug(f"STEP: Converted weathercode {weathercode} to conditions: {conditions}")
            
            result = {
                "temp_f": temp_f,
                "conditions": conditions,
                "wind_mph": wind_mph or 0.0,
                "humidity": humidity or 0.0,
            }
            logger.debug(f"Weather data successfully processed from Open-Meteo: {result}")
            return result
            
        except Exception as e:
            logger.warning(f"STEP: Exception in Open-Meteo fetch for ({latitude}, {longitude}) at {start_time}: {e}")
            import traceback
            logger.debug(f"STEP: Full traceback: {traceback.format_exc()}")
            return None
    
    @staticmethod
    def _weathercode_to_text(code: int) -> str:
        """
        Convert WMO weather code to human-readable description.
        Based on WMO Weather interpretation codes (WW).
        """
        # Main condition categories
        if code == 0:
            return "Clear"
        elif code in (1, 2, 3):
            return "Partly cloudy" if code == 1 else "Cloudy"
        elif code in (45, 48):
            return "Fog"
        elif code in (51, 53, 55):
            return "Drizzle"
        elif code in (61, 63, 65):
            return "Rain"
        elif code in (71, 73, 75):
            return "Snow"
        elif code in (80, 81, 82):
            return "Rain showers"
        elif code in (85, 86):
            return "Snow showers"
        elif code in (95, 96, 99):
            return "Thunderstorm"
        else:
            return "Unknown"
    
    @staticmethod
    def make_weather_summary(weather_data: Dict[str, Any]) -> str:
        """
        Format weather data into a concise summary string for outdoor activities.
        
        Example: "72°F, Clear, 5 mph wind, 65% humidity"
        """
        temp_f = weather_data.get("temp_f", 0)
        conditions = weather_data.get("conditions", "Unknown").lower()
        wind_mph = weather_data.get("wind_mph", 0)
        humidity = weather_data.get("humidity", 0)
        
        return f"{temp_f:.0f}°F, {conditions}, {wind_mph:.0f} mph wind, {humidity:.0f}% humidity"


class NotionSchemaCache:
    """Shared schema cache manager for multiple Notion databases."""
    
    _cache: Dict[str, Optional[set[str]]] = {}
    _api_key: Optional[str] = None
    _client: Optional[Client] = None
    
    @classmethod
    def initialize(cls, api_key: str) -> None:
        """Initialize the cache with an API key and client."""
        cls._api_key = api_key
        cls._client = Client(auth=api_key)
    
    @classmethod
    def get_schema(cls, api_key: str, database_id: str) -> Optional[set[str]]:
        """
        Get schema for a database, loading and caching if needed.
        
        Returns:
            Set of property names, or None if schema loading failed
        """
        # Initialize if needed
        if cls._api_key != api_key or cls._client is None:
            cls.initialize(api_key)
        
        # Return cached schema if available
        if database_id in cls._cache:
            return cls._cache[database_id]
        
        # Load schema
        try:
            db = cls._notion_call_with_retries(
                cls._client.databases.retrieve,
                database_id=database_id,
            )
            props = db.get("properties", {}) if isinstance(db, dict) else {}
            keys = set(props.keys())
            # If we somehow see zero properties, treat this as a soft failure so we
            # don't silently drop all writes. Better to let Notion validate.
            if not keys:
                logger.warning(
                    "Loaded Notion database schema but found 0 properties for %s. "
                    "Schema-based filtering will be disabled; writes will include "
                    "all generated properties and rely on Notion for validation.",
                    database_id[:8],
                )
                cls._cache[database_id] = None
            else:
                cls._cache[database_id] = keys
                logger.info(
                    "Loaded Notion database schema for %s; %d properties available",
                    database_id[:8],
                    len(keys),
                )
        except Exception as e:
            logger.warning(
                "Could not load Notion database schema for %s; will attempt writes without "
                "schema filtering. Errors may occur for unknown properties: %s",
                database_id[:8],
                e,
            )
            cls._cache[database_id] = None
        
        return cls._cache[database_id]
    
    @classmethod
    def _notion_call_with_retries(
        cls,
        func: Callable[..., Any],
        *args: Any,
        max_retries: int = HTTP_MAX_RETRIES,
        backoff_factor: float = HTTP_BACKOFF_FACTOR,
        **kwargs: Any,
    ) -> Any:
        """Call a Notion SDK function with basic retry/backoff on rate limits and 5xx."""
        attempts = 0
        while attempts <= max_retries:
            try:
                return func(*args, **kwargs)
            except APIResponseError as e:
                status = getattr(e, "status", None)
                if status in (429, 500, 502, 503, 504) and attempts < max_retries:
                    attempts += 1
                    sleep_seconds = backoff_factor * (2 ** (attempts - 1))
                    sleep_seconds += random.uniform(0, HTTP_BACKOFF_JITTER_MAX)
                    logger.warning(
                        "Notion API error (status=%s); retrying attempt %d/%d after %.2fs",
                        status,
                        attempts,
                        max_retries,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                    continue
                raise
            except Exception:
                # For non-API errors, don't blindly retry
                raise


class NotionClient:
    """Client for interacting with Notion API with upsert support."""
    
    def __init__(self, api_key: str, database_id: str):
        self.client = Client(auth=api_key)
        # Keep a copy of the raw API key for low-level HTTP fallbacks
        self.api_key = api_key
        self.database_id = database_id

    def _ensure_schema_loaded(self) -> Optional[set[str]]:
        """
        Retrieve and cache the Notion database schema.
        
        Returns:
            Set of property names, or None if schema loading failed
        """
        return NotionSchemaCache.get_schema(self.api_key, self.database_id)

    @staticmethod
    def _notion_call_with_retries(
        func: Callable[..., Any],
        *args: Any,
        max_retries: int = HTTP_MAX_RETRIES,
        backoff_factor: float = HTTP_BACKOFF_FACTOR,
        **kwargs: Any,
    ) -> Any:
        """Call a Notion SDK function with basic retry/backoff on rate limits and 5xx."""
        attempts = 0
        while attempts <= max_retries:
            try:
                return func(*args, **kwargs)
            except APIResponseError as e:
                status = getattr(e, "status", None)
                if status in (429, 500, 502, 503, 504) and attempts < max_retries:
                    attempts += 1
                    sleep_seconds = backoff_factor * (2 ** (attempts - 1))
                    sleep_seconds += random.uniform(0, HTTP_BACKOFF_JITTER_MAX)
                    logger.warning(
                        "Notion API error (status=%s); retrying attempt %d/%d after %.2fs",
                        status,
                        attempts,
                        max_retries,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                    continue
                raise
            except Exception:
                # For non-API errors, don't blindly retry
                raise
    
    def _database_query(self, **query_params: Any) -> Dict:
        """
        Wrapper around Notion database query that works across SDK versions.

        Prefers the official databases.query method; if unavailable, falls back
        to a raw HTTP request to /databases/{id}/query.
        """
        # Preferred path: databases.query exists on the SDK endpoint
        if hasattr(self.client.databases, "query"):
            return self._notion_call_with_retries(
                getattr(self.client.databases, "query"),
                **query_params,
            )

        # Fallback path for older SDKs: call REST API directly
        database_id = query_params.pop("database_id")
        url = f"https://api.notion.com/v1/databases/{database_id}/query"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }
        response = http_request_with_retries(
            "POST",
            url,
            headers=headers,
            json=query_params,
        )
        return response.json()
    
    def get_existing_activity_pages(self, days: int = DEFAULT_SYNC_DAYS) -> Dict[str, str]:
        """
        Get existing activity pages from Notion within date range.
        Returns dict mapping activity_id (str) to page_id (str).
        """
        existing_map = {}
        start_cursor = None
        
        # Calculate date filter (UTC, date-only for stability)
        after_date = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        
        while True:
            query_params = {
                "database_id": self.database_id,
                "filter": {
                    "property": NOTION_SCHEMA["date"],
                    "date": {
                        "on_or_after": after_date
                    }
                }
            }
            
            if start_cursor:
                query_params["start_cursor"] = start_cursor
            
            try:
                response = self._database_query(**query_params)
                
                for page in response.get("results", []):
                    props = page.get("properties", {})
                    activity_id_prop = props.get(NOTION_SCHEMA["activity_id"])
                    
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
            response = self._database_query(
                database_id=self.database_id,
                filter={
                    "property": NOTION_SCHEMA["activity_id"],
                    "rich_text": {"equals": activity_id},
                },
            )
            
            if response.get("results"):
                return response["results"][0]["id"]
            return None
        except Exception as e:
            logger.warning(f"Error searching for activity {activity_id}: {e}")
            return None
    
    def upsert_activity(self, activity: Dict, existing_page_id: Optional[str] = None) -> bool:
        """
        Upsert an activity into Notion.
        If existing_page_id is provided, updates that page; otherwise creates new.
        Returns True if successful, False otherwise.
        """
        properties = self._convert_activity_to_properties(activity)

        # Ensure schema is loaded so we only write properties that exist
        allowed_properties = self._ensure_schema_loaded()

        # Optionally set Sync Status if DB supports it
        if allowed_properties and "Sync Status" in allowed_properties:
            sync_status_value = "updated" if existing_page_id else "created"
            properties["Sync Status"] = {
                "select": {"name": sync_status_value}
            }

        # Remove None values and properties that don't exist in this database
        # Filter based on schema to avoid writing to non-existent properties
        if allowed_properties is not None:
            properties_before_filter = set(properties.keys())
            properties = {
                k: v
                for k, v in properties.items()
                if v is not None and k in allowed_properties
            }
            filtered_out = properties_before_filter - set(properties.keys())
            if filtered_out:
                logger.debug(f"Properties filtered out (not in schema): {filtered_out}")
        else:
            # Schema filtering disabled - be conservative and skip optional properties
            # that are likely to cause errors if they don't exist
            properties = {k: v for k, v in properties.items() if v is not None}
            # Remove Load (pts) if schema is unknown (it's optional and often missing)
            if NOTION_SCHEMA["load_pts"] in properties:
                logger.debug(
                    f"Schema unknown - skipping optional property '{NOTION_SCHEMA['load_pts']}' "
                    "to avoid errors. Add this property to your Notion database if you want load points."
                )
                properties.pop(NOTION_SCHEMA["load_pts"], None)
        
        try:
            if existing_page_id:
                # Update existing page
                self._notion_call_with_retries(
                    self.client.pages.update,
                    page_id=existing_page_id,
                    properties=properties,
                )
                return True
            else:
                # Create new page
                self._notion_call_with_retries(
                    self.client.pages.create,
                    parent={"database_id": self.database_id},
                    properties=properties,
                )
                return True
        except APIResponseError as e:
            error_msg = str(e)
            status = getattr(e, "status", None)
            
            # Handle 400 errors that indicate a missing property
            if status == 400:
                # Check if it's a property that doesn't exist
                if "property" in error_msg.lower() and ("doesn't exist" in error_msg.lower() or "is not a property" in error_msg.lower()):
                    # Extract property name from error if possible
                    logger.warning(
                        f"Property error for activity {activity.get('id')}: {error_msg}. "
                        "This property will be skipped in future runs once schema is properly loaded."
                    )
                    # Return False so it's counted as failed, but don't abort the whole sync
                    return False
            
            # Re-raise other APIResponseErrors (they'll be handled by retry logic if retryable)
            logger.error(f"Notion API error upserting activity {activity.get('id')}: {error_msg}")
            return False
        except Exception as e:
            error_msg = str(e)
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
        distance_mi = distance_m * METERS_TO_MILES
        
        elevation_m = activity.get("total_elevation_gain", 0)
        elevation_ft = elevation_m * METERS_TO_FEET
        
        elapsed_time_s = activity.get("elapsed_time", 0)
        moving_time_s = activity.get("moving_time", 0)
        duration_min = elapsed_time_s / SECONDS_PER_MINUTE
        moving_time_min = moving_time_s / SECONDS_PER_MINUTE if moving_time_s else None
        
        # Heart rate
        avg_hr = activity.get("average_heartrate")
        max_hr = activity.get("max_heartrate")
        
        # Pace calculation (for running-like sports)
        pace_min_per_mi = None
        running_sports = {"Run", "TrailRun", "Walk", "Hike"}
        if sport_type in running_sports and distance_mi > 0 and moving_time_s > 0:
            seconds_per_mile = moving_time_s / distance_mi
            pace_min_per_mi = seconds_per_mile / SECONDS_PER_MINUTE
        
        # Build properties dict using schema constants
        properties = {
            NOTION_SCHEMA["name"]: {
                "title": [{"text": {"content": activity_name}}]
            },
            NOTION_SCHEMA["activity_id"]: {
                "rich_text": [{"text": {"content": activity_id}}]
            },
            NOTION_SCHEMA["date"]: {
                "date": {"start": start_date.isoformat()}
            },
            NOTION_SCHEMA["sport"]: {
                "select": {"name": sport_type}
            },
            NOTION_SCHEMA["duration_min"]: {
                "number": round(duration_min, 2)
            },
            NOTION_SCHEMA["distance_mi"]: {
                "number": round(distance_mi, 2)
            },
            NOTION_SCHEMA["elevation_ft"]: {
                "number": round(elevation_ft, 1)
            }
        }
        
        # Optional properties (only add if value exists)
        if avg_hr:
            properties[NOTION_SCHEMA["avg_hr"]] = {"number": avg_hr}
        
        if max_hr:
            properties[NOTION_SCHEMA["max_hr"]] = {"number": max_hr}
        
        if pace_min_per_mi:
            properties[NOTION_SCHEMA["avg_pace_min_per_mi"]] = {"number": round(pace_min_per_mi, 2)}
        
        if moving_time_min:
            properties[NOTION_SCHEMA["moving_time_min"]] = {"number": round(moving_time_min, 2)}
        
        properties[NOTION_SCHEMA["strava_url"]] = {
            "url": f"https://www.strava.com/activities/{activity_id}"
        }
        
        properties[NOTION_SCHEMA["last_synced"]] = {
            "date": {"start": now.isoformat()}
        }
        
        # Heart rate zone summaries (if already computed and attached)
        hr_zones = activity.get("_hr_zone_minutes")
        if hr_zones:
            for zone_num, minutes in hr_zones.items():
                zone_prop_name = NOTION_SCHEMA["hr_zone_min"].format(zone=zone_num)
                properties[zone_prop_name] = {"number": minutes}

        # HR drift / decoupling metrics (if computed)
        drift = activity.get("_drift_metrics")
        if drift is not None:
            drift_pct = drift.get("drift_pct")
            if drift_pct is not None:
                properties[NOTION_SCHEMA["hr_drift_pct"]] = {"number": round(drift_pct, 2)}

            avg_hr_1 = drift.get("avg_hr_1")
            avg_hr_2 = drift.get("avg_hr_2")
            if avg_hr_1 is not None:
                properties[NOTION_SCHEMA["hr_1st_half_bpm"]] = {"number": round(avg_hr_1, 1)}
            if avg_hr_2 is not None:
                properties[NOTION_SCHEMA["hr_2nd_half_bpm"]] = {"number": round(avg_hr_2, 1)}

            # Convert m/s to mph for storage
            vel1 = drift.get("avg_vel_1_mps")
            vel2 = drift.get("avg_vel_2_mps")
            if vel1 is not None:
                properties[NOTION_SCHEMA["speed_1st_half_mph"]] = {
                    "number": round(vel1 * METERS_PER_SECOND_TO_MPH, 2)
                }
            if vel2 is not None:
                properties[NOTION_SCHEMA["speed_2nd_half_mph"]] = {
                    "number": round(vel2 * METERS_PER_SECOND_TO_MPH, 2)
                }

        # Drift eligibility & HR data quality indicators
        drift_eligible = activity.get("_drift_eligible")
        if drift_eligible is not None:
            properties[NOTION_SCHEMA["drift_eligible"]] = {"checkbox": bool(drift_eligible)}

        hr_data_quality = activity.get("_hr_data_quality")
        if hr_data_quality:
            properties[NOTION_SCHEMA["hr_data_quality"]] = {"select": {"name": hr_data_quality}}

        # Load (pts) - per-activity load if computed and property exists
        load_pts = activity.get("_load_pts")
        if load_pts is not None and load_pts > 0:
            properties[NOTION_SCHEMA["load_pts"]] = {"number": round(load_pts, 2)}

        # Primary photo URL (optional)
        photo_url = activity.get("_photo_url")
        if photo_url:
            properties[NOTION_SCHEMA["photo_url"]] = {"url": photo_url}

        # Weather data (optional, only for outdoor activities)
        weather = activity.get("_weather")
        if weather:
            temp_f = weather.get("temp_f")
            if temp_f is not None:
                properties[NOTION_SCHEMA["temperature_f"]] = {"number": round(temp_f, 1)}
            
            weather_summary = WeatherClient.make_weather_summary(weather)
            if weather_summary:
                properties[NOTION_SCHEMA["weather_conditions"]] = {
                    "rich_text": [{"text": {"content": weather_summary}}]
                }
        # Note: We don't log when weather is missing - it's optional and expected for indoor activities

        return properties
    
    def upsert_daily_summary(
        self, date_iso: str, summary: Dict[str, Any]
    ) -> bool:
        """
        Upsert a daily summary row in Notion.
        
        Args:
            date_iso: Date string in YYYY-MM-DD format
            summary: Daily summary dict with aggregated metrics
            
        Returns:
            True if successful, False otherwise
        """
        allowed_properties = self._ensure_schema_loaded()
        
        # Build properties dict
        properties: Dict[str, Any] = {
            DAILY_SUMMARY_SCHEMA["date"]: {
                "date": {"start": date_iso}
            },
            DAILY_SUMMARY_SCHEMA["session_count"]: {
                "number": summary["session_count"]
            },
        }
        
        # Add optional numeric fields
        if summary.get("total_duration_min") is not None:
            properties[DAILY_SUMMARY_SCHEMA["total_duration_min"]] = {
                "number": summary["total_duration_min"]
            }
        if summary.get("total_moving_time_min") is not None:
            properties[DAILY_SUMMARY_SCHEMA["total_moving_time_min"]] = {
                "number": summary["total_moving_time_min"]
            }
        if summary.get("total_distance_mi") is not None:
            properties[DAILY_SUMMARY_SCHEMA["total_distance_mi"]] = {
                "number": summary["total_distance_mi"]
            }
        if summary.get("total_elevation_ft") is not None:
            properties[DAILY_SUMMARY_SCHEMA["total_elevation_ft"]] = {
                "number": summary["total_elevation_ft"]
            }
        if summary.get("total_load_pts") is not None:
            properties[DAILY_SUMMARY_SCHEMA["load_pts"]] = {
                "number": summary["total_load_pts"]
            }
        
        # Load confidence (per spec: based on eligible_workouts vs load_workouts)
        eligible_cardio_count = summary.get("eligible_cardio_count", 0)
        load_workouts_count = summary.get("load_workouts_count", 0)
        
        if eligible_cardio_count > 0:
            if load_workouts_count == eligible_cardio_count:
                # All eligible cardio workouts produced load
                confidence = "High"
            elif load_workouts_count > 0:
                # Some eligible workouts produced load, but not all
                confidence = "Medium"
            else:
                # No eligible workouts produced load
                confidence = "Low"
        else:
            # No eligible cardio workouts on this day
            confidence = "Low"
        
        properties[DAILY_SUMMARY_SCHEMA["load_confidence"]] = {
            "select": {"name": confidence}
        }
        
        # Filter properties based on schema
        if allowed_properties is not None:
            properties = {
                k: v for k, v in properties.items() if k in allowed_properties
            }
        
        # Find existing page by date
        try:
            response = self._database_query(
                database_id=self.database_id,
                filter={
                    "property": DAILY_SUMMARY_SCHEMA["date"],
                    "date": {"equals": date_iso},
                },
            )
            existing_page_id = None
            if response.get("results"):
                existing_page_id = response["results"][0]["id"]
            
            if existing_page_id:
                # Update existing page
                self._notion_call_with_retries(
                    self.client.pages.update,
                    page_id=existing_page_id,
                    properties=properties,
                )
                return True
            else:
                # Create new page
                self._notion_call_with_retries(
                    self.client.pages.create,
                    parent={"database_id": self.database_id},
                    properties=properties,
                )
                return True
        except Exception as e:
            logger.warning(f"Error upserting daily summary for {date_iso}: {e}")
            return False
    
    def upsert_athlete_metrics(
        self, athlete_name: str, metrics: Dict[str, Any]
    ) -> bool:
        """
        Upsert athlete metrics row in Notion.
        
        Args:
            athlete_name: Name of the athlete (used as unique key)
            metrics: Dict with load_7d, load_28d, load_balance, etc.
            
        Returns:
            True if successful, False otherwise
        """
        allowed_properties = self._ensure_schema_loaded()
        
        # Build properties dict
        properties: Dict[str, Any] = {
            ATHLETE_METRICS_SCHEMA["name"]: {
                "title": [{"text": {"content": athlete_name}}]
            },
            ATHLETE_METRICS_SCHEMA["updated_at"]: {
                "date": {"start": datetime.now(timezone.utc).isoformat()}
            },
        }
        
        # Add load metrics
        if metrics.get("load_7d") is not None:
            properties[ATHLETE_METRICS_SCHEMA["load_7d"]] = {
                "number": metrics["load_7d"]
            }
        if metrics.get("load_28d") is not None:
            properties[ATHLETE_METRICS_SCHEMA["load_28d"]] = {
                "number": metrics["load_28d"]
            }
        if metrics.get("load_balance") is not None:
            properties[ATHLETE_METRICS_SCHEMA["load_balance"]] = {
                "number": metrics["load_balance"]
            }
        
        # ETHR fields: leave blank with note that it's not implemented
        notes = "ETHR intentionally not implemented yet."
        properties[ATHLETE_METRICS_SCHEMA["notes"]] = {
            "rich_text": [{"text": {"content": notes}}]
        }
        
        # Filter properties based on schema
        if allowed_properties is not None:
            properties = {
                k: v for k, v in properties.items() if k in allowed_properties
            }
        
        # Find existing page by name
        try:
            response = self._database_query(
                database_id=self.database_id,
                filter={
                    "property": ATHLETE_METRICS_SCHEMA["name"],
                    "title": {"equals": athlete_name},
                },
            )
            existing_page_id = None
            if response.get("results"):
                existing_page_id = response["results"][0]["id"]
            
            if existing_page_id:
                # Update existing page
                self._notion_call_with_retries(
                    self.client.pages.update,
                    page_id=existing_page_id,
                    properties=properties,
                )
                return True
            else:
                # Create new page
                self._notion_call_with_retries(
                    self.client.pages.create,
                    parent={"database_id": self.database_id},
                    properties=properties,
                )
                return True
        except Exception as e:
            logger.warning(f"Error upserting athlete metrics for {athlete_name}: {e}")
            return False


def sync_strava_to_notion(days: int = DEFAULT_SYNC_DAYS, failure_threshold: float = DEFAULT_FAILURE_THRESHOLD):
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
    
    # Optional databases
    notion_daily_summary_db_id = os.getenv("NOTION_DAILY_SUMMARY_DATABASE_ID")
    notion_athlete_metrics_db_id = os.getenv("NOTION_ATHLETE_METRICS_DATABASE_ID")
    athlete_name = os.getenv("ATHLETE_NAME", "Athlete")
    
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
    
    # Initialize weather client (use WeatherAPI.com if key provided, otherwise Open-Meteo)
    weather_api_key = os.getenv("WEATHER_API_KEY")
    if weather_api_key:
        logger.info("Using WeatherAPI.com for weather data (minimal delay)")
        weather_client = WeatherClient(api_key=weather_api_key)
    else:
        logger.info("Using Open-Meteo archive API for weather data (2-day delay - consider adding WEATHER_API_KEY for minimal delay)")
        weather_client = WeatherClient()
    
    # Fetch activities from Strava
    try:
        activities = strava.get_recent_activities(days=days)
    except Exception as e:
        error_msg = str(e)
        # Check if it's a rate limit error
        if "429" in error_msg or "rate limit" in error_msg.lower():
            logger.error(
                "Strava API rate limit exceeded. The sync cannot continue until rate limits reset.\n"
                "This usually happens if:\n"
                "  1. Multiple sync runs happened too quickly\n"
                "  2. The update_weather script was run recently and consumed API quota\n"
                "  3. You've exceeded Strava's daily/hourly rate limits\n"
                "\n"
                "**What to do:**\n"
                "  - Wait 15-30 minutes before running the sync again\n"
                "  - Check if update_weather.py ran recently (it makes many API calls)\n"
                "  - Consider reducing sync frequency if this happens often\n"
                "\n"
                f"Error details: {error_msg}"
            )
        else:
            logger.error(f"Failed to fetch Strava activities: {e}")
        sys.exit(1)
    
    if not activities:
        logger.info("No activities found to sync")
        return

    # Fetch HR zones once for the athlete
    hr_zones = strava.get_athlete_zones()
    if hr_zones:
        logger.info("Strava HR zones loaded: %d zones", len(hr_zones))
    else:
        logger.info("Strava HR zones not available; HR zone minutes will be skipped")
    
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
        # Very defensive: make sure each item is a dict from Strava, not an error string
        if not isinstance(activity, dict):
            logger.error("Unexpected activity payload (not a dict): %r", activity)
            stats["failed"] += 1
            continue

        activity_id = str(activity.get("id"))
        has_hr = bool(activity.get("has_heartrate"))
        sport_type = activity.get("type", "")
        moving_time_s = int(activity.get("moving_time") or 0)
        distance_m = float(activity.get("distance") or 0.0)

        # Default HR-related annotations
        activity["_hr_zone_minutes"] = None
        activity["_drift_metrics"] = None
        activity["_drift_eligible"] = False
        activity["_hr_data_quality"] = "None"
        activity["_photo_url"] = None
        activity["_weather"] = None
        activity["_load_pts"] = None  # Zone-weighted load points

        # Determine if this activity is eligible for drift analysis
        min_moving_time_s = DRIFT_MIN_MOVING_TIME_MINUTES * SECONDS_PER_MINUTE
        min_distance_m = DRIFT_MIN_DISTANCE_MILES / METERS_TO_MILES
        basic_drift_eligible = (
            has_hr
            and sport_type in PACE_SPORTS
            and moving_time_s >= min_moving_time_s
            and distance_m >= min_distance_m
        )

        # Fetch HR streams only when we actually need them (zones and/or drift)
        streams = None
        if has_hr and (hr_zones or basic_drift_eligible):
            streams = strava.get_activity_hr_stream(activity.get("id"))

        if streams:
            hr_values = streams.get("hr") or []
            t_values = streams.get("time") or []
            # Basic coverage checks for data quality
            n_samples = min(len(hr_values), len(t_values))
            duration_stream = (t_values[-1] - t_values[0]) if n_samples >= 2 else 0

            coverage_ok = (
                n_samples >= DRIFT_MIN_HR_SAMPLES
                or duration_stream >= max(moving_time_s * DRIFT_MIN_DURATION_FRACTION, DRIFT_MIN_DURATION_SECONDS_FALLBACK)
            )

            # HR Data Quality classification
            if not has_hr or n_samples == 0:
                activity["_hr_data_quality"] = "None"
            elif coverage_ok:
                activity["_hr_data_quality"] = "Good"
            else:
                activity["_hr_data_quality"] = "Partial"

            # HR zones (we can compute even with partial coverage)
            if hr_zones:
                hr_zone_minutes = StravaClient.compute_hr_zone_minutes(streams, hr_zones)
                if hr_zone_minutes:
                    activity["_hr_zone_minutes"] = hr_zone_minutes
                    # Compute load points ONLY if:
                    # 1. Sport is cardio (eligible for load)
                    # 2. HR Data Quality is "Good" (conservative gating)
                    if sport_type in CARDIO_SPORTS and activity["_hr_data_quality"] == "Good":
                        load_pts = compute_zone_weighted_load_points(hr_zone_minutes)
                        if load_pts is not None and load_pts > 0:
                            activity["_load_pts"] = load_pts
                else:
                    logger.debug(
                        "HR zones not computed for activity %s (%s): insufficient stream data",
                        activity.get("name"),
                        activity_id,
                    )

            # Drift metrics only if basic criteria + Good data
            if basic_drift_eligible and coverage_ok:
                drift_metrics = StravaClient.compute_hr_drift(
                    streams, moving_time_s, distance_m
                )
                if drift_metrics is not None:
                    activity["_drift_metrics"] = drift_metrics
                    activity["_drift_eligible"] = True
                else:
                    logger.debug(
                        "HR drift not computed for activity %s (%s): could not derive stable metrics",
                        activity.get("name"),
                        activity_id,
                    )
            elif basic_drift_eligible and not coverage_ok:
                logger.debug(
                    "Activity %s (%s) drift-eligible by type/length but HR data quality is %s; skipping drift",
                    activity.get("name"),
                    activity_id,
                    activity.get("_hr_data_quality"),
                )

        # Find existing page early (before fetching optional data)
        existing_page_id = existing_map.get(activity_id)
        if not existing_page_id:
            # Fallback to per-activity search if not in batch map
            existing_page_id = notion.find_page_by_activity_id(activity_id)
        
        # Fetch primary photo URL only when creating a new page (no existing_page_id)
        if not existing_page_id:
            photo_url = strava.get_activity_primary_photo_url(activity.get("id"))
            if photo_url:
                activity["_photo_url"] = photo_url
        
        # Fetch weather data only for NEW outdoor activities (weather doesn't change for past activities)
        # The update_weather.py script handles backfilling missing weather for existing activities
        if not existing_page_id and sport_type not in INDOOR_SPORTS:
            # Strava API uses start_latlng (array format [lat, lng]) as the primary field
            start_latlng = activity.get("start_latlng")
            if start_latlng and len(start_latlng) >= 2 and start_latlng[0] is not None and start_latlng[1] is not None:
                start_lat, start_lng = start_latlng[0], start_latlng[1]
            else:
                # Fallback to separate fields (if available)
                start_lat = activity.get("start_latitude")
                start_lng = activity.get("start_longitude")
            
            if start_lat and start_lng:
                try:
                    # Parse start_date to get datetime for weather lookup
                    start_date = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
                    logger.info(f"Fetching weather for activity {activity_id} at ({start_lat}, {start_lng}) on {start_date.date()}")
                    weather = weather_client.get_weather_for_activity(start_lat, start_lng, start_date)
                    if weather:
                        activity["_weather"] = weather
                        logger.info(f"Weather fetched for activity {activity_id}: {WeatherClient.make_weather_summary(weather)}")
                    else:
                        logger.warning(f"No weather data returned for activity {activity_id} (may be too recent or API error)")
                except Exception as e:
                    logger.warning(f"Error fetching weather for activity {activity_id}: {e}")
                    import traceback
                    logger.debug(f"Weather fetch traceback: {traceback.format_exc()}")
        
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
        time.sleep(NOTION_RATE_LIMIT_DELAY_SECONDS)
    
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
    
    # Initialize run stats (will be updated during optional syncs)
    run_stats = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workouts": {
            "fetched": stats["fetched"],
            "created": stats["created"],
            "updated": stats["updated"],
            "skipped": stats["skipped"],
            "failed": stats["failed"],
        },
        "daily_summary": {
            "enabled": bool(notion_daily_summary_db_id),
            "days_processed": 0,
            "failed": 0,
        },
        "athlete_metrics": {
            "enabled": bool(notion_athlete_metrics_db_id),
            "upserted": False,
            "failed": False,
        },
        "warnings": [],
        "errors": [],
    }
    
    # Optional: Daily Summary sync
    daily_summaries = None  # Will be computed if needed
    if notion_daily_summary_db_id:
        try:
            logger.info("Syncing Daily Summary database...")
            daily_summary_client = NotionClient(notion_token, notion_daily_summary_db_id)
            daily_summaries = aggregate_daily_summaries(activities, days)
            
            daily_summary_stats = {"created": 0, "updated": 0, "failed": 0}
            for date_iso, summary in daily_summaries.items():
                success = daily_summary_client.upsert_daily_summary(date_iso, summary)
                if success:
                    daily_summary_stats["created"] += 1  # Upsert doesn't distinguish
                else:
                    daily_summary_stats["failed"] += 1
                time.sleep(NOTION_RATE_LIMIT_DELAY_SECONDS)
            
            logger.info(
                "Daily Summary sync: %d days processed, %d failed",
                len(daily_summaries),
                daily_summary_stats["failed"],
            )
            run_stats["daily_summary"]["days_processed"] = len(daily_summaries)
            run_stats["daily_summary"]["failed"] = daily_summary_stats["failed"]
        except Exception as e:
            error_msg = f"Failed to sync Daily Summary database: {e}"
            logger.warning(error_msg)
            run_stats["warnings"].append(error_msg)
            # Don't abort - this is optional
    
    # Optional: Athlete Metrics sync
    if notion_athlete_metrics_db_id:
        try:
            logger.info("Syncing Athlete Metrics database...")
            athlete_metrics_client = NotionClient(notion_token, notion_athlete_metrics_db_id)
            
            # Compute rolling loads from daily summaries (preferred if already computed, otherwise compute now)
            # Use local timezone for "today" to match daily summary date bucketing
            # For simplicity, use UTC now and convert to date (daily summaries use local dates from Strava)
            today = datetime.now(timezone.utc)
            if daily_summaries is None:
                # Compute daily summaries for rolling load calculation
                daily_summaries = aggregate_daily_summaries(activities, days)
            
            rolling_loads = compute_rolling_loads(daily_summaries, today)
            
            # Compute load balance (7d / 28d)
            # Per spec: if Load_28d == 0 or missing, set Load_Balance to None (don't divide by zero)
            load_balance = None
            load_7d_val = rolling_loads.get("load_7d", 0.0)
            load_28d_val = rolling_loads.get("load_28d", 0.0)
            if load_28d_val > 0:
                load_balance = round(load_7d_val / load_28d_val, 3)
            
            # Only include load values if they're > 0 (otherwise leave as None/empty in Notion)
            load_7d_val = rolling_loads.get("load_7d", 0.0)
            load_28d_val = rolling_loads.get("load_28d", 0.0)
            metrics = {
                "load_7d": load_7d_val if load_7d_val > 0 else None,
                "load_28d": load_28d_val if load_28d_val > 0 else None,
                "load_balance": load_balance,
            }
            
            success = athlete_metrics_client.upsert_athlete_metrics(athlete_name, metrics)
            if success:
                logger.info(f"Athlete Metrics sync: {athlete_name} metrics updated")
                run_stats["athlete_metrics"]["upserted"] = True
            else:
                error_msg = f"Athlete Metrics sync: failed to update {athlete_name} metrics"
                logger.warning(error_msg)
                run_stats["athlete_metrics"]["failed"] = True
                run_stats["warnings"].append(error_msg)
        except Exception as e:
            error_msg = f"Failed to sync Athlete Metrics database: {e}"
            logger.warning(error_msg)
            run_stats["athlete_metrics"]["failed"] = True
            run_stats["warnings"].append(error_msg)
            # Don't abort - this is optional
    
    # Persist run stats to file for weekly reporting
    try:
        stats_dir = Path(__file__).parent / "stats"
        stats_dir.mkdir(exist_ok=True)
        stats_file = stats_dir / "run_stats.json"
        
        # Read existing stats (append to list)
        all_stats = []
        if stats_file.exists():
            try:
                with open(stats_file, "r") as f:
                    existing = json.load(f)
                    if isinstance(existing, list):
                        all_stats = existing
                    else:
                        # Legacy format (single dict) - convert to list
                        all_stats = [existing]
            except (json.JSONDecodeError, IOError):
                pass
        
        # Append new stats and keep only last 30 days (prune old entries)
        all_stats.append(run_stats)
        
        # Prune entries older than 30 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        all_stats = [
            s for s in all_stats
            if datetime.fromisoformat(s["timestamp"]) > cutoff
        ]
        
        # Write back
        with open(stats_file, "w") as f:
            json.dump(all_stats, f, indent=2)
        
        logger.debug(f"Run stats persisted to {stats_file}")
    except Exception as e:
        logger.debug(f"Failed to persist run stats (non-fatal): {e}")


if __name__ == "__main__":
    sync_strava_to_notion()
