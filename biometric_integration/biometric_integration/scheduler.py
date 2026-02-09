import frappe
from biometric_integration.biometric_integration.api import sync_attendance_from_device

def sync_biometric_attendance():
    """
    Scheduled task to sync attendance from all configured devices
    Runs every 30 minutes
    """
    # Get all biometric devices from settings
    devices = frappe.get_all(
        "Biometric Device",
        filters={"enabled": 1},
        fields=["device_ip", "device_port"]
    )
    
    for device in devices:
        try:
            sync_attendance_from_device(
                device.device_ip,
                device.device_port or 4370
            )
        except Exception as e:
            frappe.log_error(
                f"Auto-sync failed for {device.device_ip}: {str(e)}",
                "Biometric Auto-Sync Error"
            )