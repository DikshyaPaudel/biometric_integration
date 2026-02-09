#!/usr/bin/env python3
"""
Biometric Attendance Push Sync Script

Standalone script that runs on a local machine (same LAN as the ZK biometric device).
Fetches attendance logs from the device via pyzk and POSTs them to the ERPNext
receive_attendance API endpoint.

Dependencies: pip install pyzk requests

Usage:
    python3 local_sync.py                          # uses local_sync.conf
    python3 local_sync.py --config /path/to/conf   # custom config path

Cron example (every 30 minutes):
    */30 * * * * /usr/bin/python3 /path/to/local_sync.py >> /var/log/biometric_sync.log 2>&1

Can also be configured via environment variables (override config file values):
    ERPNEXT_URL, API_KEY, API_SECRET, DEVICE_IP, DEVICE_PORT, DEVICE_IDENTIFIER,
    CLEAR_AFTER_SYNC
"""

import argparse
import configparser
import json
import logging
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("biometric_sync")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def load_config(config_path=None):
    """Load configuration from INI file, with environment variable overrides."""
    cfg = {
        "erpnext_url": "",
        "api_key": "",
        "api_secret": "",
        "device_ip": "",
        "device_port": 4370,
        "device_identifier": "",
        "clear_after_sync": False,
    }

    # Read INI config file
    if config_path and os.path.exists(config_path):
        parser = configparser.ConfigParser()
        parser.read(config_path)
        section = "biometric_sync"
        if parser.has_section(section):
            cfg["erpnext_url"] = parser.get(section, "erpnext_url", fallback="")
            cfg["api_key"] = parser.get(section, "api_key", fallback="")
            cfg["api_secret"] = parser.get(section, "api_secret", fallback="")
            cfg["device_ip"] = parser.get(section, "device_ip", fallback="")
            cfg["device_port"] = parser.getint(section, "device_port", fallback=4370)
            cfg["device_identifier"] = parser.get(section, "device_identifier", fallback="")
            cfg["clear_after_sync"] = parser.getboolean(section, "clear_after_sync", fallback=False)
    elif config_path:
        log.warning("Config file not found: %s — using environment variables only", config_path)

    # Environment variable overrides
    cfg["erpnext_url"] = os.environ.get("ERPNEXT_URL", cfg["erpnext_url"]).rstrip("/")
    cfg["api_key"] = os.environ.get("API_KEY", cfg["api_key"])
    cfg["api_secret"] = os.environ.get("API_SECRET", cfg["api_secret"])
    cfg["device_ip"] = os.environ.get("DEVICE_IP", cfg["device_ip"])
    cfg["device_port"] = int(os.environ.get("DEVICE_PORT", cfg["device_port"]))
    cfg["device_identifier"] = os.environ.get("DEVICE_IDENTIFIER", cfg["device_identifier"])
    if os.environ.get("CLEAR_AFTER_SYNC"):
        cfg["clear_after_sync"] = os.environ["CLEAR_AFTER_SYNC"].lower() in ("1", "true", "yes")

    return cfg


def validate_config(cfg):
    """Ensure required configuration values are present."""
    missing = []
    for key in ("erpnext_url", "api_key", "api_secret", "device_ip"):
        if not cfg.get(key):
            missing.append(key)
    if missing:
        log.error("Missing required config: %s", ", ".join(missing))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Device interaction
# ---------------------------------------------------------------------------
def fetch_attendance_from_device(device_ip, device_port=4370):
    """Connect to ZK device, fetch attendance logs, return list of dicts."""
    try:
        from zk import ZK
    except ImportError:
        log.error("pyzk is not installed. Run: pip install pyzk")
        sys.exit(1)

    log.info("Connecting to device at %s:%s ...", device_ip, device_port)
    zk = ZK(device_ip, port=device_port, timeout=5)
    conn = zk.connect()
    conn.disable_device()

    attendance_logs = conn.get_attendance()
    if not attendance_logs:
        log.info("No attendance logs on device")
        conn.enable_device()
        conn.disconnect()
        return [], conn, zk

    records = []
    for entry in attendance_logs:
        records.append({
            "user_id": str(entry.user_id),
            "timestamp": entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        })

    log.info("Fetched %d punch records from device", len(records))

    # Return conn so caller can optionally clear logs and disconnect
    conn.enable_device()
    return records, conn, zk


# ---------------------------------------------------------------------------
# Push to ERPNext
# ---------------------------------------------------------------------------
def push_to_erpnext(cfg, attendance_data):
    """POST attendance data to ERPNext's receive_attendance endpoint."""
    try:
        import requests
    except ImportError:
        log.error("requests is not installed. Run: pip install requests")
        sys.exit(1)

    url = f"{cfg['erpnext_url']}/api/method/biometric_integration.biometric_integration.api.receive_attendance"

    headers = {
        "Authorization": f"token {cfg['api_key']}:{cfg['api_secret']}",
        "Content-Type": "application/json",
    }

    payload = {
        "attendance_data": json.dumps(attendance_data),
    }
    if cfg.get("device_identifier"):
        payload["device_identifier"] = cfg["device_identifier"]

    log.info("Pushing %d records to %s ...", len(attendance_data), cfg["erpnext_url"])
    resp = requests.post(url, json=payload, headers=headers, timeout=60)

    if resp.status_code != 200:
        log.error("ERPNext returned HTTP %d: %s", resp.status_code, resp.text[:500])
        return None

    result = resp.json()
    message = result.get("message", result)
    log.info("ERPNext response: %s", json.dumps(message, indent=2))
    return message


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Push biometric attendance to ERPNext")
    parser.add_argument(
        "--config", "-c",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_sync.conf"),
        help="Path to config file (default: local_sync.conf next to this script)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    validate_config(cfg)

    # Fetch from device
    records, conn, zk = fetch_attendance_from_device(cfg["device_ip"], cfg["device_port"])
    if not records:
        log.info("Nothing to sync")
        return

    # Push to ERPNext
    result = push_to_erpnext(cfg, records)

    if result and result.get("success"):
        log.info("Sync complete: %d synced, %d errors", result.get("synced", 0), result.get("errors", 0))

        # Clear device logs after successful sync if configured
        if cfg["clear_after_sync"] and result.get("synced", 0) > 0:
            try:
                log.info("Clearing device logs ...")
                conn2 = zk.connect()
                conn2.disable_device()
                conn2.clear_attendance()
                conn2.enable_device()
                conn2.disconnect()
                log.info("Device logs cleared")
            except Exception as e:
                log.warning("Failed to clear device logs: %s", e)
    else:
        log.error("Sync failed or returned unexpected result")
        sys.exit(1)


if __name__ == "__main__":
    main()
