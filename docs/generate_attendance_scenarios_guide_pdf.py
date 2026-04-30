from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "attendance_scenarios_guide.pdf"


def _p(text: str, style: ParagraphStyle) -> Paragraph:
	return Paragraph(text.replace("\n", "<br/>"), style)


def build_pdf(output_path: Path) -> None:
	doc = SimpleDocTemplate(
		str(output_path),
		pagesize=A4,
		topMargin=2 * cm,
		bottomMargin=2 * cm,
		leftMargin=2.2 * cm,
		rightMargin=2.2 * cm,
	)

	styles = getSampleStyleSheet()
	title = ParagraphStyle(
		"Title",
		parent=styles["Title"],
		fontName="Helvetica-Bold",
		fontSize=18,
		textColor=colors.HexColor("#1a1a2e"),
		alignment=TA_CENTER,
		spaceAfter=6,
	)
	subtitle = ParagraphStyle(
		"Subtitle",
		parent=styles["Normal"],
		fontName="Helvetica",
		fontSize=10,
		textColor=colors.HexColor("#555555"),
		alignment=TA_CENTER,
		spaceAfter=12,
	)
	h = ParagraphStyle(
		"H",
		parent=styles["Heading3"],
		fontName="Helvetica-Bold",
		fontSize=11,
		textColor=colors.HexColor("#1a1a2e"),
		spaceBefore=12,
		spaceAfter=4,
	)
	b = ParagraphStyle(
		"B",
		parent=styles["Normal"],
		fontName="Helvetica",
		fontSize=10,
		leading=15,
		textColor=colors.HexColor("#333333"),
		alignment=TA_JUSTIFY,
		spaceAfter=6,
	)
	callout = ParagraphStyle(
		"Callout",
		parent=b,
		fontName="Helvetica-Oblique",
		fontSize=9.5,
		backColor=colors.HexColor("#f4f6fb"),
		borderColor=colors.HexColor("#c5cde8"),
		leftIndent=12,
		rightIndent=8,
		spaceBefore=4,
		spaceAfter=6,
	)
	footer = ParagraphStyle(
		"Footer",
		parent=styles["Normal"],
		fontName="Helvetica",
		fontSize=8,
		textColor=colors.HexColor("#999999"),
		alignment=TA_CENTER,
	)

	story: list = []
	story.append(_p("Biometric Integration — Attendance Scenario Guide", title))
	story.append(_p("How punches become Employee Checkins and Attendance in ERPNext", subtitle))
	story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#c5cde8"), spaceAfter=10))

	story.append(_p("1) Data flow (high level)", h))
	story.append(
		_p(
			"Device punch → sync into ERPNext (pull or push) → process punches into one IN and one OUT per day "
			"→ ERPNext Auto Attendance generates Attendance → app overrides add deviation fields and optional "
			"approval/submission behavior.",
			b,
		)
	)
	story.append(_p("2) Required configuration checklist", h))
	story.append(
		_p(
			"<b>Employee</b>: set <b>attendance_device_id</b> and ensure a Shift Type is assigned.<br/>"
			"<b>Shift Type</b>: enable <b>Auto Attendance</b> and configure start/end/grace/thresholds.<br/>"
			"<b>Biometric Device</b>: set IP/port and sync logs (pull model).",
			b,
		)
	)

	story.append(_p("3) Scenarios", h))
	story.append(
		_p(
			"<b>A — Normal day (many punches)</b>: first punch = IN, last punch = OUT; middle punches ignored by this app’s pairing logic.",
			b,
		)
	)
	story.append(_p("<b>B — Only 1 punch (missing OUT)</b>: IN is created, OUT is missing; auto attendance may not compute working hours. Fix by adding the missing OUT or re-syncing.", b))
	story.append(_p("<b>C — Late/early entry</b>: the app stores deviation seconds in custom fields (rounded to the nearest minute).", b))
	story.append(_p("<b>D — Early exit vs shift end</b>: if workflow is Approved, the app can adjust OUT time up to shift end for payroll consistency.", b))
	story.append(_p("<b>E — Full-time OUT</b>: if OUT ≥ shift end, the app can auto-approve and submit Attendance.", b))
	story.append(_p("<b>F — Overnight shift</b>: if shift end is after midnight, deviation calculations treat end time as next day.", b))
	story.append(_p("<b>G — Employee not mapped</b>: unknown device IDs are skipped; set Employee <b>attendance_device_id</b> and re-sync.", b))

	story.append(_p("4) Troubleshooting quick checks", h))
	story.append(
		_p(
			"• No Attendance: verify Shift Type Auto Attendance and scheduler.<br/>"
			"• Wrong times: verify device timezone/clock and timestamp formats.<br/>"
			"• OUT not updating: app updates OUT only when a later punch exists for that date.<br/>"
			"• Approval behavior: verify Attendance events in <b>biometric_integration/hooks.py</b> and workflow labels.",
			callout,
		)
	)

	story.append(Spacer(1, 12))
	story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=6))
	story.append(_p("This guide describes current app logic; final Attendance results depend on your Shift Type and HR settings.", footer))

	doc.build(story)


if __name__ == "__main__":
	build_pdf(OUTPUT)
	print(f"PDF saved to: {OUTPUT}")

