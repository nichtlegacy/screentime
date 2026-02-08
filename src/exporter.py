#!/usr/bin/env python3
"""
Apple Screen Time Exporter - Exporter
Exports data to Home Assistant and InfluxDB
"""

import json
import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

# Load .env from parent directory
load_dotenv(Path(__file__).parent.parent / ".env")

# --- CONFIGURATION ---
SCRIPT_DIR = Path(__file__).parent.parent

# Home Assistant
HA_URL = os.getenv("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")

# InfluxDB
INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "home")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "screentime")

# Paths
CSV_FILE = SCRIPT_DIR / "data" / "screentime.csv"
LAST_EXPORT_FILE = SCRIPT_DIR / "data" / ".last_export_timestamp"

# App Categories
CATEGORIES = {
    # Social
    "Discord": "social",
    "X (Twitter)": "social",
    "Instagram": "social",
    "Snapchat": "social",
    "TikTok": "social",
    "Reddit": "social",
    "WhatsApp": "social",
    "WhatsApp Business": "social",
    "WhatsApp Messenger": "social",

    # Communication
    "Messages": "communication",
    "Phone": "communication",
    "Phone Call": "communication",
    "Microsoft Teams": "communication",
    "Gmail": "communication",
    "Outlook": "communication",
    "WEB.DE Mail": "communication",

    # Productivity
    "Xcode": "productivity",
    "VS Code": "productivity",
    "Terminal": "productivity",
    "Finder": "productivity",
    "Antigravity": "productivity",
    "GitHub": "productivity",
    "Personio": "productivity",

    # Browser
    "Chrome": "browser",
    "Safari": "browser",

    # Media
    "Spotify": "media",
    "Photos": "media",
    "Camera": "media",
    "Your Spotify Stats": "media",

    # Utilities
    "Google Gemini": "utilities",
    "Home Assistant": "utilities",
    "MS Authenticator": "utilities",
    "Clock": "utilities",
    "Calendar": "utilities",
    "Weather": "utilities",
    "Settings": "utilities",
    "System Settings": "utilities",
    "App Store": "utilities",
    "UniFi": "utilities",

    # Shopping
    "Amazon": "shopping",
    "DHL": "shopping",
    "Kleinanzeigen": "shopping",
    "Mydealz": "shopping",
    "Lidl Plus": "shopping",
}

# Normalize long app names to short ones
TITLE_NORMALIZE = {
    "TikTok - Videos, Shop & LIVE": "TikTok",
    "Discord - Talk, Chat & Hang Out": "Discord",
    "WhatsApp Messenger": "WhatsApp",
    "Instagram": "Instagram",
    "Snapchat": "Snapchat",
    "Reddit": "Reddit",
    "Gmail - Email by Google": "Gmail",
    "Google Chrome": "Chrome",
    "Microsoft Teams": "Microsoft Teams",
    "Microsoft Outlook": "Outlook",
    "Spotify: Music and Podcasts": "Spotify",
    "Kleinanzeigen - without eBay": "Kleinanzeigen",
}

def get_category(title: str) -> str:
    """Determines the category of an app."""
    if title in CATEGORIES:
        return CATEGORIES[title]

    # Detect system apps
    if title.startswith("System:") or title in ["Lock Screen", "Control Center", "Clock Widget"]:
        return "system"

    return "other"


def get_last_export_timestamp() -> float:
    """Reads the last export timestamp."""
    if LAST_EXPORT_FILE.exists():
        try:
            return float(LAST_EXPORT_FILE.read_text().strip())
        except:
            pass
    return 0.0


def save_last_export_timestamp(ts: float):
    """Saves the last export timestamp."""
    LAST_EXPORT_FILE.write_text(str(ts))


def load_data(since_timestamp: float = 0) -> pd.DataFrame:
    """Loads the CSV and filters for new data."""
    if not CSV_FILE.exists():
        print(f"CSV not found: {CSV_FILE}")
        return pd.DataFrame()

    df = pd.read_csv(CSV_FILE)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    df["unix_ts"] = df["timestamp"].apply(lambda x: int(x.timestamp()))

    # Nur neue Daten
    if since_timestamp > 0:
        df = df[df["unix_ts"] > since_timestamp]

    # Normalize titles (long App Store names -> short)
    df["title"] = df["title"].apply(lambda t: TITLE_NORMALIZE.get(t, t))

    # Add category
    df["category"] = df["title"].apply(get_category)

    return df


def export_to_influxdb(df: pd.DataFrame) -> bool:
    """
    Writes raw data to InfluxDB in Line Protocol format.

    Schema:
      screentime,source=iphone,app=com.google.Chrome,title=Chrome,category=browser duration=45.5 1707400000000000000
    """
    if df.empty:
        print("[InfluxDB] No data to export")
        return True

    if not INFLUX_TOKEN:
        print("[InfluxDB] INFLUX_TOKEN not set - skipping")
        return False

    lines = []
    for _, row in df.iterrows():
        # Escape special characters in tag values
        source = row["source"].replace(" ", "\\ ").replace(",", "\\,")
        app = row["app"].replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
        title = row["title"].replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
        category = row["category"].replace(" ", "\\ ").replace(",", "\\,")

        # Timestamp in nanoseconds
        ts_ns = int(row["timestamp"].timestamp() * 1e9)

        line = f'screentime,source={source},app={app},title={title},category={category} duration={row["duration"]} {ts_ns}'
        lines.append(line)

    # Batch write
    data = "\n".join(lines)

    try:
        response = requests.post(
            f"{INFLUX_URL}/api/v2/write",
            params={"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "ns"},
            headers={
                "Authorization": f"Token {INFLUX_TOKEN}",
                "Content-Type": "text/plain; charset=utf-8"
            },
            data=data.encode('utf-8'),
            timeout=30
        )

        if response.status_code == 204:
            print(f"[InfluxDB] {len(lines)} data points written")
            return True
        else:
            print(f"[InfluxDB] Error {response.status_code}: {response.text[:200]}")
            return False

    except Exception as e:
        print(f"[InfluxDB] Connection error: {e}")
        return False


def calculate_daily_aggregates(df: pd.DataFrame, target_date: datetime.date = None) -> dict:
    """
    Calculates daily aggregates for Home Assistant sensors.

    Returns:
        {
            "total_minutes": 245.5,
            "iphone_minutes": 180.0,
            "mac_minutes": 65.5,
            "top_app": "Chrome",
            "top_app_minutes": 45.0,
            "by_category": {"social": 90, "productivity": 60, ...},
            "by_app": {"Chrome": 45, "Discord": 30, ...},
            "session_count": 150,
        }
    """
    if target_date is None:
        target_date = datetime.now().date()

    # Filter for target day
    df_day = df[df["timestamp"].dt.date == target_date]

    if df_day.empty:
        return None

    # Base aggregates
    total_seconds = df_day["duration"].sum()
    iphone_seconds = df_day[df_day["source"] == "iphone"]["duration"].sum()
    mac_seconds = df_day[df_day["source"] == "mac"]["duration"].sum()

    # Top App
    app_totals = df_day.groupby("title")["duration"].sum().sort_values(ascending=False)
    top_app = app_totals.index[0] if len(app_totals) > 0 else "Unknown"
    top_app_seconds = app_totals.iloc[0] if len(app_totals) > 0 else 0

    # By category
    by_category = df_day.groupby("category")["duration"].sum().to_dict()
    by_category = {k: round(v / 60, 1) for k, v in by_category.items()}

    # Top 10 apps
    by_app = app_totals.head(10).to_dict()
    by_app = {k: round(v / 60, 1) for k, v in by_app.items()}

    return {
        "total_minutes": round(total_seconds / 60, 1),
        "iphone_minutes": round(iphone_seconds / 60, 1),
        "mac_minutes": round(mac_seconds / 60, 1),
        "top_app": top_app,
        "top_app_minutes": round(top_app_seconds / 60, 1),
        "by_category": by_category,
        "by_app": by_app,
        "session_count": len(df_day),
    }


def update_ha_sensor(entity_id: str, state: any, attributes: dict = None, unit: str = None):
    """Updates a Home Assistant sensor via REST API."""
    if not HA_TOKEN:
        print(f"[HA] HA_TOKEN not set - skipping {entity_id}")
        return False

    url = f"{HA_URL}/api/states/{entity_id}"

    payload = {
        "state": state,
        "attributes": attributes or {}
    }

    if unit:
        payload["attributes"]["unit_of_measurement"] = unit

    payload["attributes"]["state_class"] = "measurement"
    payload["attributes"]["last_updated"] = datetime.now().isoformat()

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {HA_TOKEN}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=10
        )

        if response.status_code in [200, 201]:
            print(f"[HA] {entity_id} = {state}")
            return True
        else:
            print(f"[HA] Error {response.status_code} for {entity_id}: {response.text[:100]}")
            return False

    except Exception as e:
        print(f"[HA] Connection error: {e}")
        return False


def export_to_homeassistant(df: pd.DataFrame):
    """Exports daily aggregates as Home Assistant sensors."""
    if not HA_TOKEN:
        print("[HA] HA_TOKEN not set - skipping Home Assistant export")
        return

    today = datetime.now().date()
    aggregates = calculate_daily_aggregates(df, today)

    if not aggregates:
        print("[HA] No data for today")
        return

    # Update sensors
    sensors = [
        ("sensor.screentime_total", aggregates["total_minutes"], "min", {
            "friendly_name": "Screen Time Total",
            "icon": "mdi:cellphone-screen",
            "session_count": aggregates["session_count"],
        }),
        ("sensor.screentime_iphone", aggregates["iphone_minutes"], "min", {
            "friendly_name": "Screen Time iPhone",
            "icon": "mdi:cellphone",
        }),
        ("sensor.screentime_mac", aggregates["mac_minutes"], "min", {
            "friendly_name": "Screen Time Mac",
            "icon": "mdi:laptop",
        }),
        ("sensor.screentime_top_app", aggregates["top_app"], None, {
            "friendly_name": "Top App Today",
            "icon": "mdi:trophy",
            "minutes": aggregates["top_app_minutes"],
        }),
    ]

    for entity_id, state, unit, attrs in sensors:
        update_ha_sensor(entity_id, state, attrs, unit)

    # Category sensor with all values as attributes
    update_ha_sensor(
        "sensor.screentime_by_category",
        aggregates["by_category"].get("social", 0),  # Social als Hauptwert
        {
            "friendly_name": "Screen Time by Category",
            "icon": "mdi:chart-pie",
            **{f"category_{k}": v for k, v in aggregates["by_category"].items()}
        },
        "min"
    )

    # Top apps as attributes
    update_ha_sensor(
        "sensor.screentime_top_apps",
        len(aggregates["by_app"]),
        {
            "friendly_name": "Screen Time Top Apps",
            "icon": "mdi:format-list-numbered",
            **aggregates["by_app"]
        },
        "apps"
    )


def main():
    print(f"=== Screen Time Export - {datetime.now().isoformat()} ===\n")

    # Load last export timestamp
    last_export = get_last_export_timestamp()
    if last_export > 0:
        print(f"Last export: {datetime.fromtimestamp(last_export).isoformat()}")
    else:
        print("First export - all data will be exported")

    # Load data
    df = load_data(since_timestamp=last_export)
    print(f"Loaded data: {len(df)} new entries")

    if df.empty:
        print("\nNo new data to export.")
        return

    # Export to InfluxDB (raw data)
    print("\n--- InfluxDB Export ---")
    influx_success = export_to_influxdb(df)

    # Export to Home Assistant (aggregates)
    print("\n--- Home Assistant Export ---")
    # For HA we need all data from today, not just new
    df_full = load_data(since_timestamp=0)
    export_to_homeassistant(df_full)

    # Save last timestamp
    if influx_success and len(df) > 0:
        max_ts = df["unix_ts"].max()
        save_last_export_timestamp(max_ts)
        print(f"\nExport completed. Last timestamp: {datetime.fromtimestamp(max_ts).isoformat()}")


if __name__ == "__main__":
    main()
