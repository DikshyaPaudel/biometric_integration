# Biometric Integration — Attendance Scenario Guide

This document explains how the `biometric_integration` app moves biometric punches into ERPNext attendance, and how common real‑world scenarios behave.

## 1) Data flow (high level)

1. **Biometric device punch** → creates a raw log entry (user id + timestamp) on the device.
2. **Sync into ERPNext** (two options):
   - **Pull model:** ERPNext connects to the device and pulls logs (`sync_attendance_from_device`).
   - **Push model:** an external script pushes logs to ERPNext (`receive_attendance`).
3. **Punch processing** → `process_attendance_records()` groups punches by **(device user id, date)** and creates/updates:
   - one **Employee Checkin (IN)** per employee per day (first punch)
   - one **Employee Checkin (OUT)** per employee per day (last punch)
4. **Attendance creation** → ERPNext **Auto Attendance** (Shift Type → *Enable Auto Attendance*) turns checkins into an **Attendance** record.
5. **App overrides** (extra behavior) → on Attendance events the app:
   - stores shift deviation seconds in custom fields (`set_shift_deviation_fields`)
   - adjusts `out_time` to shift end on approved workflows (`adjust_out_time`)
   - auto-approves/submits when `out_time` is at/after shift end (`auto_submit_attendance`)

## 2) Required configuration checklist

- **Employee**
  - Set `attendance_device_id` (must match the biometric device user id).
  - Ensure a working **Shift Type** assignment (e.g., Employee `default_shift` or shift assignment as per your setup).
- **Shift Type**
  - Enable **Auto Attendance**.
  - Set start/end times and grace/threshold values as needed.
- **Biometric Device (DocType)**
  - Add device IP/port and use the form button actions to test/sync (if using pull model).

## 3) Scenarios (what happens and what to check)

### Scenario A — Normal day (many punches)

**Input punches:** 09:02, 12:58, 13:35, 18:06  
**Result:**
- IN checkin = **09:02** (first punch)
- OUT checkin = **18:06** (last punch)
- ERPNext auto attendance computes working hours from IN/OUT and marks Attendance.

**Notes:** Middle punches are ignored by this app’s pairing logic; only first/last matter.

### Scenario B — Only 1 punch (missing OUT)

**Input punches:** 09:05 only  
**Result:**
- IN checkin is created
- OUT checkin is not created
- Auto attendance may not be able to compute `working_hours` correctly depending on your shift rules

**What to do:** add a missing OUT checkin (or correct device sync) and re-run/let auto attendance process again.

### Scenario C — Late entry / early entry vs shift start

If shift starts at 09:00 and IN is 09:20:
- ERPNext may flag `late_entry` depending on grace rules
- App stores `custom_late_entry` (seconds) on Attendance (rounded to nearest minute)

If IN is earlier than shift start:
- App stores `custom_early_entry` (seconds), and `custom_late_entry` remains 0.

### Scenario D — Early exit vs shift end

If shift ends at 18:00 and OUT is 17:40:
- ERPNext may flag `early_exit` depending on grace rules
- If workflow is **Approved**, the app’s `adjust_out_time` can change `out_time` to the shift end time (18:00).

**Why:** some teams want approved attendances to always end at shift end for payroll consistency.

### Scenario E — Full-time OUT → auto-approve/submit

If OUT is **at or after** shift end:
- `auto_submit_attendance` sets workflow_state = **Approved** and submits the Attendance.

**What to watch:**
- If you use a custom workflow state naming scheme, ensure “Approved” matches your workflow.

### Scenario F — Overnight shift (end time next day)

If a Shift Type ends after midnight (end_time < start_time):
- `set_shift_deviation_fields` treats the shift end as **next day** when calculating deviations.

### Scenario G — Employee not mapped (device id missing)

If a punch arrives with a user id that doesn’t match any Employee `attendance_device_id`:
- the record is counted as an error and is not written to Employee Checkin

**Fix:** set the correct device id on Employee and re-sync the same logs.

## 4) Troubleshooting quick checks

- **No Attendance created:** confirm Shift Type auto attendance is enabled and scheduled jobs are running.
- **IN/OUT swapped or wrong day:** ensure device time/timezone is correct; ensure punch timestamps are in the expected format.
- **OUT not updating:** the app updates OUT only if a later OUT punch exists for the same date.
- **Approval behavior unexpected:** check `hooks.py` Attendance events and your workflow state labels.

