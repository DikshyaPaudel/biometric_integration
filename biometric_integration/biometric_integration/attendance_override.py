import frappe
from datetime import datetime, timedelta
from frappe.utils import getdate, get_datetime


def cap_working_hours_to_shift_end(doc, method):
    """
    Before Attendance is saved, apply two rules based on OUT time vs shift end time:

    1. OUT after shift end  → cap out_time to shift end, recalculate working_hours
    2. OUT before shift end → keep working_hours as-is (actual out - in),
                              set status = Half Day, leave_type = Leave Without Pay

    IN time always stays as the actual checkin time.
    Skips if already On Leave / no shift / no in_time / no out_time.
    """
    # DEBUG: confirm hook is now firing on before_submit
    frappe.log_error(
        f"cap_working_hours before_submit CALLED\n"
        f"  doc={doc.name} status={doc.status} docstatus={doc.docstatus}\n"
        f"  shift={doc.shift} in_time={doc.in_time} out_time={doc.out_time}\n"
        f"  working_hours={doc.working_hours}",
        "DBG cap_working_hours"
    )

    if not doc.out_time or not doc.shift or not doc.in_time:
        return

    # Don't interfere with leave-based attendance
    if doc.status in ("On Leave", "Absent"):
        return

    try:
        shift = frappe.get_cached_doc("Shift Type", doc.shift)

        # shift.end_time / start_time are timedelta (seconds from midnight)
        attendance_date = getdate(doc.attendance_date)
        shift_end_dt = datetime.combine(attendance_date, datetime.min.time()) + shift.end_time

        # Overnight shift: end_time < start_time means end falls on the next day
        if shift.end_time < shift.start_time:
            shift_end_dt += timedelta(days=1)

        out_time = get_datetime(doc.out_time)

        frappe.log_error(
            f"cap_working_hours_to_shift_end COMPARE\n"
            f"  out_time={out_time}  shift_end_dt={shift_end_dt}\n"
            f"  out > shift_end: {out_time > shift_end_dt}",
            "DBG cap_working_hours"
        )

        if out_time > shift_end_dt:
            # --- Rule 1: Late exit — cap to shift end ---
            in_time = get_datetime(doc.in_time)
            new_wh = round(float((shift_end_dt - in_time).total_seconds()) / 3600, 2)
            frappe.log_error(
                f"cap_working_hours_to_shift_end CAPPING\n"
                f"  old out_time={doc.out_time} → new={shift_end_dt}\n"
                f"  old working_hours={doc.working_hours} → new={new_wh}",
                "DBG cap_working_hours"
            )
            doc.out_time = shift_end_dt
            doc.working_hours = new_wh

        elif out_time < shift_end_dt:
            # --- Rule 2: Early exit — Half Day + LWP ---
            doc.status = "Half Day"
            doc.leave_type = "Leave Without Pay"
            # working_hours stays as calculated by ERPNext (actual out - in)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Error in attendance override for Attendance {doc.name}"
        )


def create_compensatory_leave_on_holiday(doc, method):
    """
    On Attendance submit: if the employee punched IN and OUT on a holiday,
    auto-create and submit a Compensatory Leave Request so a leave day is allocated.
    """
    frappe.log_error(
        f"create_compensatory_leave_on_holiday CALLED\n"
        f"  doc={doc.name} status={doc.status} docstatus={doc.docstatus}\n"
        f"  shift={doc.shift} in_time={doc.in_time} out_time={doc.out_time}\n"
        f"  working_hours={doc.working_hours}",
        "Compendatory Leave DBG"
    )
    # Only process if employee actually worked (both checkins present)
    if not doc.in_time or not doc.out_time:
        return

    try:
        # Get holiday list for the employee (Employee → Company → Global Defaults)
        from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
        holiday_list = get_holiday_list_for_employee(doc.employee, raise_exception=False)
        if not holiday_list:
            return

        # Check if attendance_date is a holiday
        is_holiday = frappe.db.exists(
            "Holiday",
            {"parent": holiday_list, "holiday_date": doc.attendance_date}
        )
        if not is_holiday:
            return

        # Skip if a Compensatory Leave Request already exists for this employee + date
        already_exists = frappe.db.exists(
            "Compensatory Leave Request",
            {
                "employee": doc.employee,
                "work_from_date": doc.attendance_date,
                "work_end_date": doc.attendance_date,
                "docstatus": ["!=", 2],
            },
        )
        if already_exists:
            return

        # Find the compensatory leave type
        leave_type = frappe.db.get_value("Leave Type", {"is_compensatory": 1}, "name")
        if not leave_type:
            frappe.log_error(
                f"No Leave Type with 'Is Compensatory' found. "
                f"Cannot create compensatory leave for {doc.employee} on {doc.attendance_date}.",
                "Compensatory Leave Setup Missing"
            )
            return

        # Create and submit Compensatory Leave Request
        comp_leave = frappe.new_doc("Compensatory Leave Request")
        comp_leave.employee = doc.employee
        comp_leave.work_from_date = doc.attendance_date
        comp_leave.work_end_date = doc.attendance_date
        comp_leave.reason = f"Worked on holiday — auto-created from biometric punch"
        comp_leave.leave_type = leave_type
        comp_leave.insert(ignore_permissions=True)
        comp_leave.submit()

        frappe.logger("biometric").info(
            f"Compensatory Leave Request {comp_leave.name} created for "
            f"{doc.employee} on holiday {doc.attendance_date}"
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Error creating compensatory leave for Attendance {doc.name}"
        )
