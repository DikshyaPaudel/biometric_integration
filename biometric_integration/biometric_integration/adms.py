import frappe
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response

from biometric_integration.biometric_integration.utils import process_attendance_records


def handle_iclock_request():
    """Intercept /iclock/* requests before Frappe's website renderer.

    Called via before_request hook. If the path doesn't start with /iclock/,
    returns immediately and lets Frappe handle the request normally.
    """
    request = frappe.local.request
    path = request.path

    if not path.startswith("/iclock/"):
        return

    method = request.method
    args = frappe.local.form_dict

    # GET /iclock/getrequest?SN=xxx — device polling for commands
    if "getrequest" in path:
        _abort_with_plain("OK")

    # GET /iclock/cdata?SN=xxx&options=all — device handshake
    if method == "GET" and args.get("options"):
        sn = args.get("SN", "")
        frappe.logger("adms").info(f"Handshake from SN={sn}")

        config = (
            "GET OPTION FROM: {sn}\n"
            "Stamp=9999\n"
            "OpStamp=9999\n"
            "PhotoStamp=9999\n"
            "ErrorDelay=60\n"
            "Delay=30\n"
            "TransTimes=00:00;14:05\n"
            "TransInterval=1\n"
            "TransFlag=TransData AttLog\tOpLog\n"
            "Realtime=1\n"
            "TimeZone=5\n"
            "ATTLOGStamp=0\n"
            "OPERLOGStamp=0\n"
        ).format(sn=sn)

        _abort_with_plain(config)

    # POST /iclock/cdata?SN=xxx&table=ATTLOG — device pushing attendance
    if method == "POST":
        sn = args.get("SN", "")
        table = args.get("table", "")

        if table == "ATTLOG":
            body = request.get_data(as_text=True)
            frappe.logger("adms").info(f"ATTLOG from SN={sn}: {body[:200]}")

            attendance_data = _parse_attlog(body)

            if attendance_data:
                device_identifier = _get_device_identifier(sn)
                result = process_attendance_records(
                    attendance_data, device_identifier=device_identifier
                )
                frappe.logger("adms").info(
                    f"SN={sn}: {result.get('synced', 0)} synced, "
                    f"{result.get('errors', 0)} errors"
                )

        _abort_with_plain("OK")

    # Fallback for any /iclock/ path
    _abort_with_plain("OK")


def _parse_attlog(body):
    """Parse ADMS ATTLOG tab-separated body into attendance dicts.

    Each line: pin \\t timestamp \\t status \\t verify \\t workcode
    Example:   1\\t2026-02-09 08:30:00\\t0\\t1\\t0
    """
    records = []
    for line in body.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            records.append({
                "user_id": parts[0].strip(),
                "timestamp": parts[1].strip(),
            })
    return records


def _get_device_identifier(serial_number):
    """Look up Biometric Device by serial number or device name."""
    if not serial_number:
        return None

    device_ip = frappe.db.get_value(
        "Biometric Device",
        {"device_name": serial_number},
        "device_ip",
    )
    if device_ip:
        return device_ip

    return serial_number


def _abort_with_plain(text):
    """Abort request processing and return plain text to the device.

    Raises HTTPException which is caught by Frappe's app.py (line 134)
    and returned directly as a WSGI response.
    """
    response = Response(text, status=200, content_type="text/plain")
    raise HTTPException(response=response)
