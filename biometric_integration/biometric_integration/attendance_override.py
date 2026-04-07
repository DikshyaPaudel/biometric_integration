import frappe
from datetime import datetime, timedelta
from frappe.utils import getdate, get_datetime, get_time, cint


def _round_to_minute(seconds):
    """
    Round seconds to the nearest whole minute.
    >= 30 seconds → round up   (e.g. 30m 40s → 31m = 1860s)
    <  30 seconds → round down (e.g. 30m 20s → 30m = 1800s)
    """
    return int((int(seconds) + 30) / 60) * 60


def set_shift_deviation_fields(doc, method):
    """
    On Attendance before_save: calculate and store the deviation between
    actual in/out times and the shift start/end times.

    - custom_late_entry  : how late the employee punched IN after shift start
    - custom_early_entry : how early the employee punched IN before shift start
    - custom_early_exit  : how early the employee punched OUT before shift end
    - custom_late_exit   : how late the employee punched OUT after shift end

    Only one of each pair will be non-zero at a time.
    Values are rounded to the nearest minute (>=30s rounds up, <30s rounds down).
    Duration fields store seconds as integers.
    """
    doc.custom_late_entry = 0
    doc.custom_early_entry = 0
    doc.custom_early_exit = 0
    doc.custom_late_exit = 0

    if not doc.shift:
        return

    try:
        shift = frappe.get_cached_doc("Shift Type", doc.shift)
        attendance_date = getdate(doc.attendance_date)

        shift_start_dt = datetime.combine(attendance_date, datetime.min.time()) + shift.start_time
        shift_end_dt = datetime.combine(attendance_date, datetime.min.time()) + shift.end_time

        # Overnight shift: end_time < start_time means end falls on next day
        if shift.end_time < shift.start_time:
            shift_end_dt += timedelta(days=1)

        if doc.in_time:
            in_time = get_datetime(doc.in_time)
            diff = (in_time - shift_start_dt).total_seconds()
            if diff > 0:
                doc.custom_late_entry = _round_to_minute(diff) // 60
            elif diff < 0:
                doc.custom_early_entry = _round_to_minute(abs(diff)) // 60

        if doc.out_time:
            out_time = get_datetime(doc.out_time)
            diff = (out_time - shift_end_dt).total_seconds()
            if diff > 0:
                doc.custom_late_exit = _round_to_minute(diff) // 60
            elif diff < 0:
                doc.custom_early_exit = _round_to_minute(abs(diff)) // 60

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Error calculating shift deviation for Attendance {doc.name}"
        )


def cap_working_hours_to_shift_end(doc, method):
    """
    On Attendance before_submit: apply rules based on OUT time vs shift end time:

    1. OUT after shift end  → cap working_hours to (shift_end - in_time), keep actual out_time
    2. early_exit flagged by ERPNext → keep working_hours as-is (actual out - in)

    Skips if already On Leave / no shift / no in_time / no out_time.
    """
    if not doc.out_time or not doc.shift or not doc.in_time:
        return

    if doc.status in ("On Leave", "Absent"):
        return

    try:
        shift = frappe.get_cached_doc("Shift Type", doc.shift)
        attendance_date = getdate(doc.attendance_date)
        shift_end_dt = datetime.combine(attendance_date, datetime.min.time()) + shift.end_time

        if shift.end_time < shift.start_time:
            shift_end_dt += timedelta(days=1)

        out_time = get_datetime(doc.out_time)

        if out_time > shift_end_dt:
            in_time = get_datetime(doc.in_time)
            doc.working_hours = round(
                float((shift_end_dt - in_time).total_seconds()) / 3600, 2
            )

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
    if not doc.in_time or not doc.out_time:
        return

    try:
        from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
        holiday_list = get_holiday_list_for_employee(doc.employee, raise_exception=False)
        if not holiday_list:
            return

        is_holiday = frappe.db.exists(
            "Holiday",
            {"parent": holiday_list, "holiday_date": doc.attendance_date}
        )
        if not is_holiday:
            return

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

        leave_type = frappe.db.get_value("Leave Type", {"is_compensatory": 1}, "name")
        if not leave_type:
            frappe.log_error(
                f"No Leave Type with 'Is Compensatory' found. "
                f"Cannot create compensatory leave for {doc.employee} on {doc.attendance_date}.",
                "Compensatory Leave Setup Missing"
            )
            return

        comp_leave = frappe.new_doc("Compensatory Leave Request")
        comp_leave.employee = doc.employee
        comp_leave.work_from_date = doc.attendance_date
        comp_leave.work_end_date = doc.attendance_date
        comp_leave.reason = "Worked on holiday — auto-created from biometric punch"
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


def create_leave_application_on_holiday(doc, method):
    """
    On Attendance submit: if the employee worked on a holiday AND the shift has
    custom_compensatory_leave_offset_days set, auto-create and submit a Leave
    Application for (attendance_date + offset_days) using the compensatory leave type.
    """
    if not doc.in_time or not doc.out_time or not doc.shift:
        frappe.log_error(f"Leave App skip: missing in_time/out_time/shift. doc={doc.name}", "DBG LeaveApp")
        return

    try:
        shift = frappe.get_cached_doc("Shift Type", doc.shift)
        offset = cint(shift.get("custom_compensatory_leave_offset_days"))
        frappe.log_error(f"Leave App: shift={doc.shift} offset={offset}", "DBG LeaveApp")
        if not offset:
            return

        from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
        holiday_list = get_holiday_list_for_employee(doc.employee, raise_exception=False)
        frappe.log_error(f"Leave App: holiday_list={holiday_list}", "DBG LeaveApp")
        if not holiday_list:
            return

        is_holiday = frappe.db.exists(
            "Holiday",
            {"parent": holiday_list, "holiday_date": doc.attendance_date}
        )
        frappe.log_error(f"Leave App: attendance_date={doc.attendance_date} is_holiday={is_holiday}", "DBG LeaveApp")
        if not is_holiday:
            return

        leave_date = getdate(doc.attendance_date) + timedelta(days=offset)
        frappe.log_error(f"Leave App: leave_date={leave_date}", "DBG LeaveApp")

        # Skip if a Leave Application already exists for this employee + leave_date
        already_exists = frappe.db.exists(
            "Leave Application",
            {
                "employee": doc.employee,
                "from_date": leave_date,
                "to_date": leave_date,
                "docstatus": ["!=", 2],
            },
        )
        frappe.log_error(f"Leave App: already_exists={already_exists}", "DBG LeaveApp")
        if already_exists:
            return

        leave_type = frappe.db.get_value("Leave Type", {"is_compensatory": 1}, "name")
        frappe.log_error(f"Leave App: leave_type={leave_type}", "DBG LeaveApp")
        if not leave_type:
            frappe.log_error(
                f"No Leave Type with 'Is Compensatory' found. "
                f"Cannot create leave application for {doc.employee} on {leave_date}.",
                "Compensatory Leave Application Setup Missing"
            )
            return

        leave_app = frappe.new_doc("Leave Application")
        leave_app.employee = doc.employee
        leave_app.leave_type = leave_type
        leave_app.from_date = leave_date
        leave_app.to_date = leave_date
        leave_app.description = (
            f"Auto-created: worked on holiday {doc.attendance_date} (Attendance {doc.name})"
        )
        leave_app.leave_approver = "mira@raindropinc.com"
        leave_app.status = "Open"
        leave_app.insert(ignore_permissions=True)
        leave_app.submit()

        frappe.logger("biometric").info(
            f"Leave Application {leave_app.name} created for "
            f"{doc.employee} on {leave_date} (holiday work on {doc.attendance_date})"
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Error creating leave application for Attendance {doc.name}"
        )


# ---------------------------------------------------------------------------
# Workflow-aware out_time adjustment
# ---------------------------------------------------------------------------

APPROVED_WORKFLOW_KEYWORDS = ("approve",)
REJECTED_WORKFLOW_KEYWORDS = ("reject",)


def _workflow_state(doc):
    return (doc.get("workflow_state") or "").strip().lower()


def _is_approved(doc):
    return any(k in _workflow_state(doc) for k in APPROVED_WORKFLOW_KEYWORDS)


def _is_rejected(doc):
    return any(k in _workflow_state(doc) for k in REJECTED_WORKFLOW_KEYWORDS)


def adjust_out_time(doc, method=None):
    """
    Adjusts Attendance out_time to shift end time if earlier.
    Triggered before_save / before_submit.
    Only adjusts when workflow state is Approved.
    """
    if _is_rejected(doc):
        return

    if method != "before_submit" and not _is_approved(doc):
        return

    if not doc.attendance_date or not doc.out_time or not doc.shift:
        return

    try:
        shift = frappe.get_cached_doc("Shift Type", doc.shift)
        if not shift or not shift.end_time:
            return

        shift_end = get_time(shift.end_time)
        out_dt = get_datetime(doc.out_time)
        shift_end_dt = get_datetime(doc.attendance_date).replace(
            hour=shift_end.hour,
            minute=shift_end.minute,
            second=0,
            microsecond=0
        )

        if out_dt < shift_end_dt:
            doc.out_time = shift_end_dt

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Attendance Out Time Adjustment Failed")


# ---------------------------------------------------------------------------
# Auto-submit when employee has worked full shift
# ---------------------------------------------------------------------------

def auto_submit_attendance(doc, method=None):
    """
    Auto-submit Attendance if out_time >= shift end_time.
    Triggered on_update.
    """
    if not doc.name or not doc.attendance_date or not doc.out_time or not doc.shift:
        return

    if doc.docstatus != 0:
        return

    try:
        shift = frappe.get_cached_doc("Shift Type", doc.shift)
        if not shift or not shift.end_time:
            return

        shift_end = get_time(shift.end_time)
        shift_end_dt = get_datetime(doc.attendance_date).replace(
            hour=shift_end.hour,
            minute=shift_end.minute,
            second=0,
            microsecond=0
        )

        out_dt = get_datetime(doc.out_time)

        if out_dt >= shift_end_dt:
            doc.workflow_state = "Approved"
            doc.status = "Present"
            doc.flags.ignore_permissions = True
            doc.submit()

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Attendance Auto Submit Failed")
