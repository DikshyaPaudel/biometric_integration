import traceback

import frappe
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response

from biometric_integration.biometric_integration.utils import process_attendance_records
import logging

def handle_iclock_request():
    """Intercept /iclock/* requests before Frappe's website renderer."""
    logger = frappe.logger()
    logger.setLevel(logging.INFO)

    request = frappe.local.request
    path = request.path

    if not path.startswith("/iclock/"):
        return

    method = request.method
    args = frappe.local.form_dict
    logger.info(f"Received ADMS request: {method} {path} with args {dict(args)} from {request.remote_addr}")

    # Log every request from device
    frappe.log_error(
        title="ADMS Request",
        message=f"Method: {method}\nPath: {path}\nArgs: {dict(args)}\n"
        f"Remote IP: {request.remote_addr}",
    )

    # GET /iclock/getrequest?SN=xxx — device polling for commands
    if "getrequest" in path:
        _abort_with_plain("OK")

    # GET /iclock/cdata?SN=xxx&options=all — device handshake
    if method == "GET" and args.get("options"):
        sn = args.get("SN", "")

        frappe.log_error(
            title="ADMS Handshake",
            message=f"Device connected: SN={sn}\nIP: {request.remote_addr}",
        )

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

        body = request.get_data(as_text=True)

        frappe.log_error(
            title=f"ADMS POST (table={table})",
            message=f"SN: {sn}\nTable: {table}\nBody:\n{body[:500]}",
        )

        if table == "ATTLOG":
            try:
                attendance_data = _parse_attlog(body)

                frappe.log_error(
                    title="ADMS Parsed Records",
                    message=f"SN: {sn}\nParsed {len(attendance_data)} records:\n"
                    + "\n".join(str(r) for r in attendance_data[:20]),
                )

                if attendance_data:
                    device_identifier = _get_device_identifier(sn)

                    frappe.log_error(
                        title="ADMS Device Lookup",
                        message=f"SN: {sn}\nDevice identifier: {device_identifier}",
                    )

                    result = process_attendance_records(
                        attendance_data, device_identifier=device_identifier
                    )

                    frappe.log_error(
                        title="ADMS Sync Result",
                        message=f"SN: {sn}\n"
                        f"Synced: {result.get('synced', 0)}\n"
                        f"Errors: {result.get('errors', 0)}\n"
                        f"Message: {result.get('message', '')}\n"
                        f"Error details: {result.get('error_details', [])}",
                    )
                else:
                    frappe.log_error(
                        title="ADMS No Records",
                        message=f"SN: {sn}\nBody was not empty but parsed 0 records.\nBody: {body[:300]}",
                    )

            except Exception as e:
                frappe.log_error(
                    title="ADMS Processing Error",
                    message=f"SN: {sn}\nError: {str(e)}\n{traceback.format_exc()}",
                )

        _abort_with_plain("OK")

    # Fallback for any /iclock/ path
    frappe.log_error(
        title="ADMS Unknown Request",
        message=f"Unhandled: {method} {path}\nArgs: {dict(args)}",
    )
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
    """Abort request processing and return plain text to the device."""
    # Commit any pending DB writes (like log_error) before aborting
    frappe.db.commit()
    response = Response(text, status=200, content_type="text/plain")
    raise HTTPException(response=response)
