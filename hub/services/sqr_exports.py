"""Columns and workbook rendering for SQR tracker downloads."""

from collections.abc import Callable, Iterable
from datetime import date
from decimal import Decimal
from io import BytesIO

from accounts.models import User
from hub.models import SqrSubmission

SqrExportColumn = tuple[str, Callable[[SqrSubmission], str | float | int]]


def _date(d: date | None) -> str:
    """Format a tracker date without a time component."""
    return d.strftime("%Y-%m-%d") if d else ""


def _name(u: User | None) -> str:
    """Use the full name or username of the selected owner."""
    return (u.get_full_name() or u.username) if u else ""


def _get_sqr_export_columns() -> list[SqrExportColumn]:
    """Define the tracker columns, also used as the current import headers."""
    return [
        ("SQR Date", lambda s: _date(s.created_at.date())),
        ("SQR ID", lambda s: s.reference_code or ""),
        ("Account Name", lambda s: s.customer_name or ""),
        ("Service Description", lambda s: s.project_title or ""),
        ("Scope of Services", lambda s: s.project_details or ""),
        ("RQ ID", lambda s: s.linked_request.reference_code if s.linked_request else ""),
        ("Group Name", lambda s: s.customer_company or ""),
        ("Account Manager", lambda s: s.customer_contact or ""),
        ("Requester Name", lambda s: _name(s.engineer)),
        ("Approver Name", lambda s: _name(s.pm_esg_reviewer)),
        ("SQR Doc. Ref. Link", lambda s: s.sqr_folder_link or ""),
        ("SSE Man-hrs", lambda s: float(s.sse_manhrs) if s.sse_manhrs is not None else ""),
        ("SSE Amount", lambda s: float(s.sse_amount) if s.sse_amount is not None else ""),
        ("PM Man-hrs", lambda s: float(s.pm_manhrs) if s.pm_manhrs is not None else ""),
        ("PM Amount", lambda s: float(s.pm_amount) if s.pm_amount is not None else ""),
        ("Maintenance Amt.", lambda s: float(s.managed_support_amount) if s.managed_support_amount is not None else ""),
        ("Discount Rate (%)", lambda s: float(s.discount_rate) if s.discount_rate is not None else ""),
        ("Discount Amount", lambda s: float(s.computed_discount_amount) if s.computed_discount_amount is not None else ""),
        ("Total Price", lambda s: float(s.computed_total_price) if s.computed_total_price is not None else ""),
        ("Remarks", lambda s: s.remarks or ""),
        ("SQR Status", lambda s: s.get_status_display() if hasattr(s, "get_status_display") else s.status),
        ("Hold Reason", lambda s: s.hold_reason or ""),
        ("Approval Date", lambda s: _date(s.reviewed_at.date()) if s.reviewed_at else ""),
        ("Validity Due Date", lambda s: _date(s.validity_due_date)),
        ("Proposal Status", lambda s: s.get_proposal_status_display() if hasattr(s, "get_proposal_status_display") else (s.proposal_status or "")),
        ("Assigned PM", lambda s: _name(s.assigned_pm) or _name(s.pm_esg_reviewer)),
        ("Assigned SSE", lambda s: _name(s.assigned_sse)),
        ("Start Date", lambda s: _date(s.delivery_start_date)),
        ("Legacy Target Finish Date", lambda s: _date(s.delivery_target_finish_date)),
        ("Overall Status", lambda s: s.overall_status or ""),
        ("Schedule Performance", lambda s: s.schedule_performance or ""),
        ("Budget/Effort Performance", lambda s: s.budget_effort_performance or ""),
        ("Quality Performance", lambda s: s.quality_performance or ""),
        ("Legacy Health Status", lambda s: s.delivery_health or ""),
        ("Overall Progress %", lambda s: s.delivery_progress if s.delivery_progress is not None else ""),
        ("Key Updates / Risks", lambda s: s.key_updates_risks or ""),
        ("Finish Date", lambda s: _date(s.delivery_actual_finish_date)),
        ("Legacy Completion Signed Date", lambda s: _date(s.delivery_completion_signed_date)),
        ("Legacy Stored Warranty End Date", lambda s: _date(s.warranty_end_date)),
        ("Completion Warranty End Date", lambda s: _date(s.computed_post_svc_warranty_end_date)),
        ("Maintenance Start Date", lambda s: _date(s.managed_support_start_date)),
        ("Maintenance End Date", lambda s: _date(s.managed_support_end_date)),
        ("PO / PNL Date", lambda s: _date(s.po_pnl_date)),
        ("PO Remarks", lambda s: s.revenue_remarks or ""),
        (
            "Billing Type",
            lambda s: (
                {"internal": "Internal", "invoiced": "Invoiced", "unbilled": "Unbilled"}.get(
                    s.revenue_source, s.revenue_source or ""
                )
            ),
        ),
        ("Billed Date", lambda s: _date(s.revenue_date)),
        ("Billing Reference", lambda s: (s.revenue_reference_no or "").upper()),
        (
            "Billing Status",
            lambda s: (
                s.get_revenue_status_display()
                if s.revenue_status
                else ("Billed" if s.revenue_date else "Not Yet Billed")
            ),
        ),
        ("Billing Remarks", lambda s: s.revenue_overview or ""),
    ]


def _get_sqr_esg_export_columns() -> list[SqrExportColumn]:
    """Define the ESG tracker columns with their original fallbacks."""

    def _date_short(d: date | None) -> str:
        """Display an unpadded date for the tracker schedule."""
        return f"{d.month}/{d.day}/{str(d.year)[-2:]}" if d else ""

    def _date_medium_safe(d: date | None) -> str:
        """Display a billing date with its abbreviated month name."""
        return f"{d.day}-{d.strftime('%b')}-{str(d.year)[-2:]}" if d else ""

    def _project_name(s: SqrSubmission) -> str:
        """Combine the customer and project names when both are present."""
        if s.customer_name and s.project_title:
            return f"{s.customer_name} - {s.project_title}"
        return s.customer_name or s.project_title or ""

    def _sse1(s: SqrSubmission) -> str:
        """Prefer the assigned SSE to the originating engineer."""
        return _name(s.assigned_sse) or _name(s.engineer)

    def _pm(s: SqrSubmission) -> str:
        """Prefer the assigned PM to the ESG reviewer."""
        return _name(s.assigned_pm) or _name(s.pm_esg_reviewer)

    def _project_flag(s: SqrSubmission) -> str:
        """Mark submissions with ownership or a recorded start date."""
        return "Yes" if s.assigned_pm or s.pm_esg_reviewer or s.delivery_start_date else "No"

    def _status(s: SqrSubmission) -> str:
        """Derive the ESG tracker status using its established precedence."""
        if s.overall_status == "on_hold":
            return "ON HOLD"
        if s.overall_status == "completed" or s.delivery_health == "completed":
            return "COMPLETED"
        if s.delivery_start_date or s.overall_status == "in_progress" or s.proposal_status == "closed_won":
            return "ONGOING"
        if s.status == "for_revision":
            return "FOR REVISION"
        return "NOT STARTED"

    def _start_finish_dates(s: SqrSubmission) -> str:
        """Show the actual or planned schedule, marking missing dates TBD."""
        if not (s.delivery_start_date or s.delivery_target_finish_date or s.delivery_actual_finish_date):
            return ""
        start = _date_short(s.delivery_start_date) or "TBD"
        finish = _date_short(s.delivery_actual_finish_date) or _date_short(s.delivery_target_finish_date) or "TBD"
        return f"{start}-{finish}"

    def _money(value: Decimal | None) -> float | str:
        """Write prices as numbers and unset prices as blank cells."""
        return float(value) if value is not None else ""

    def _total_revenue(s: SqrSubmission) -> float | str:
        """Use the calculation when available, otherwise the quoted total."""
        return _money(s.computed_total_price if s.computed_total_price is not None else s.quotation_total_price)

    def _revenue_source(s: SqrSubmission) -> str:
        """Label recorded billing sources without exposing unknown codes."""
        if s.revenue_source == "internal":
            return "Internal"
        if s.revenue_source == "invoiced":
            return "Invoiced"
        if s.revenue_source == "unbilled":
            return "Unbilled"
        return ""

    return [
        ("Account Name", lambda s: s.customer_name or ""),
        ("Service Description", lambda s: s.project_title or ""),
        ("Project Name", _project_name),
        ("SSE1", _sse1),
        ("SSE2", lambda s: ""),
        ("SSE3", lambda s: ""),
        ("Project?", _project_flag),
        ("PM", _pm),
        ("Status", _status),
        ("Start and Finish Dates", _start_finish_dates),
        ("% Complete", lambda s: s.delivery_progress if s.delivery_progress is not None else ""),
        ("Key Updates", lambda s: s.key_updates_risks or ""),
        ("Active Risks/Issues", lambda s: s.key_updates_risks or ""),
        ("Scope Status", lambda s: s.project_details or ""),
        ("Schedule Status", lambda s: _date_short(s.delivery_target_finish_date)),
        ("Budget Status", lambda s: ""),
        ("Folder", lambda s: s.sqr_folder_link or ""),
        ("Quoted SSE hrs", lambda s: float(s.sse_manhrs) if s.sse_manhrs is not None else ""),
        ("Billed SSE hrs", lambda s: ""),
        ("Quoted PM hrs", lambda s: float(s.effective_pm_manhrs) if s.effective_pm_manhrs is not None else ""),
        ("Billed PM hrs", lambda s: ""),
        ("Project Management", lambda s: _money(s.effective_pm_amount)),
        ("Deployment", lambda s: _money(s.sse_amount)),
        ("On-Call", lambda s: _money(s.managed_support_amount)),
        ("Total Revenue", _total_revenue),
        ("Revenue?", lambda s: "Yes" if s.revenue_date or s.revenue_declaration == "declared" else "No"),
        ("SI Type", _revenue_source),
        ("SI Reference", lambda s: (s.revenue_reference_no or "").upper()),
        ("SI Date", lambda s: _date_medium_safe(s.revenue_date)),
        ("Remarks", lambda s: s.revenue_remarks or s.remarks or ""),
        ("Action", lambda s: "History"),
    ]


def _get_sqr_performance_export_columns() -> list[SqrExportColumn]:
    """Define the performance columns using the saved SQR assessments."""
    from hub.services.sqr_performance import build_assessment_snapshot

    def _assessment(s: SqrSubmission, field: str) -> str:
        """Read the display label of a single assessment dimension."""
        return build_assessment_snapshot(s)["indicators"][field]["label"]

    return [
        ("SQR ID", lambda s: s.reference_code or ""),
        ("Description", lambda s: s.project_title or ""),
        ("Project Manager", lambda s: _name(s.assigned_pm or s.pm_esg_reviewer)),
        ("Start Date", lambda s: _date(s.delivery_start_date)),
        ("Finish Date", lambda s: _date(s.delivery_actual_finish_date)),
        ("Status", lambda s: s.get_overall_status_display() or "Not set"),
        ("% Complete", lambda s: s.delivery_progress if s.delivery_progress is not None else ""),
        ("Key Updates / Risks", lambda s: s.key_updates_risks or ""),
        ("Folder link", lambda s: s.sqr_folder_link or ""),
        ("Schedule Performance", lambda s: _assessment(s, "schedule_performance")),
        ("Budget/Effort Performance", lambda s: _assessment(s, "budget_effort_performance")),
        ("Quality Performance", lambda s: _assessment(s, "quality_performance")),
        ("Overall Performance", lambda s: build_assessment_snapshot(s)["overall_label"]),
        ("Assessed Dimensions", lambda s: build_assessment_snapshot(s)["assessed_count"]),
        ("Last Updated Date", lambda s: s.updated_at.strftime("%Y-%m-%d %H:%M") if s.updated_at else ""),
    ]


def build_sqr_export_workbook(submissions: Iterable[SqrSubmission], report_type: str) -> tuple[str, BytesIO]:
    """Render the selected workbook and return its filename prefix and rewound buffer."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    title, filename_prefix, get_columns = {
        "sqr": ("SQR", "sqr-export", _get_sqr_export_columns),
        "esg": ("ESG Performance Tracker", "esg-performance-tracker", _get_sqr_esg_export_columns),
        "performance": (
            "Project Performance Tracking", "project-performance-tracking", _get_sqr_performance_export_columns,
        ),
    }[report_type]

    wb = Workbook()
    ws = wb.active
    ws.title = title

    hdr_font = Font(bold=True, color="FFFFFF", size=9)
    hdr_fill = PatternFill("solid", fgColor="1A1F2E")
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell_align = Alignment(vertical="center")
    thin = Side(style="thin", color="CCCCCC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    columns = get_columns()

    ws.row_dimensions[1].height = 36
    for col_idx, (label, _) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = hdr_align
        cell.border = border

    for row_idx, submission in enumerate(submissions, start=2):
        ws.row_dimensions[row_idx].height = 18
        for col_idx, (_, extractor) in enumerate(columns, start=1):
            # Preserve the blank cell fallback when a legacy value cannot be formatted.
            try:
                val = extractor(submission)
            except Exception:  # noqa: BLE001
                val = ""
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            if isinstance(val, str):
                cell.data_type = "s"
            cell.alignment = cell_align
            cell.border = border

    for col_idx, (label, _) in enumerate(columns, start=1):
        col_letter = get_column_letter(col_idx)
        max_len = len(label)
        for row_idx in range(2, ws.max_row + 1):
            value = ws.cell(row=row_idx, column=col_idx).value
            if value:
                max_len = max(max_len, min(len(str(value)), 40))
        ws.column_dimensions[col_letter].width = max_len + 2

    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return filename_prefix, buf
