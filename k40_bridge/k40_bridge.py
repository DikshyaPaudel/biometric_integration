#!/usr/bin/env python3
"""
K40 to ERPNext Bridge
Pulls attendance from K40 device and pushes to ERPNext.
Run: python3 k40_bridge.py              -> syncs from last run date up to yesterday
Run: python3 k40_bridge.py 2026-02-13   -> syncs from that date up to yesterday
"""

import sys
import json
import logging
import os
from datetime import datetime, date, timedelta
from zk import ZK
import requests
# ============================================
# CONFIGURATION
# ============================================

K40_IP = '192.168.24.246'
K40_SERIAL = 'A6F521360285'
ERPNEXT_URL = 'https://demo-sb.raindropinc.com'
WEBHOOK_PATH = '/api/method/biometric_integration.biometric_integration.biometric_integration.zkteco_push_attendance'

# Files stored next to the exe/script
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
LOG_FILE = os.path.join(BASE_DIR, "k40_bridge.log")
LAST_SYNC_FILE = os.path.join(BASE_DIR, "last_sync.json")

# ============================================
# SETUP LOGGING
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
logger.info(f"Logging started. Log file path: {LOG_FILE}")

# ============================================
# LAST SYNC TRACKING
# ============================================
def get_last_sync_date():
    """Read last synced date from file. Returns None if file doesn't exist."""
    if not os.path.exists(LAST_SYNC_FILE):
        return None
    try:
        with open(LAST_SYNC_FILE, "r") as f:
            data = json.load(f)
        return datetime.strptime(data["last_sync_date"], "%Y-%m-%d").date()
    except Exception as e:
        logger.warning(f"Could not read last sync file: {e}")
        return None


def save_last_sync_date(sync_date):
    """Save the last successfully synced date to file."""
    try:
        with open(LAST_SYNC_FILE, "w") as f:
            json.dump({"last_sync_date": sync_date.strftime("%Y-%m-%d")}, f)
        logger.info(f"Last sync date saved: {sync_date}")
    except Exception as e:
        logger.error(f"Could not save last sync file: {e}")

# ============================================
# FUNCTIONS
# ============================================

def get_attendance_from_k40():
    """Connect to K40 and retrieve attendance records"""
    try:
        logger.info(f"Connecting to K40 at {K40_IP}")
        conn = ZK(K40_IP, timeout=5)
        zk = conn.connect()

        logger.info("Connected! Disabling device...")
        zk.disable_device()

        logger.info("Fetching attendance records...")
        attendances = zk.get_attendance()

        logger.info("Re-enabling device...")
        zk.enable_device()

        logger.info("Disconnecting...")
        zk.disconnect()

        logger.info(f"Retrieved {len(attendances)} records from K40")
        return attendances

    except Exception as e:
        logger.error(f"Error connecting to K40: {e}")
        return []

def send_to_erpnext(attendance):
    """Send single attendance record to ERPNext.
    Returns: 'synced' if success, 'error' if failed.
    """
    try:
        record_id = f"{attendance.user_id}_{attendance.timestamp}"

        data = {
            'device_id': K40_SERIAL,
            'employee_id': str(attendance.user_id),
            'punch_time': attendance.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'punch_type': 'IN'
        }

        url = f"{ERPNEXT_URL}{WEBHOOK_PATH}"
        response = requests.post(
            url,
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )

        logger.info(f"ERPNext Response for {record_id}: {response.status_code} - {response.text[:500]}")

        if response.status_code != 200:
            logger.warning(f"Failed to sync Employee {attendance.user_id}: HTTP {response.status_code}")
            return 'error'

        logger.info(f"Synced: Employee {attendance.user_id} at {attendance.timestamp}")
        return 'synced'

    except Exception as e:
        logger.error(f"Error sending Employee {attendance.user_id} to ERPNext: {e}")
        return 'error'


def main():
    """Main entry point"""
    yesterday = date.today() - timedelta(days=1)

    # Determine start date
    if len(sys.argv) > 1:
        from_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        logger.info(f"Start date from argument: {from_date}")
    else:
        last_sync = get_last_sync_date()
        if last_sync:
            from_date = last_sync + timedelta(days=1)
            logger.info(f"Resuming from last sync: {last_sync} → syncing from {from_date}")
        else:
            from_date = yesterday
            logger.info(f"No previous sync found. Syncing yesterday: {from_date}")

    if from_date > yesterday:
        logger.info(f"Already up to date. Last sync was {from_date - timedelta(days=1)}, nothing new to sync.")
        return

    logger.info("K40 Bridge Started!")
    logger.info(f"K40 Device: {K40_IP}")
    logger.info(f"ERPNext URL: {ERPNEXT_URL}")
    logger.info(f"Syncing from {from_date} to {yesterday}")
    logger.info("="*50)

    # Get all attendance from K40
    attendances = get_attendance_from_k40()

    # Filter to date range: from_date <= date <= yesterday
    attendances = [att for att in attendances if from_date <= att.timestamp.date() <= yesterday]

    if not attendances:
        logger.info(f"No records found between {from_date} and {yesterday}")
        save_last_sync_date(yesterday)
        return

    logger.info(f"Found {len(attendances)} records from {from_date} to {yesterday}")

    synced_count = 0
    error_count = 0

    for att in attendances:
        result = send_to_erpnext(att)
        if result == 'synced':
            synced_count += 1
        else:
            error_count += 1

    logger.info(f"Done: {synced_count} synced, {error_count} errors")
    logger.info("="*50)

    # Save last sync date only if no errors (or partial — save yesterday anyway)
    save_last_sync_date(yesterday)


if __name__ == '__main__':
    main()
