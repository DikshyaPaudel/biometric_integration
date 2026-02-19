#!/usr/bin/env python3
"""
K40 to ERPNext Bridge
Pulls attendance from K40 device and pushes to ERPNext.
Run: python3 k40_bridge.py              -> syncs today
Run: python3 k40_bridge.py 2026-02-13   -> syncs specific date
"""

import sys
import logging
import os
from datetime import datetime, date
from zk import ZK
import requests

# ============================================
# CONFIGURATION
# ============================================
K40_IP = '192.168.18.200'
K40_SERIAL = 'A6F5215360564'

ERPNEXT_URL = 'https://demo-sb.raindropinc.com'
WEBHOOK_PATH = '/api/method/biometric_integration.biometric_integration.biometric_integration.zkteco_push_attendance'

# Log file — same folder as the script/exe
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "k40_bridge.log")

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
    if len(sys.argv) > 1:
        target_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        target_date = date.today()

    logger.info("K40 Bridge Started!")
    logger.info(f"K40 Device: {K40_IP}")
    logger.info(f"ERPNext URL: {ERPNEXT_URL}")
    logger.info(f"Target date: {target_date}")
    logger.info("="*50)

    # Get attendance from K40
    attendances = get_attendance_from_k40()

    # Filter to target date only
    attendances = [att for att in attendances if att.timestamp.date() == target_date]

    if not attendances:
        logger.info("No records for this date")
        return
        

    logger.info(f"Found {len(attendances)} records for {target_date}")

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


if __name__ == '__main__':
    main()
