#!/usr/bin/env python3
"""
K40 to ERPNext Bridge
Pulls attendance from K40 device and pushes to ERPNext
"""
#bench --site your-site-name execute biometric_integration.biometric_integration.utils.create_attendance_from_checkins --kwargs "{'date': '2026-02-11'}"                                     

import sys
import time
import logging
import json
import os
from datetime import datetime, date
from zk import ZK
import requests

# ============================================
# CONFIGURATION
# ============================================
K40_IP = '192.168.18.200'
K40_PORT = 4370
K40_SERIAL = 'A6F5215360564'

ERPNEXT_URL = 'https://demo-sb.raindropinc.com'
WEBHOOK_PATH = '/api/method/biometric_integration.biometric_integration.biometric_integration.zkteco_push_attendance'

SYNC_INTERVAL = 120  # seconds (5 minutes)

# Log file on Desktop
LOG_FILE = '/home/raindrop/Desktop/k40_bridge.log'

# Persistent synced record storage
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
logger.info(f"Logging started. Log file path: {LOG_FILE}")

# ============================================
# LOAD PERSISTENT SYNCED RECORDS
# ============================================
if os.path.exists(SYNCED_RECORDS_FILE):
    try:
        with open(SYNCED_RECORDS_FILE, 'r') as f:
            synced_records = set(json.load(f))
        logger.info(f"Loaded {len(synced_records)} previously synced records")
    except Exception as e:
        logger.error(f"Error loading synced records: {e}")
        synced_records = set()
else:
    synced_records = set()

# ============================================
# FUNCTIONS
# ============================================

def save_synced_records():
    """Save synced records to file"""
    try:
        with open(SYNCED_RECORDS_FILE, 'w') as f:
            json.dump(list(synced_records), f)
    except Exception as e:
        logger.error(f"Error saving synced records: {e}")

def get_attendance_from_k40():
    """Connect to K40 and retrieve attendance records"""
    try:
        logger.info(f"Connecting to K40 at {K40_IP}:{K40_PORT}")
        conn = ZK(K40_IP, port=K40_PORT, timeout=5)
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
    """Send single attendance record to ERPNext with full logging.
    Returns: 'skipped' if already synced, 'synced' if newly synced, 'error' if failed.
    """
    try:
        record_id = f"{attendance.user_id}_{attendance.timestamp}"

        if record_id in synced_records:
            return 'skipped'

        data = {
            'device_id': K40_SERIAL,
            'employee_id': str(attendance.user_id),
            'punch_time': attendance.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'punch_type': 'IN'  # Adjust if needed
        }

        url = f"{ERPNEXT_URL}{WEBHOOK_PATH}"
        response = requests.post(
            url,
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )

        # Log ERPNext response
        logger.info(f"ERPNext Response for {record_id}: {response.status_code} - {response.text[:500]}")

        if response.status_code != 200:
            logger.warning(f"⚠️ Failed to sync Employee {attendance.user_id}: HTTP {response.status_code}")
            return 'error'

        # Check response body for application-level errors
        try:
            resp_json = response.json()
            message = resp_json.get('message', {})
            if isinstance(message, dict) and message.get('status') == 'error':
                error_msg = message.get('message', 'Unknown error')
                # Duplicate checkin means record already exists — treat as synced
                if 'already has a log with the same timestamp' in error_msg:
                    logger.info(f"Already exists: Employee {attendance.user_id} at {attendance.timestamp}")
                    synced_records.add(record_id)
                    save_synced_records()
                    return 'skipped'
                logger.error(f" ERPNext error for Employee {attendance.user_id}: {error_msg}")
                return 'error'
        except (ValueError, AttributeError):
            pass

        synced_records.add(record_id)
        save_synced_records()
        logger.info(f"✅ Synced: Employee {attendance.user_id} at {attendance.timestamp}")
        return 'synced'

    except Exception as e:
        logger.error(f"❌ Error sending Employee {attendance.user_id} to ERPNext: {e}")
        return 'error'

def sync_cycle():
    """One complete sync cycle"""
    logger.info("="*50)
    logger.info("Starting sync cycle...")

    # Get attendance from K40
    attendances = get_attendance_from_k40()

    # today_date = date.today()
    # today_date = date(2026, 2, 15)
    if len(sys.argv) > 1:
        today_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        today_date = date.today()

    # Only today
    attendances = [att for att in attendances if att.timestamp.date() == today_date]

    if not attendances:
        logger.info("No records to sync")
        return

    synced_count = 0
    skipped_count = 0
    error_count = 0

    for att in attendances:
        result = send_to_erpnext(att)
        if result == 'synced':
            synced_count += 1
        elif result == 'skipped':
            skipped_count += 1
        else:
            error_count += 1

    logger.info(f"Sync complete: {synced_count} new, {skipped_count} skipped, {error_count} errors")
    logger.info("="*50)

def main():
    """Main loop"""
    logger.info("🚀 K40 Bridge Started!")
    logger.info(f"K40 Device: {K40_IP}:{K40_PORT}")
    logger.info(f"ERPNext URL: {ERPNEXT_URL}")
    logger.info(f"Sync Interval: {SYNC_INTERVAL} seconds")
    logger.info(f"Persistent Synced Records File: {SYNCED_RECORDS_FILE}")
    logger.info("="*50)

    # Initial sync
    sync_cycle()

    # Continuous sync
    while True:
        try:
            time.sleep(SYNC_INTERVAL)
            sync_cycle()
        except KeyboardInterrupt:
            logger.info("Stopping bridge...")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(60)

if __name__ == '__main__':
    main()
