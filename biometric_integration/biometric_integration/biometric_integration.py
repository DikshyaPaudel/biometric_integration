import json
import frappe


@frappe.whitelist(allow_guest=True)
def push_bulk_attendance(**kwargs):
    """
    Bulk API endpoint for K40 bridge.
    Called via POST /api/method/biometric_integration.biometric_integration.biometric_integration.push_bulk_attendance

    Expects JSON body:
        {
            "device_id": "A6F5215360564",
            "punches": "[{\"employee_id\": \"1\", \"punch_time\": \"2026-02-12 08:30:00\"}, ...]"
        }

    Server-side logic (in utils.py):
        - First punch of day → Employee Checkin with log_type = IN
        - Subsequent punches → Employee Checkin with log_type = OUT
        - Duplicates within 15 mins → skipped
    """
    from biometric_integration.biometric_integration.utils import process_attendance_records

    device_id = kwargs.get("device_id", "")
    punches_raw = kwargs.get("punches", "[]")

    try:
        punches = json.loads(punches_raw) if isinstance(punches_raw, str) else punches_raw
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "message": "Invalid punches JSON"}

    if not punches:
        return {"success": True, "synced": 0, "skipped": 0, "errors": 0, "message": "No punches received"}

    # Convert to format expected by process_attendance_records
    records = []
    for p in punches:
        employee_id = p.get("employee_id", "")
        punch_time = p.get("punch_time", "")
        if employee_id and punch_time:
            records.append({"user_id": employee_id, "timestamp": punch_time})

    result = process_attendance_records(records, device_identifier=device_id)
    return result
