import frappe
from frappe.utils import now_datetime
from datetime import datetime


def process_attendance_records(attendance_data, device_identifier=None):
    """
    Process attendance records and create/update Attendance docs in ERPNext.

    Args:
        attendance_data: list of {"user_id": str, "timestamp": str} dicts
        device_identifier: optional device name/IP to update Biometric Device record

    Returns:
        dict with success, synced, errors, message, error_details
    """
    if not attendance_data:
        return {
            "success": True,
            "synced": 0,
            "errors": 0,
            "message": "No attendance records to process",
        }

    # Group by employee_id + date
    grouped = {}
    for record in attendance_data:
        user_id = str(record.get("user_id", ""))
        timestamp_str = str(record.get("timestamp", ""))
        if not user_id or not timestamp_str:
            continue

        try:
            ts = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                ts = datetime.fromisoformat(timestamp_str)
            except ValueError:
                continue

        att_date = ts.date()
        key = f"{user_id}_{att_date}"
        if key not in grouped:
            grouped[key] = {
                "employee_id": user_id,
                "date": att_date,
                "punches": [],
            }
        grouped[key]["punches"].append(ts)

    synced = 0
    errors = 0
    error_details = []

    for key, data in grouped.items():
        try:
            employee = frappe.db.get_value(
                "Employee",
                {"attendance_device_id": data["employee_id"]},
                ["name", "company", "employee_name", "default_shift"],
                as_dict=True,
            )

            if not employee:
                errors += 1
                error_details.append(
                    f"Employee not found for device ID: {data['employee_id']}"
                )
                continue

            punches = sorted(data["punches"])
            check_in = punches[0]
            check_out = punches[-1] if len(punches) > 1 else None

            # Calculate working hours
            working_hours = 0
            if check_in and check_out:
                working_hours = round(
                    (check_out - check_in).total_seconds() / 3600, 2
                )

            # Determine status
            if check_out:
                if working_hours < 4:
                    status = "Absent"
                elif working_hours < 6:
                    status = "Half Day"
                else:
                    status = "Present"
            else:
                status = "Present"

            # Check existing attendance
            existing = frappe.db.exists(
                "Attendance",
                {
                    "employee": employee.name,
                    "attendance_date": data["date"],
                    "docstatus": ["!=", 2],
                },
            )

            if existing:
                doc = frappe.get_doc("Attendance", existing)
                if doc.docstatus == 1:
                    doc.cancel()
                    # Create a new doc after cancelling submitted one
                    att = frappe.new_doc("Attendance")
                    att.employee = employee.name
                    att.employee_name = employee.employee_name
                    att.attendance_date = data["date"]
                    att.company = employee.company
                    att.in_time = check_in
                    att.out_time = check_out
                    att.working_hours = working_hours
                    att.status = status
                    if employee.default_shift:
                        att.shift = employee.default_shift
                    if frappe.db.has_column("Attendance", "device_id"):
                        att.device_id = device_identifier or ""
                    if frappe.db.has_column("Attendance", "total_punches"):
                        att.total_punches = len(punches)
                    att.amended_from = doc.name
                    att.insert(ignore_permissions=True)
                    att.submit()
                    synced += 1
                    continue
                else:
                    # Draft — update in place
                    doc.in_time = check_in
                    doc.out_time = check_out
                    doc.working_hours = working_hours
                    doc.status = status
                    doc.save(ignore_permissions=True)
                    doc.submit()
                    synced += 1
                    continue

            # Create new attendance
            att = frappe.new_doc("Attendance")
            att.employee = employee.name
            att.employee_name = employee.employee_name
            att.attendance_date = data["date"]
            att.company = employee.company
            att.in_time = check_in
            att.out_time = check_out
            att.working_hours = working_hours
            att.status = status

            if employee.default_shift:
                att.shift = employee.default_shift
            if frappe.db.has_column("Attendance", "device_id"):
                att.device_id = device_identifier or ""
            if frappe.db.has_column("Attendance", "total_punches"):
                att.total_punches = len(punches)

            att.insert(ignore_permissions=True)
            att.submit()
            synced += 1

        except Exception as e:
            errors += 1
            error_details.append(f"{data['employee_id']}: {str(e)[:100]}")
            frappe.log_error(
                title="Attendance Sync Error",
                message=f"Employee ID: {data['employee_id']}\nError: {str(e)}",
            )

    # Update Biometric Device record if identifier provided
    if device_identifier:
        _update_device_record(device_identifier, synced)

    frappe.db.commit()

    return {
        "success": True,
        "synced": synced,
        "errors": errors,
        "message": f"Synced {synced} records. {errors} errors.",
        "error_details": error_details[:10],
    }


def _update_device_record(device_identifier, synced_count):
    """Update last_sync_time and total_synced on the Biometric Device record."""
    device_name = frappe.db.get_value(
        "Biometric Device",
        {"device_name": device_identifier},
        "name",
    )
    if not device_name:
        # Try matching by IP
        device_name = frappe.db.get_value(
            "Biometric Device",
            {"device_ip": device_identifier},
            "name",
        )
    if device_name:
        frappe.db.set_value(
            "Biometric Device",
            device_name,
            {
                "last_sync_time": now_datetime(),
                "total_synced": (
                    frappe.db.get_value("Biometric Device", device_name, "total_synced")
                    or 0
                )
                + synced_count,
            },
        )
