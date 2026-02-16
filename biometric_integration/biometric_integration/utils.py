import frappe
from frappe.utils import now_datetime
from datetime import datetime, timedelta


DUPLICATE_WINDOW_MINUTES = 15


def process_attendance_records(attendance_data, device_identifier=None):
    """
    Process biometric punch records and create Employee Checkin docs.

    - First punch of the day for an employee → log_type = "IN"
    - Subsequent punches → log_type = "OUT"
    - Duplicate detection: skip if punch is within 15 mins of last checkin

    Args:
        attendance_data: list of {"user_id": str, "timestamp": str} dicts
        device_identifier: optional device name/IP

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

    synced = 0
    errors = 0
    skipped = 0
    error_details = []
    synced_punches = []   # record IDs that were actually created or safely skipped
    failed_punches = []   # record IDs that failed (bridge should retry these)

    for record in attendance_data:
        user_id = str(record.get("user_id", "")).strip()
        timestamp_str = str(record.get("timestamp", "")).strip()
        if not user_id or not timestamp_str:
            continue

        record_id = f"{user_id}_{timestamp_str}"

        try:
            ts = _parse_timestamp(timestamp_str)
            if not ts:
                errors += 1
                error_details.append(f"Invalid timestamp: {timestamp_str}")
                failed_punches.append(record_id)
                continue

            # Find employee by attendance_device_id
            employee = frappe.db.get_value(
                "Employee",
                {"attendance_device_id": user_id},
                ["name", "employee_name"],
                as_dict=True,
            )

            if not employee:
                errors += 1
                error_details.append(
                    f"Employee not found for device ID: {user_id}"
                )
                failed_punches.append(record_id)
                continue

            # Duplicate detection: skip if punch within 15 mins of last checkin
            if _is_duplicate_punch(employee.name, ts):
                skipped += 1
                synced_punches.append(record_id)  # safe to mark as done
                continue

            # Determine log_type: first checkin of the day = IN, rest = OUT
            log_type = _get_log_type(employee.name, ts)

            # Create Employee Checkin
            checkin = frappe.new_doc("Employee Checkin")
            checkin.employee = employee.name
            checkin.employee_name = employee.employee_name
            checkin.time = ts
            checkin.log_type = log_type
            checkin.latitude = 27.7228
            checkin.longitude = 85.3211
            if device_identifier:
                checkin.device_id = device_identifier
            checkin.insert(ignore_permissions=True)
            synced += 1
            synced_punches.append(record_id)

            frappe.logger("biometric").info(
                f"Checkin created: {employee.name} {log_type} at {ts}"
            )

        except Exception as e:
            errors += 1
            error_details.append(f"{user_id}: {str(e)[:100]}")
            failed_punches.append(record_id)
            frappe.log_error(
                title="Biometric Checkin Error",
                message=f"Employee Device ID: {user_id}\nError: {str(e)}",
            )

    # Update Biometric Device record
    if device_identifier:
        _update_device_record(device_identifier, synced)

    frappe.db.commit()

    return {
        "success": True,
        "synced": synced,
        "skipped": skipped,
        "errors": errors,
        "message": f"Synced {synced}, skipped {skipped} duplicates, {errors} errors.",
        "error_details": error_details[:10],
        "synced_punches": synced_punches,
        "failed_punches": failed_punches,
    }


def _parse_timestamp(timestamp_str):
    """Parse timestamp string into datetime object."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(timestamp_str, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(timestamp_str)
    except ValueError:
        return None


def _is_duplicate_punch(employee, punch_time):
    """Check if a punch is within DUPLICATE_WINDOW_MINUTES of the last checkin."""
    window_start = punch_time - timedelta(minutes=DUPLICATE_WINDOW_MINUTES)
    window_end = punch_time + timedelta(minutes=DUPLICATE_WINDOW_MINUTES)

    existing = frappe.db.exists(
        "Employee Checkin",
        {
            "employee": employee,
            "time": ["between", [window_start, window_end]],
        },
    )
    return bool(existing)


def _get_log_type(employee, punch_time):
    """
    Determine log_type for the punch.
    First checkin of the day → IN, any subsequent → OUT.
    """
    punch_date = punch_time.date()
    day_start = datetime.combine(punch_date, datetime.min.time())
    day_end = datetime.combine(punch_date, datetime.max.time())

    existing_today = frappe.db.count(
        "Employee Checkin",
        {
            "employee": employee,
            "time": ["between", [day_start, day_end]],
        },
    )

    return "IN" if existing_today == 0 else "OUT"


def create_attendance_from_checkins(date=None):
    """
    Nightly job (runs at 23:58): create Attendance from Employee Checkins.

    For each employee who has checkins today:
    - First checkin → check-in time
    - Last checkin → check-out time
    - Calculate working hours and status
    - Late entry / early exit determined from Shift Type start_time / end_time
    - Early exit → status = Half Day, leave_type = Leave Without Pay
    - Create/update Attendance record
    """
    from frappe.utils import getdate, today

    target_date = getdate(date) if date else getdate(today())
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date, datetime.max.time())

    employees_with_checkins = frappe.db.sql(
        """
        SELECT DISTINCT employee
        FROM `tabEmployee Checkin`
        WHERE time BETWEEN %s AND %s
        """,
        (day_start, day_end),
        as_dict=True,
    )

    created = 0
    errors = 0

    for row in employees_with_checkins:
        try:
            emp = row.employee

            checkins = frappe.db.sql(
                """
                SELECT time, log_type
                FROM `tabEmployee Checkin`
                WHERE employee = %s AND time BETWEEN %s AND %s
                ORDER BY time ASC
                """,
                (emp, day_start, day_end),
                as_dict=True,
            )

            if not checkins:
                continue

            check_in = checkins[0].time
            check_out = checkins[-1].time if len(checkins) > 1 else None

            # Calculate working hours
            working_hours = 0
            if check_in and check_out:
                working_hours = round(
                    (check_out - check_in).total_seconds() / 3600, 2
                )

            # Get employee details
            employee = frappe.db.get_value(
                "Employee",
                emp,
                ["name", "company", "employee_name", "default_shift"],
                as_dict=True,
            )

            if not employee:
                continue

            # Determine late entry / early exit from Shift Type
            late_entry = False
            early_exit = False
            shift_start = None
            shift_end = None

            if employee.default_shift:
                shift = frappe.db.get_value(
                    "Shift Type",
                    employee.default_shift,
                    ["start_time", "end_time"],
                    as_dict=True,
                )
                if shift:
                    shift_start = shift.start_time
                    shift_end = shift.end_time

                    # shift start_time/end_time are timedelta objects
                    # check_in is datetime — compare using time-of-day
                    checkin_time = timedelta(
                        hours=check_in.hour,
                        minutes=check_in.minute,
                        seconds=check_in.second,
                    )
                    if checkin_time > shift_start:
                        late_entry = True

                    if check_out:
                        checkout_time = timedelta(
                            hours=check_out.hour,
                            minutes=check_out.minute,
                            seconds=check_out.second,
                        )
                        if checkout_time < shift_end:
                            early_exit = True

            # Determine status
            if early_exit:
                status = "Half Day"
                leave_type = "Leave Without Pay"
            else:
                status = "Present"
                leave_type = ""

            # Build attendance fields
            att_data = {
                "employee": employee.name,
                "employee_name": employee.employee_name,
                "attendance_date": target_date,
                "company": employee.company,
                "in_time": check_in,
                "out_time": check_out,
                "working_hours": working_hours,
                "status": status,
                "late_entry": late_entry,
                "early_exit": early_exit,
            }
            if employee.default_shift:
                att_data["shift"] = employee.default_shift
            if leave_type:
                att_data["leave_type"] = leave_type

            # Check existing attendance for this date
            existing = frappe.db.exists(
                "Attendance",
                {
                    "employee": emp,
                    "attendance_date": target_date,
                    "docstatus": ["!=", 2],
                },
            )

            if existing:
                doc = frappe.get_doc("Attendance", existing)
                if doc.docstatus == 1:
                    doc.cancel()
                    att = frappe.new_doc("Attendance")
                    att.update(att_data)
                    att.amended_from = doc.name
                    att.insert(ignore_permissions=True)
                    att.submit()
                else:
                    doc.update(att_data)
                    doc.save(ignore_permissions=True)
                    doc.submit()
            else:
                att = frappe.new_doc("Attendance")
                att.update(att_data)
                att.insert(ignore_permissions=True)
                att.submit()

            created += 1

            frappe.logger("biometric").info(
                f"Attendance: {emp} on {target_date} "
                f"IN={check_in} OUT={check_out} hours={working_hours} "
                f"status={status} late={late_entry} early_exit={early_exit}"
            )

        except Exception as e:
            errors += 1
            frappe.log_error(
                title="Attendance Creation Error",
                message=f"Employee: {row.employee}\nDate: {target_date}\nError: {str(e)}",
            )

    frappe.db.commit()

    frappe.logger("biometric").info(
        f"Nightly attendance: created={created} errors={errors} for {target_date}"
    )

    return {"created": created, "errors": errors}


def _update_device_record(device_identifier, synced_count):
    """Update last_sync_time and total_synced on the Biometric Device record."""
    device_name = frappe.db.get_value(
        "Biometric Device",
        {"device_name": device_identifier},
        "name",
    )
    if not device_name:
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
