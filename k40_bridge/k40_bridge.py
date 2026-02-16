#!/usr/bin/env python3
"""
K40 to ERPNext Bridge (Windows EXE version)
Run: k40_bridge.exe              -> syncs today
Run: k40_bridge.exe 2026-02-13   -> syncs specific date
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

# Log file — same folder as the exe
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


def get_attendance_from_k40():
    """Connect to K40 and retrieve attendance records"""
    try:
        logger.info(f"Connecting to K40 at {K40_IP}")
        conn = ZK(K40_IP, timeout=5)
        zk = conn.connect()

        zk.disable_device()
        attendances = zk.get_attendance()
        zk.enable_device()
        zk.disconnect()

        logger.info(f"Retrieved {len(attendances)} total records from K40")
        return attendances

    except Exception as e:
        logger.error(f"Error connecting to K40: {e}")
        return []


def send_to_erpnext(attendance):
    """Send single attendance record to ERPNext."""
    try:
        data = {
            'device_id': K40_SERIAL,
            'employee_id': str(attendance.user_id),
            'punch_time': attendance.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'punch_type': 'IN'
        }

        url = f"{ERPNEXT_URL}{WEBHOOK_PATH}"
        response = requests.post(url, json=data, headers={'Content-Type': 'application/json'}, timeout=10)

        if response.status_code != 200:
            logger.warning(f"Failed Employee {attendance.user_id}: HTTP {response.status_code}")
            return 'error'

        logger.info(f"Synced: Employee {attendance.user_id} at {attendance.timestamp}")
        return 'synced'

    except Exception as e:
        logger.error(f"Error sending Employee {attendance.user_id}: {e}")
        return 'error'


def main():
    if len(sys.argv) > 1:
        target_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        target_date = date.today()

    logger.info(f"K40 Bridge - syncing for {target_date}")

    attendances = get_attendance_from_k40()
    attendances = [att for att in attendances if att.timestamp.date() == target_date]

    if not attendances:
        logger.info("No records for this date")
        input("Press Enter to exit...")
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
    input("Press Enter to exit...")


if __name__ == '__main__':
    main()
