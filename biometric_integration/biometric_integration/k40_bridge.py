#!/usr/bin/env python3
"""
K40 to ERPNext Bridge
Pulls attendance punches from K40 device and pushes to ERPNext as Employee Checkins.

Flow:
  K40 Device → k40_bridge.py → ERPNext API → Employee Checkin
  Then at 23:58, ERPNext scheduler creates Attendance from first/last checkins.
"""

import time
import logging
import json
import os
from datetime import datetime, date, timedelta
from zk import ZK
import requests

# ============================================
# CONFIGURATION
# ============================================
K40_IP = '192.168.18.200'
K40_PORT = 4370
K40_SERIAL = 'A6F5215360564'

ERPNEXT_URL = 'http://sandboxsarathi.raindropinc.com'
API_PATH = '/api/method/biometric_integration.biometric_integration.biometric_integration.push_bulk_attendance'

SYNC_INTERVAL = 1800  # 30 minutes in seconds

# Time windows — bridge runs within these and exits automatically
# Morning: 09:30 to 11:00 | Evening: 18:30 to 21:30
TIME_WINDOWS = [
    {"start": "09:30", "end": "11:00"},
    {"start": "18:30", "end": "21:30"},
]

LOG_FILE = '/home/raindrop/Desktop/k40_bridge.log'
SYNCED_RECORDS_FILE = '/home/raindrop/Desktop/k40_synced.json'

# ============================================
# SETUP LOGGING
# ============================================
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ============================================
# LOAD PERSISTENT SYNCED RECORDS
# ============================================
synced_records = set()
if os.path.exists(SYNCED_RECORDS_FILE):
    try:
        with open(SYNCED_RECORDS_FILE, 'r') as f:
            synced_records = set(json.load(f))
        logger.info(f"Loaded {len(synced_records)} previously synced records")
    except Exception as e:
        logger.error(f"Error loading synced records: {e}")


def save_synced_records():
    """Persist synced record IDs to disk."""
    try:
        with open(SYNCED_RECORDS_FILE, 'w') as f:
            json.dump(list(synced_records), f)
    except Exception as e:
        logger.error(f"Error saving synced records: {e}")


def get_attendance_from_k40():
    """Connect to K40 and retrieve today's attendance records."""
    try:
        logger.info(f"Connecting to K40 at {K40_IP}:{K40_PORT}")
        conn = ZK(K40_IP, port=K40_PORT, timeout=5)
        zk = conn.connect()

        zk.disable_device()
        attendances = zk.get_attendance()
        zk.enable_device()
        zk.disconnect()

        # Filter to today only
        today_date = date.today()
        today_records = [a for a in attendances if a.timestamp.date() == today_date]

        logger.info(f"Retrieved {len(today_records)} today's records from K40 (total: {len(attendances)})")
        return today_records

    except Exception as e:
        logger.error(f"Error connecting to K40: {e}")
        return []


def send_to_erpnext(records):
    """
    Send a batch of attendance records to ERPNext.
    Only marks records as synced if the server confirms them individually.
    Returns number of newly synced records.
    """
    if not records:
        return 0

    # Build payload — list of punches not already synced locally
    punches = []
    for att in records:
        record_id = f"{att.user_id}_{att.timestamp}"
        if record_id in synced_records:
            continue
        punches.append({
            'employee_id': str(att.user_id),
            'punch_time': att.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        })

    if not punches:
        logger.info("All records already synced locally")
        return 0

    logger.info(f"Sending {len(punches)} new records to ERPNext")

    try:
        url = f"{ERPNEXT_URL}{API_PATH}"
        response = requests.post(
            url,
            json={
                'device_id': K40_SERIAL,
                'punches': json.dumps(punches),
            },
            headers={'Content-Type': 'application/json'},
            timeout=30
        )

        logger.info(f"ERPNext response: {response.status_code} - {response.text[:500]}")

        if response.status_code != 200:
            logger.warning(f"Failed: HTTP {response.status_code}")
            return 0

        resp_json = response.json()
        message = resp_json.get('message', {})

        if isinstance(message, dict) and message.get('success'):
            # Only mark records the server confirmed as synced/skipped
            for rid in message.get('synced_punches', []):
                synced_records.add(rid)
            save_synced_records()

            # Log failed ones — bridge will retry next cycle
            failed = message.get('failed_punches', [])
            if failed:
                logger.warning(f"Server failed {len(failed)} records (will retry): {failed[:5]}")

            synced_count = message.get('synced', 0)
            skipped_count = message.get('skipped', 0)
            error_count = message.get('errors', 0)
            logger.info(f"Result: synced={synced_count}, skipped={skipped_count} duplicates, errors={error_count}")
            return synced_count

        error_msg = message.get('message', 'Unknown error') if isinstance(message, dict) else str(message)
        logger.error(f"ERPNext error: {error_msg}")
        return 0

    except Exception as e:
        logger.error(f"Error sending to ERPNext: {e}")
        return 0


def cleanup_old_synced_records():
    """Remove synced record IDs older than 2 days to prevent file bloat."""
    today_str = date.today().strftime('%Y-%m-%d')
    yesterday_str = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')

    before = len(synced_records)
    to_keep = set()
    for rid in synced_records:
        # record_id format: "user_id_2026-02-12 08:30:00"
        if today_str in rid or yesterday_str in rid:
            to_keep.add(rid)

    synced_records.clear()
    synced_records.update(to_keep)

    removed = before - len(synced_records)
    if removed:
        save_synced_records()
        logger.info(f"Cleaned up {removed} old synced records")


def sync_cycle():
    """One complete sync cycle."""
    logger.info("=" * 50)
    logger.info("Starting sync cycle...")

    # Clean old tracked records to prevent file bloat
    cleanup_old_synced_records()

    records = get_attendance_from_k40()
    if not records:
        logger.info("No records to sync")
        return

    synced = send_to_erpnext(records)
    logger.info(f"Sync cycle complete: {synced} new checkins created")
    logger.info("=" * 50)


def is_within_time_window():
    """Check if current time falls within any configured time window."""
    now = datetime.now().strftime('%H:%M')
    for window in TIME_WINDOWS:
        if window["start"] <= now <= window["end"]:
            return True, window["end"]
    return False, None


def main():
    """Main loop — runs within time windows, exits when window ends."""
    logger.info("K40 Bridge Started")
    logger.info(f"K40 Device: {K40_IP}:{K40_PORT}")
    logger.info(f"ERPNext URL: {ERPNEXT_URL}")
    logger.info(f"Sync Interval: {SYNC_INTERVAL}s (30 min)")
    logger.info(f"Time windows: {TIME_WINDOWS}")

    in_window, window_end = is_within_time_window()
    if not in_window:
        logger.info(f"Not within any time window. Current time: {datetime.now().strftime('%H:%M')}. Exiting.")
        return

    logger.info(f"Within time window (until {window_end}). Starting sync...")

    # Initial sync
    sync_cycle()

    # Keep syncing every 30 mins until window ends
    while True:
        try:
            time.sleep(SYNC_INTERVAL)

            in_window, window_end = is_within_time_window()
            if not in_window:
                logger.info("Time window ended. Exiting.")
                break

            sync_cycle()
        except KeyboardInterrupt:
            logger.info("Stopping bridge...")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(60)


if __name__ == '__main__':
    main()
