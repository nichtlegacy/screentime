#!/usr/bin/env python3
"""
Apple Screen Time Exporter - Collector
Collects screen time data from Mac (knowledgeC.db) and iPhone (Biome)
"""

import csv
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load .env from parent directory
load_dotenv(Path(__file__).parent.parent / ".env")

# --- CONFIGURATION ---
DEVICE_ID = os.getenv("DEVICE_ID", "")
SCRIPT_DIR = Path(__file__).parent.parent
OUTPUT_CSV = SCRIPT_DIR / "data" / "screentime.csv"
LAST_TIMESTAMP_FILE = SCRIPT_DIR / "data" / "screentime.csv.last"
AW_BIN = SCRIPT_DIR / "aw-import-screentime" / ".venv" / "bin" / "aw-import-screentime"

# Mac knowledgeC.db - the official Screen Time database
KNOWLEDGE_DB = Path.home() / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"

# Apple Epoch offset (seconds between 1970-01-01 and 2001-01-01)
APPLE_EPOCH_OFFSET = 978307200

# Mapping for known apps (bundle_id -> display name)
APP_MAP = {
    # Social
    "com.hammerandchisel.discord": "Discord",
    "com.hnc.Discord": "Discord",
    "com.atebits.Tweetie2": "X (Twitter)",
    "com.burbn.instagram": "Instagram",
    "com.toyopagroup.picaboo": "Snapchat",
    "com.zhiliaoapp.musically": "TikTok",
    "com.reddit.Reddit": "Reddit",
    "net.whatsapp.WhatsApp": "WhatsApp",
    "net.whatsapp.WhatsAppSMB": "WhatsApp Business",

    # Communication
    "com.apple.MobileSMS": "Messages",
    "com.apple.mobilephone": "Phone",
    "com.apple.InCallService": "Phone Call",
    "com.microsoft.skype.teams": "Microsoft Teams",
    "com.google.Gmail": "Gmail",
    "de.web.mobilenavigator": "WEB.DE Mail",
    "com.microsoft.Office.Outlook": "Outlook",

    # Productivity
    "com.apple.dt.Xcode": "Xcode",
    "com.microsoft.VSCode": "VS Code",
    "com.apple.Terminal": "Terminal",
    "com.apple.finder": "Finder",
    "com.google.antigravity": "Antigravity",
    "com.personio": "Personio",
    "com.github.stormbreaker.prod": "GitHub",

    # Browser
    "com.google.Chrome": "Chrome",
    "com.google.chrome.ios": "Chrome",
    "com.apple.Safari": "Safari",

    # Media
    "com.spotify.client": "Spotify",
    "com.apple.mobileslideshow": "Photos",
    "com.apple.camera": "Camera",

    # Utilities
    "com.google.gemini": "Google Gemini",
    "io.robbie.HomeAssistant": "Home Assistant",
    "com.microsoft.azureauthenticator": "MS Authenticator",
    "com.apple.mobiletimer": "Clock",
    "com.apple.mobilecal": "Calendar",
    "com.apple.weather": "Weather",
    "com.apple.Preferences": "Settings",
    "com.apple.systempreferences": "System Settings",
    "com.apple.AppStore": "App Store",
    "com.ubnt.unifiac": "UniFi",

    # Shopping
    "com.amazon.AmazonDE": "Amazon",
    "de.deutschepost.dhl": "DHL",
    "com.ebaykleinanzeigen.ebc": "Kleinanzeigen",
    "com.6minutesmedia.mydealz": "Mydealz",
    "com.lidl.eci.lidl.plus": "Lidl Plus",

    # System (often ignorable)
    "com.apple.springboard.home-screen-open-folder": "System: Folder",
    "com.apple.springboard.today-view": "System: Today View",
    "com.apple.control-center": "Control Center",
    "com.apple.SleepLockScreen": "Lock Screen",
    "com.apple.ClockAngel": "Clock Widget",
    "com.apple.iphonesimulator": "iPhone Simulator",

    # Other
    "nichtlegacy.your-spotify": "Your_Spotify",
}

def get_app_title(bundle_id):
    """Returns a display name for the app with intelligent fallback."""
    # 1. Exaktes Mapping
    if bundle_id in APP_MAP:
        return APP_MAP[bundle_id]

    # 2. Fallback: Take last part of bundle ID and format it
    if "." in bundle_id:
        name = bundle_id.split(".")[-1]
        # CamelCase to spaces: "MobileSMS" -> "Mobile SMS"
        name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
        # Capitalize first letters
        name = name.title()
        return name

    return bundle_id

def get_last_timestamp():
    """Reads the last extraction timestamp from a separate file."""
    if LAST_TIMESTAMP_FILE.exists():
        try:
            with open(LAST_TIMESTAMP_FILE, "r") as f:
                return float(f.read().strip())
        except:
            pass
    return 0.0

def save_last_timestamp(ts):
    """Saves the last extraction timestamp."""
    with open(LAST_TIMESTAMP_FILE, "w") as f:
        f.write(str(ts))

def get_mac_data(last_created_at):
    """Extracts Mac Screen Time from knowledgeC.db."""
    if not KNOWLEDGE_DB.exists():
        print(f"[Mac] knowledgeC.db not found: {KNOWLEDGE_DB}")
        return []

    if not os.access(KNOWLEDGE_DB, os.R_OK):
        print("[Mac] knowledgeC.db not readable. Terminal needs Full Disk Access.")
        return []

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Extracting Mac data...")

    query = """
    SELECT
        ZOBJECT.ZVALUESTRING AS "app",
        (ZOBJECT.ZENDDATE - ZOBJECT.ZSTARTDATE) AS "usage",
        (ZOBJECT.ZSTARTDATE + 978307200) as "start_time",
        (ZOBJECT.ZENDDATE + 978307200) as "end_time",
        (ZOBJECT.ZCREATIONDATE + 978307200) as "created_at"
    FROM ZOBJECT
    WHERE
        ZSTREAMNAME = "/app/usage" AND
        (ZOBJECT.ZCREATIONDATE + 978307200) > ?
    ORDER BY ZSTARTDATE ASC
    """

    try:
        with sqlite3.connect(f"file:{KNOWLEDGE_DB}?mode=ro", uri=True) as conn:
            cursor = conn.cursor()
            cursor.execute(query, (last_created_at,))
            rows = cursor.fetchall()

            events = []
            for row in rows:
                app, usage, start_time, end_time, created_at = row
                if not app or usage is None:
                    continue

                ts_iso = datetime.fromtimestamp(start_time).astimezone().isoformat()
                title = get_app_title(app)

                events.append({
                    "timestamp": ts_iso,
                    "app": app,
                    "title": title,
                    "duration": round(usage, 2),
                    "source": "mac",
                    "_created_at": created_at
                })

            print(f"[Mac] {len(events)} new entries found")
            return events
    except Exception as e:
        print(f"[Mac] Error: {e}")
        return []

def get_iphone_data(last_created_at):
    """Extracts iPhone Screen Time via aw-import-screentime."""
    if not DEVICE_ID:
        print("[iPhone] DEVICE_ID not set in .env - skipping")
        print("[iPhone] Tip: cd aw-import-screentime && .venv/bin/aw-import-screentime devices")
        return []

    if not AW_BIN.exists():
        print(f"[iPhone] aw-import-screentime nicht gefunden: {AW_BIN}")
        return []

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Extracting iPhone data...")

    # Always query 28 days, deduplication happens via created_at
    cmd = [str(AW_BIN), "events", "preview", "--device", DEVICE_ID, "--since", "28d"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
        data = json.loads(result.stdout)
        events = []

        if data and "events" in data[0]:
            for event in data[0]["events"]:
                ts = event["timestamp"]
                duration = event.get("duration_seconds", 0)

                # Parse timestamp für created_at Vergleich
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    end_time = dt.timestamp() + duration
                    created_at = end_time

                    # Skip already collected events
                    if created_at <= last_created_at:
                        continue
                except:
                    continue

                bundle_id = event["data"].get("app", "unknown")
                title = event["data"].get("title", "unknown")

                if title == "unknown" or not title:
                    title = APP_MAP.get(bundle_id, bundle_id.split(".")[-1])

                events.append({
                    "timestamp": ts,
                    "app": bundle_id,
                    "title": title,
                    "duration": round(duration, 2),
                    "source": "iphone",
                    "_created_at": created_at
                })

        print(f"[iPhone] {len(events)} new entries found")
        return events
    except Exception as e:
        print(f"[iPhone] Error: {e}")
        return []

def save_to_csv(events):
    if not events:
        print("\nNo new data since last run.")
        return

    # Remove internal _created_at fields before writing
    max_created_at = max(ev.get("_created_at", 0) for ev in events)
    for ev in events:
        ev.pop("_created_at", None)

    file_exists = OUTPUT_CSV.exists()
    with open(OUTPUT_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "app", "title", "duration", "source"])
        if not file_exists:
            writer.writeheader()
        writer.writerows(events)

    # Save last timestamp for deduplication
    if max_created_at > 0:
        save_last_timestamp(max_created_at)

    print(f"\nSuccess: {len(events)} NEW entries added.")

if __name__ == "__main__":
    print(f"=== Screen Time Collection - {datetime.now().isoformat()} ===\n")

    last_ts = get_last_timestamp()
    if last_ts > 0:
        print(f"Last extraction: {datetime.fromtimestamp(last_ts).isoformat()}")
    else:
        print("First run - extracting all available data")

    # Collect from both sources
    iphone_events = get_iphone_data(last_ts)
    mac_events = get_mac_data(last_ts)

    # Combine and sort by timestamp
    all_events = iphone_events + mac_events
    all_events.sort(key=lambda x: x["timestamp"])

    print(f"\n  iPhone: {len(iphone_events)} Events")
    print(f"  Mac:    {len(mac_events)} Events")

    save_to_csv(all_events)