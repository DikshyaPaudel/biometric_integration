import frappe
from frappe.utils import now_datetime
from datetime import datetime, timedelta
from collections import defaultdict


def process_attendance_records(attendance_data, device_identifier=None):
    """
    Process biometric punch records: group by (user_id, date), then create/update
    exactly one IN checkin, one OUT checkin, and one draft Attendance per employee per day.

    Args:
        attendance_data: list of {"user_id": str, "timestamp": str} dicts
        device_identifier: optional device name/IP

    Returns:
        dict with success, synced, errors, message, error_details, synced_punches, failed_punches
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
    error_details = []
    synced_punches = []
    failed_punches = []

    # Parse all punches and group by (user_id, date)
    grouped = defaultdict(list)
    record_ids_by_group = defaultdict(list)

    for record in attendance_data:
        user_id = str(record.get("user_id", "")).strip()
        timestamp_str = str(record.get("timestamp", "")).strip()
        if not user_id or not timestamp_str:
            continue

        record_id = f"{user_id}_{timestamp_str}"

        ts = _parse_timestamp(timestamp_str)
        if not ts:
            errors += 1
            error_details.append(f"Invalid timestamp: {timestamp_str}")
            failed_punches.append(record_id)
            continue

        key = (user_id, ts.date())
        grouped[key].append(ts)
        record_ids_by_group[key].append(record_id)

    # Process each (user_id, date) group
    for (user_id, punch_date), timestamps in grouped.items():
        group_record_ids = record_ids_by_group[(user_id, punch_date)]

        try:
            employee = frappe.db.get_value(
                "Employee",
                {"attendance_device_id": user_id},
                ["name", "employee_name", "company", "default_shift"],
                as_dict=True,
            )

            if not employee:
                errors += 1
                error_details.append(f"Employee not found for device ID: {user_id}")
                failed_punches.extend(group_record_ids)
                continue

            timestamps.sort()
            last_punch = timestamps[-1]

            # Check if IN already exists in DB for this employee+date
            day_start = datetime.combine(punch_date, datetime.min.time())
            day_end = datetime.combine(punch_date, datetime.max.time())
            existing_in = frappe.db.get_value(
                "Employee Checkin",
                {
                    "employee": employee.name,
                    "log_type": "IN",
                    "time": ["between", [day_start, day_end]],
                },
                ["name", "time"],
                as_dict=True,
            )

            if existing_in:
                # IN exists — this punch updates OUT
                _upsert_checkin_out(employee, punch_date, last_punch, device_identifier)
                _upsert_attendance(employee, punch_date, existing_in.time, last_punch)
            else:
                # No IN — first punch is IN
                _upsert_checkin_in(employee, punch_date, timestamps[0], device_identifier)
                _upsert_attendance(employee, punch_date, timestamps[0], None)

            synced += len(timestamps)
            synced_punches.extend(group_record_ids)

            frappe.logger("biometric").info(
                f"Processed {employee.name} on {punch_date}: "
                f"IN={existing_in.time if existing_in else timestamps[0]}, "
                f"OUT={last_punch if existing_in else 'N/A'}, "
                f"punches={len(timestamps)}"
            )

        except Exception as e:
            errors += 1
            error_details.append(f"{user_id}: {str(e)[:100]}")
            failed_punches.extend(group_record_ids)
            frappe.log_error(
                title="Biometric Processing Error",
                message=f"Employee Device ID: {user_id}\nDate: {punch_date}\nError: {str(e)}",
            )

    if device_identifier:
        _update_device_record(device_identifier, synced)

    frappe.db.commit()

    return {
        "success": True,
        "synced": synced,
        "errors": errors,
        "message": f"Synced {synced} punches, {errors} errors.",
        "error_details": error_details[:10],
        "synced_punches": synced_punches,
        "failed_punches": failed_punches,
    }


def _upsert_checkin_in(employee, punch_date, in_time, device_identifier=None):
    """Create IN checkin if none exists for this employee+date."""
    day_start = datetime.combine(punch_date, datetime.min.time())
    day_end = datetime.combine(punch_date, datetime.max.time())

    existing = frappe.db.exists(
        "Employee Checkin",
        {
            "employee": employee.name,
            "log_type": "IN",
            "time": ["between", [day_start, day_end]],
        },
    )

    if existing:
        return

    checkin = frappe.new_doc("Employee Checkin")
    checkin.employee = employee.name
    checkin.employee_name = employee.employee_name
    checkin.time = in_time
    checkin.log_type = "IN"
    checkin.skip_auto_attendance = 1
    checkin.latitude = 27.7228
    checkin.longitude = 85.3211
    if device_identifier:
        checkin.device_id = device_identifier
    checkin.insert(ignore_permissions=True)


def _upsert_checkin_out(employee, punch_date, out_time, device_identifier=None):
    """Create OUT checkin if none exists, or update time if new punch is later."""
    day_start = datetime.combine(punch_date, datetime.min.time())
    day_end = datetime.combine(punch_date, datetime.max.time())

    existing = frappe.db.get_value(
        "Employee Checkin",
        {
            "employee": employee.name,
            "log_type": "OUT",
            "time": ["between", [day_start, day_end]],
        },
        ["name", "time"],
        as_dict=True,
    )

    if existing:
        if out_time > existing.time:
            frappe.db.set_value("Employee Checkin", existing.name, "time", out_time)
        return

    checkin = frappe.new_doc("Employee Checkin")
    checkin.employee = employee.name
    checkin.employee_name = employee.employee_name
    checkin.time = out_time
    checkin.log_type = "OUT"
    checkin.skip_auto_attendance = 1
    checkin.latitude = 27.7228
    checkin.longitude = 85.3211
    if device_identifier:
        checkin.device_id = device_identifier
    checkin.insert(ignore_permissions=True)


def _upsert_attendance(employee, punch_date, in_time, out_time=None):
    """
    Create or update a draft Attendance for the employee on punch_date.
    If a submitted (docstatus=1) Attendance exists, skip.
    If a draft (docstatus=0) exists, update it.
    Otherwise create a new draft.
    """
    # Check for submitted attendance — do not touch it
    submitted = frappe.db.exists(
        "Attendance",
        {
            "employee": employee.name,
            "attendance_date": punch_date,
            "docstatus": 1,
        },
    )
    if submitted:
        return

    # Calculate working hours
    working_hours = 0
    if in_time and out_time:
        working_hours = round((out_time - in_time).total_seconds() / 3600, 2)

    # Calculate late entry / early exit
    late_entry, early_exit = _calculate_late_early(employee, in_time, out_time)

    if early_exit:
        status = "Half Day"
        leave_type = "Leave Without Pay"
    else:
        status = "Present"
        leave_type = ""

    att_data = {
        "employee": employee.name,
        "employee_name": employee.employee_name,
        "attendance_date": punch_date,
        "company": employee.company,
        "in_time": in_time,
        "out_time": out_time,
        "working_hours": working_hours,
        "status": status,
        "late_entry": late_entry,
        "early_exit": early_exit,
    }
    if employee.default_shift:
        att_data["shift"] = employee.default_shift
    if leave_type:
        att_data["leave_type"] = leave_type

    # Check for existing draft
    draft = frappe.db.exists(
        "Attendance",
        {
            "employee": employee.name,
            "attendance_date": punch_date,
            "docstatus": 0,
        },
    )

    if draft:
        doc = frappe.get_doc("Attendance", draft)
        doc.update(att_data)
        doc.save(ignore_permissions=True)
    else:
        att = frappe.new_doc("Attendance")
        att.update(att_data)
        att.insert(ignore_permissions=True)


def _calculate_late_early(employee, in_time, out_time=None):
    """
    Determine late_entry and early_exit using the employee's Shift Type
    grace periods.

    Returns:
        (late_entry: bool, early_exit: bool)
    """
    late_entry = False
    early_exit = False

    if not employee.default_shift:
        return late_entry, early_exit

    shift = frappe.db.get_value(
        "Shift Type",
        employee.default_shift,
        [
            "start_time",
            "end_time",
            "enable_late_entry_marking",
            "late_entry_grace_period",
            "enable_early_exit_marking",
            "early_exit_grace_period",
        ],
        as_dict=True,
    )

    if not shift:
        return late_entry, early_exit

    # shift.start_time and shift.end_time are timedelta objects
    # Convert in_time / out_time to timedelta for comparison
    in_td = timedelta(hours=in_time.hour, minutes=in_time.minute, seconds=in_time.second)

    if shift.enable_late_entry_marking:
        grace = timedelta(minutes=shift.late_entry_grace_period or 0)
        if in_td > shift.start_time + grace:
            late_entry = True

    if out_time and shift.enable_early_exit_marking:
        out_td = timedelta(hours=out_time.hour, minutes=out_time.minute, seconds=out_time.second)
        grace = timedelta(minutes=shift.early_exit_grace_period or 0)
        if out_td < shift.end_time - grace:
            early_exit = True

    return late_entry, early_exit


def submit_draft_attendance(date=None):
    """
    Nightly job: submit all draft Attendance records for the given date (default today).
    """
    from frappe.utils import getdate, today

    target_date = getdate(date) if date else getdate(today())

    drafts = frappe.get_all(
        "Attendance",
        filters={
            "attendance_date": target_date,
            "docstatus": 0,
        },
        pluck="name",
    )

    submitted = 0
    errors = 0

    for att_name in drafts:
        try:
            doc = frappe.get_doc("Attendance", att_name)
            doc.submit()
            submitted += 1
        except Exception as e:
            errors += 1
            frappe.log_error(
                title="Attendance Submit Error",
                message=f"Attendance: {att_name}\nDate: {target_date}\nError: {str(e)}",
            )

    frappe.db.commit()

    frappe.logger("biometric").info(
        f"Submit drafts: submitted={submitted} errors={errors} for {target_date}"
    )

    return {"submitted": submitted, "errors": errors}


def mark_absent_employees(date=None):
    """
    Nightly job: for all Active employees without any Attendance for the date,
    create and submit an Absent attendance record.
    """
    from frappe.utils import getdate, today

    target_date = getdate(date) if date else getdate(today())

    # Get all active employees
    all_active = frappe.get_all(
        "Employee",
        filters={"status": "Active"},
        fields=["name", "employee_name", "company", "default_shift"],
    )

    # Get employees who already have attendance for the date
    employees_with_attendance = frappe.get_all(
        "Attendance",
        filters={
            "attendance_date": target_date,
            "docstatus": ["!=", 2],
        },
        pluck="employee",
    )
    employees_with_attendance = set(employees_with_attendance)

    marked = 0
    errors = 0

    for emp in all_active:
        if emp.name in employees_with_attendance:
            continue

        try:
            att = frappe.new_doc("Attendance")
            att.employee = emp.name
            att.employee_name = emp.employee_name
            att.attendance_date = target_date
            att.company = emp.company
            att.status = "Absent"
            if emp.default_shift:
                att.shift = emp.default_shift
            att.insert(ignore_permissions=True)
            att.submit()
            marked += 1
        except Exception as e:
            errors += 1
            frappe.log_error(
                title="Mark Absent Error",
                message=f"Employee: {emp.name}\nDate: {target_date}\nError: {str(e)}",
            )

    frappe.db.commit()

    frappe.logger("biometric").info(
        f"Mark absent: marked={marked} errors={errors} for {target_date}"
    )

    return {"marked": marked, "errors": errors}



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
