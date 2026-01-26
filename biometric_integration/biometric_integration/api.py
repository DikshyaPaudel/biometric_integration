
import frappe
from frappe import _
from frappe.utils import now_datetime
import traceback

# Check if pyzk is installed
try:
    from zk import ZK
    ZK_INSTALLED = True
except ImportError:
    ZK_INSTALLED = False


@frappe.whitelist()
def test_connection(device_ip, device_port=4370):
    """
    Test connection to biometric device
    
    Args:
        device_ip (str): IP address of device
        device_port (int): Port number (default: 4370)
    
    Returns:
        dict: Success status and message
    """
    if not ZK_INSTALLED:
        return {
            "success": False,
            "message": "pyzk library not installed. Run: pip install pyzk"
        }
    
    try:
        # Convert port to int
        device_port = int(device_port) if device_port else 4370
        
        # Create connection
        zk = ZK(device_ip, port=device_port, timeout=5)
        conn = zk.connect()
        
        # Get device info
        firmware = conn.get_firmware_version()
        serial = conn.get_serialnumber()
        
        # Get user count
        users = conn.get_users()
        user_count = len(users) if users else 0
        
        # Disconnect
        conn.disconnect()
        
        return {
            "success": True,
            "message": f"Connection successful!",
            "firmware": firmware,
            "serial": serial,
            "user_count": user_count
        }
        
    except Exception as e:
        error_msg = str(e)
        frappe.log_error(
            title="Biometric Device Connection Test Failed",
            message=f"IP: {device_ip}, Port: {device_port}\nError: {error_msg}\n{traceback.format_exc()}"
        )
        return {
            "success": False,
            "message": f"Biometric Connection failed: {error_msg}"
        }


# @frappe.whitelist()
# def sync_attendance_from_device(device_ip, device_port=4370, clear_after_sync=1):
#     """
#     Sync attendance records from biometric device to ERPNext
    
#     Args:
#         device_ip (str): IP address of device
#         device_port (int): Port number
#         clear_after_sync (int): Clear device logs after sync (1 or 0)
    
#     Returns:
#         dict: Sync results with counts
#     """
#     if not ZK_INSTALLED:
#         frappe.throw(_("pyzk library not installed. Run: pip install pyzk"))
    
#     try:
#         device_port = int(device_port) if device_port else 4370
#         # clear_after_sync = int(clear_after_sync) if clear_after_sync else 1
        
#         # Connect to device
#         zk = ZK(device_ip, port=device_port, timeout=5)
#         conn = zk.connect()
#         conn.disable_device()  # Disable during operation
        
#         # Get attendance logs
#         attendance_logs = conn.get_attendance()
        
#         if not attendance_logs:
#             conn.enable_device()
#             conn.disconnect()
#             return {
#                 "success": True,
#                 "synced": 0,
#                 "errors": 0,
#                 "message": "No attendance logs found on device"
#             }
        
#         synced_count = 0
#         error_count = 0
#         error_details = []
        
#         for log in attendance_logs:
#             try:
#                 # Get employee by device ID
#                 employee = frappe.db.get_value(
#                     "Employee",
#                     {"attendance_device_id": str(log.user_id)},
#                     ["name", "company"],
#                     as_dict=True
#                 )
                
#                 if not employee:
#                     error_count += 1
#                     error_details.append(f"Employee not found for device ID: {log.user_id}")
#                     continue
                
#                 # Check if attendance already exists
#                 attendance_date = log.timestamp.date()
#                 existing = frappe.db.exists("Attendance", {
#                     "employee": employee.name,
#                     "attendance_date": attendance_date,
#                     "docstatus": ["!=", 2]
#                 })
                
#                 if existing:
#                     continue  # Skip if already recorded
                
#                 # Create attendance record
#                 attendance = frappe.new_doc("Attendance")
#                 attendance.employee = employee.name
#                 attendance.attendance_date = attendance_date
#                 attendance.status = "Present"
#                 attendance.in_time = log.timestamp
#                 attendance.company = employee.company
                
#                 # Add custom field if exists
#                 if frappe.db.has_column("Attendance", "device_id"):
#                     attendance.device_id = device_ip
                
#                 attendance.insert(ignore_permissions=True)
#                 attendance.submit()
                
#                 synced_count += 1
                
#             except Exception as e:
#                 error_count += 1
#                 error_msg = str(e)[:100]
#                 error_details.append(f"User {log.user_id}: {error_msg}")
#                 frappe.log_error(
#                     title="Biometric Attendance Sync Error",
#                     message=f"User ID: {log.user_id}\nError: {str(e)}\n{traceback.format_exc()}"
#                 )
        
#         # Clear device logs if requested and sync successful
#         if clear_after_sync and synced_count > 0:
#             try:
#                 conn.clear_attendance()
#             except Exception as e:
#                 frappe.log_error(
#                     title="Failed to Clear Device Logs",
#                     message=f"Device: {device_ip}\nError: {str(e)}"
#                 )
        
#         # Re-enable device
#         conn.enable_device()
#         conn.disconnect()
        
#         frappe.db.commit()
        
#         result = {
#             "success": True,
#             "synced": synced_count,
#             "errors": error_count,
#             "message": f"Synced {synced_count} records. {error_count} errors."
#         }
        
#         if error_details:
#             result["error_details"] = error_details[:10]  # First 10 errors
        
#         return result
        
#     except Exception as e:
#         frappe.log_error(
#             title="Biometric Attendance Sync Failed",
#             message=f"Device: {device_ip}\nError: {str(e)}\n{traceback.format_exc()}"
#         )
#         frappe.throw(_("Sync failed: {0}").format(str(e)))


# Add this import at top
from datetime import datetime, timedelta

# Replace sync_attendance_from_device function with enhanced version
@frappe.whitelist()
def sync_attendance_from_device(device_ip, device_port=4370, clear_after_sync=1):
    """
    Enhanced sync with check-in/check-out times and working hours
    """
    if not ZK_INSTALLED:
        frappe.throw(_("pyzk library not installed"))
    
    try:
        device_port = int(device_port) if device_port else 4370
        # clear_after_sync = int(clear_after_sync) if clear_after_sync else 1
        
        # Connect
        zk = ZK(device_ip, port=device_port, timeout=5)
        conn = zk.connect()
        conn.disable_device()
        
        attendance_logs = conn.get_attendance()
        
        if not attendance_logs:
            conn.enable_device()
            conn.disconnect()
            return {"success": True, "synced": 0, "errors": 0, "message": "No logs"}
        
        # Group by employee and date
        grouped = {}
        for log in attendance_logs:
            key = f"{log.user_id}_{log.timestamp.date()}"
            if key not in grouped:
                grouped[key] = {
                    'employee_id': str(log.user_id),
                    'date': log.timestamp.date(),
                    'punches': []
                }
            grouped[key]['punches'].append(log.timestamp)
        
        synced = 0
        errors = 0
        error_details = []
        
        for key, data in grouped.items():
            try:
                employee = frappe.db.get_value("Employee",
                    {"attendance_device_id": data['employee_id']},
                    ["name", "company", "employee_name", "default_shift"],
                    as_dict=True
                )
                
                if not employee:
                    errors += 1
                    error_details.append(f"Employee not found: {data['employee_id']}")
                    continue
                
                # Check existing
                existing = frappe.db.exists("Attendance", {
                    "employee": employee.name,
                    "attendance_date": data['date'],
                    "docstatus": ["!=", 2]
                })
                
                # Sort punches
                punches = sorted(data['punches'])
                check_in = punches[0]
                check_out = punches[-1] if len(punches) > 1 else None
                
                if existing:
                    # Update existing
                    doc = frappe.get_doc("Attendance", existing)
                    if doc.docstatus == 1:
                        doc.cancel()
                    doc.in_time = check_in
                    doc.out_time = check_out
                    if check_in and check_out:
                        hours = (check_out - check_in).total_seconds() / 3600
                        doc.working_hours = round(hours, 2)
                    doc.save(ignore_permissions=True)
                    doc.submit()
                    continue
                
                # Create new
                att = frappe.new_doc("Attendance")
                att.employee = employee.name
                att.employee_name = employee.employee_name
                att.attendance_date = data['date']
                att.company = employee.company
                att.in_time = check_in
                att.out_time = check_out
                
                # Calculate hours
                if check_in and check_out:
                    hours = (check_out - check_in).total_seconds() / 3600
                    att.working_hours = round(hours, 2)
                    
                    # Status based on hours
                    if hours < 4:
                        att.status = "Absent"
                    elif hours < 6:
                        att.status = "Half Day"
                    else:
                        att.status = "Present"
                else:
                    att.status = "Present"
                
                # Shift
                if employee.default_shift:
                    att.shift = employee.default_shift
                
                # Custom fields if exist
                if frappe.db.has_column("Attendance", "device_id"):
                    att.device_id = device_ip
                if frappe.db.has_column("Attendance", "total_punches"):
                    att.total_punches = len(punches)
                
                att.insert(ignore_permissions=True)
                att.submit()
                synced += 1
                
            except Exception as e:
                errors += 1
                error_details.append(f"{data['employee_id']}: {str(e)[:50]}")
                frappe.log_error(str(e), "Attendance Sync Error")
        
        # Clear logs
        # if clear_after_sync and synced > 0:
        #     try:
        #         conn.clear_attendance()
        #     except:
        #         pass
        
        conn.enable_device()
        conn.disconnect()
        frappe.db.commit()
        
        return {
            "success": True,
            "synced": synced,
            "errors": errors,
            "message": f"Synced {synced} records. {errors} errors.",
            "error_details": error_details[:10]
        }
        
    except Exception as e:
        frappe.log_error(str(e), "Sync Failed")
        frappe.throw(str(e))

@frappe.whitelist()
def sync_employees_to_device(device_ip, device_port=4370):
    """
    Sync employees from ERPNext to biometric device
    
    Args:
        device_ip (str): IP address of device
        device_port (int): Port number
    
    Returns:
        dict: Sync results
    """
    if not ZK_INSTALLED:
        frappe.throw(_("pyzk library not installed. Run: pip install pyzk"))
    
    try:
        device_port = int(device_port) if device_port else 4370
        
        # Get active employees with device IDs
        employees = frappe.get_all(
            "Employee",
            filters={
                "status": "Active",
                "attendance_device_id": ["!=", ""]
            },
            fields=["name", "employee_name", "attendance_device_id"]
        )
        
        if not employees:
            return {
                "success": False,
                "message": "No employees with Biometric Device ID found"
            }
        
        # Connect to device
        zk = ZK(device_ip, port=device_port, timeout=5)
        conn = zk.connect()
        conn.disable_device()
        
        synced_count = 0
        error_count = 0
        error_details = []
        
        for emp in employees:
            try:
                # Add user to device
                uid = int(emp.attendance_device_id)
                name = emp.employee_name[:24]  # Device limit: 24 chars
                
                conn.set_user(
                    uid=uid,
                    name=name,
                    privilege=0,  # Regular user
                    password='',
                    group_id='',
                    user_id=str(uid)
                )
                
                synced_count += 1
                
            except Exception as e:
                error_count += 1
                error_msg = str(e)[:100]
                error_details.append(f"{emp.employee_name}: {error_msg}")
                frappe.log_error(
                    title="Employee Sync Error",
                    message=f"Employee: {emp.name}\nError: {str(e)}"
                )
        
        conn.enable_device()
        conn.disconnect()
        
        result = {
            "success": True,
            "synced": synced_count,
            "errors": error_count,
            "message": f"Synced {synced_count} employees. {error_count} errors."
        }
        
        if error_details:
            result["error_details"] = error_details[:10]
        
        return result
        
    except Exception as e:
        frappe.log_error(
            title="Employee Sync to Device Failed",
            message=f"Device: {device_ip}\nError: {str(e)}\n{traceback.format_exc()}"
        )
        frappe.throw(_("Sync failed: {0}").format(str(e)))


@frappe.whitelist()
def clear_device_logs(device_ip, device_port=4370):
    """
    Clear all attendance logs from device
    
    Args:
        device_ip (str): IP address of device
        device_port (int): Port number
    
    Returns:
        dict: Success status
    """
    if not ZK_INSTALLED:
        frappe.throw(_("pyzk library not installed. Run: pip install pyzk"))
    
    try:
        device_port = int(device_port) if device_port else 4370
        
        # Connect to device
        zk = ZK(device_ip, port=device_port, timeout=5)
        conn = zk.connect()
        conn.disable_device()
        
        # Clear attendance logs
        conn.clear_attendance()
        
        conn.enable_device()
        conn.disconnect()
        
        return {
            "success": True,
            "message": "Device logs cleared successfully"
        }
        
    except Exception as e:
        frappe.log_error(
            title="Clear Device Logs Failed",
            message=f"Device: {device_ip}\nError: {str(e)}\n{traceback.format_exc()}"
        )
        frappe.throw(_("Failed to clear logs: {0}").format(str(e)))


@frappe.whitelist()
def get_device_users(device_ip, device_port=4370):
    """
    Get list of users from device
    
    Args:
        device_ip (str): IP address of device
        device_port (int): Port number
    
    Returns:
        dict: List of users
    """
    if not ZK_INSTALLED:
        frappe.throw(_("pyzk library not installed. Run: pip install pyzk"))
    
    try:
        device_port = int(device_port) if device_port else 4370
        
        # Connect to device
        zk = ZK(device_ip, port=device_port, timeout=5)
        conn = zk.connect()
        
        # Get users
        users = conn.get_users()
        
        conn.disconnect()
        
        user_list = []
        for user in users:
            user_list.append({
                "uid": user.uid,
                "name": user.name,
                "user_id": user.user_id,
                "privilege": user.privilege
            })
        
        return {
            "success": True,
            "users": user_list,
            "count": len(user_list)
        }
        
    except Exception as e:
        frappe.log_error(
            title="Get Device Users Failed",
            message=f"Device: {device_ip}\nError: {str(e)}"
        )
        frappe.throw(_("Failed to get users: {0}").format(str(e)))


@frappe.whitelist()
def get_device_info(device_ip, device_port=4370):
    """
    Get device information
    
    Args:
        device_ip (str): IP address of device
        device_port (int): Port number
    
    Returns:
        dict: Device information
    """
    if not ZK_INSTALLED:
        frappe.throw(_("pyzk library not installed. Run: pip install pyzk"))
    
    try:
        device_port = int(device_port) if device_port else 4370
        
        # Connect to device
        zk = ZK(device_ip, port=device_port, timeout=5)
        conn = zk.connect()
        
        # Get device info
        info = {
            "firmware": conn.get_firmware_version(),
            "serialnumber": conn.get_serialnumber(),
            "platform": conn.get_platform(),
            "device_name": conn.get_device_name(),
            "face_version": conn.get_face_version(),
            "fp_version": conn.get_fp_version(),
        }
        
        # Get counts
        users = conn.get_users()
        attendance = conn.get_attendance()
        
        info["user_count"] = len(users) if users else 0
        info["attendance_count"] = len(attendance) if attendance else 0
        
        conn.disconnect()
        
        return {
            "success": True,
            "info": info
        }
        
    except Exception as e:
        frappe.log_error(
            title="Get Device Info Failed",
            message=f"Device: {device_ip}\nError: {str(e)}"
        )
        return {
            "success": False,
            "message": str(e)
        }