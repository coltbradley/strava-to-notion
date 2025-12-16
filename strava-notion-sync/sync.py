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
import random
import hashlib
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Callable, Any

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


def _token_fingerprint(token: str) -> str:
    """Return a short, non-reversible fingerprint for a token for debugging."""
    if not token:
        return "none"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]


def http_request_with_retries(
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    timeout: int = 30,
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
            sleep_seconds = backoff_factor * (2 ** (attempts - 1))
            sleep_seconds += random.uniform(0, 0.25)
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
    
    def get_recent_activities(self, days: int = 180) -> List[Dict]:
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
        return {zone: round(seconds / 60, 2) for zone, seconds in zone_counts.items()}

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
        if avg_vel_1 <= 0.1 or avg_vel_2 <= 0.1:
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


class NotionClient:
    """Client for interacting with Notion API with upsert support."""
    
    def __init__(self, api_key: str, database_id: str):
        self.client = Client(auth=api_key)
        # Keep a copy of the raw API key for low-level HTTP fallbacks
        self.api_key = api_key
        self.database_id = database_id
        self.allowed_properties: Optional[set[str]] = None

    def _ensure_schema_loaded(self) -> None:
        """Retrieve and cache the Notion database schema."""
        if self.allowed_properties is not None:
            return
        try:
            db = self._notion_call_with_retries(
                self.client.databases.retrieve,
                database_id=self.database_id,
            )
            props = db.get("properties", {}) if isinstance(db, dict) else {}
            keys = set(props.keys())
            # If we somehow see zero properties, treat this as a soft failure so we
            # don't silently drop all writes. Better to let Notion validate.
            if not keys:
                logger.warning(
                    "Loaded Notion database schema but found 0 properties. "
                    "Schema-based filtering will be disabled; writes will include "
                    "all generated properties and rely on Notion for validation."
                )
                self.allowed_properties = None
            else:
                self.allowed_properties = keys
                logger.info(
                    "Loaded Notion database schema; %d properties available: %s",
                    len(self.allowed_properties),
                    sorted(self.allowed_properties),
                )
        except Exception as e:
            logger.warning(
                "Could not load Notion database schema; will attempt writes without "
                "schema filtering. Errors may occur for unknown properties: %s",
                e,
            )
            self.allowed_properties = None

    @staticmethod
    def _notion_call_with_retries(
        func: Callable[..., Any],
        *args: Any,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
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
                    sleep_seconds += random.uniform(0, 0.25)
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
    
    def get_existing_activity_pages(self, days: int = 180) -> Dict[str, str]:
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
                    "property": "Date",
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
            response = self._database_query(
                database_id=self.database_id,
                filter={
                    "property": "Activity ID",
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
        self._ensure_schema_loaded()

        # Optionally set Sync Status if DB supports it
        if self.allowed_properties and "Sync Status" in self.allowed_properties:
            sync_status_value = "updated" if existing_page_id else "created"
            properties["Sync Status"] = {
                "select": {"name": sync_status_value}
            }

        # Remove None values and properties that don't exist in this database
        if self.allowed_properties is not None:
            properties = {
                k: v
                for k, v in properties.items()
                if v is not None and k in self.allowed_properties
            }
        else:
            properties = {k: v for k, v in properties.items() if v is not None}
        
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

        # HR drift / decoupling metrics (if computed)
        drift = activity.get("_drift_metrics")
        if drift is not None:
            drift_pct = drift.get("drift_pct")
            if drift_pct is not None:
                properties["HR Drift (%)"] = {"number": round(drift_pct, 2)}

            avg_hr_1 = drift.get("avg_hr_1")
            avg_hr_2 = drift.get("avg_hr_2")
            if avg_hr_1 is not None:
                properties["HR 1st Half (bpm)"] = {"number": round(avg_hr_1, 1)}
            if avg_hr_2 is not None:
                properties["HR 2nd Half (bpm)"] = {"number": round(avg_hr_2, 1)}

            # Convert m/s to mph for storage
            mps_to_mph = 2.236936
            vel1 = drift.get("avg_vel_1_mps")
            vel2 = drift.get("avg_vel_2_mps")
            if vel1 is not None:
                properties["Speed 1st Half (mph)"] = {
                    "number": round(vel1 * mps_to_mph, 2)
                }
            if vel2 is not None:
                properties["Speed 2nd Half (mph)"] = {
                    "number": round(vel2 * mps_to_mph, 2)
                }

        # Drift eligibility & HR data quality indicators
        drift_eligible = activity.get("_drift_eligible")
        if drift_eligible is not None:
            properties["Drift Eligible"] = {"checkbox": bool(drift_eligible)}

        hr_data_quality = activity.get("_hr_data_quality")
        if hr_data_quality:
            properties["HR Data Quality"] = {"select": {"name": hr_data_quality}}

        # Primary photo URL (optional)
        photo_url = activity.get("_photo_url")
        if photo_url:
            properties["Photo URL"] = {"url": photo_url}

        return properties


def sync_strava_to_notion(days: int = 180, failure_threshold: float = 0.2):
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

        # Determine if this activity is eligible for drift analysis
        min_moving_time_s = 20 * 60  # 20 minutes
        min_distance_m = 3.0 / 0.000621371  # 3 miles in meters
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
                n_samples >= 120
                or duration_stream >= max(moving_time_s * 0.8, 10 * 60)  # >= 80% or 10 min
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

        # Fetch primary photo URL only when creating a new page (no existing_page_id)
        existing_page_id = existing_map.get(activity_id)
        if not existing_page_id:
            photo_url = strava.get_activity_primary_photo_url(activity.get("id"))
            if photo_url:
                activity["_photo_url"] = photo_url
        
        # Find existing page (may have already been looked up above for photos)
        existing_page_id = existing_map.get(activity_id) or existing_page_id
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
