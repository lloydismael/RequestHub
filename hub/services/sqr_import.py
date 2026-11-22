"""SQR workbook parsing, atomic replacement, and import job progress."""

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from logging import getLogger
from typing import Any, TypedDict

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from openpyxl.worksheet.worksheet import Worksheet

from accounts.models import User
from hub.models import MANILA_TZ, Request, SqrSubmission
from hub.services import sqr_workflow
from hub.services.sqr_exports import _get_sqr_export_columns

logger = getLogger("hub.views")


class _ImportRow(TypedDict):
    """The in-memory row and values parsed from a worksheet."""

    instance: SqrSubmission
    data: dict[str, Any]


def _normalize_import_text(value: object) -> str:
    """Normalize worksheet labels and lookup keys without changing display text."""
    return " ".join(
        str(value or "")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("_", " ")
        .split()
    ).casefold()


def _build_choice_import_map(choices: Iterable[tuple[str, object]]) -> dict[str, str]:
    """Accept either the stored value or display label for each choice."""
    mapping: dict[str, str] = {}
    for value, label in choices:
        mapping[_normalize_import_text(value)] = value
        mapping[_normalize_import_text(label)] = value
    return mapping


class SqrWorkbookImport:
    """Run the workbook job independently of its HTTP entry points."""

    EXPECTED_HEADERS = [label for label, _ in _get_sqr_export_columns()]
    LEGACY_POSITIONAL_HEADERS = [
        "SQR Date", "SQR ID", "Account Name", "Service Description", "Scope of Services", "RQ ID",
        "Group Name", "Account Manager", "Requester Name", "Approver Name", "SQR Doc. Ref. Link",
        "SSE Man-hrs", "SSE Amount", "PM Man-hrs", "PM Amount", "Maintenance Amt.", "Discount Rate (%)",
        "Discount Amount", "Total Price", "Remarks", "SQR Status", "Hold Reason", "Approval Date",
        "Validity Due Date", "Proposal Status", "Assigned PM", "Assigned SSE", "Start Date",
        "Target Finish Date", "Overall Status", "Health Status", "Overall Progress %", "Key Updates / Risks",
        "Actual Finish Date", "Completion Signed Date", "Completion Warranty End Date",
        "Maintenance Start Date", "Maintenance End Date", "PO / PNL Date", "PO Remarks", "Billing Type",
        "Billed Date", "Billing Reference", "Billing Status", "Billing Remarks",
    ]
    STATUS_MAP = _build_choice_import_map(SqrSubmission.Status.choices)
    PROPOSAL_STATUS_MAP = _build_choice_import_map(SqrSubmission.ProposalStatus.choices)
    OVERALL_STATUS_MAP = _build_choice_import_map(SqrSubmission.OverallStatus.choices)
    DELIVERY_HEALTH_MAP = _build_choice_import_map(SqrSubmission.DeliveryHealth.choices)
    REVENUE_DECLARATION_MAP = _build_choice_import_map(SqrSubmission.RevenueDeclaration.choices)
    IMPORT_STATUS_TIMEOUT = 60 * 60
    MANAGED_SCOPES = sqr_workflow.MANAGED_SCOPES

    @classmethod
    def _status_cache_key(cls, user_id: int, job_id: str) -> str:
        """Scope cached progress to the user and import job."""
        return f"sqr-import:{user_id}:{job_id}"

    @classmethod
    def _set_status(cls, user: User | int, job_id: str, payload: dict[str, Any]) -> None:
        """Store the initial import progress with the job's expiration."""
        user_id = user.pk if hasattr(user, "pk") else user
        cache.set(cls._status_cache_key(user_id, job_id), payload, timeout=cls.IMPORT_STATUS_TIMEOUT)

    @classmethod
    def _get_status(cls, user: User | int, job_id: str) -> dict[str, Any] | None:
        """Read this user's job progress, if it has not expired."""
        user_id = user.pk if hasattr(user, "pk") else user
        return cache.get(cls._status_cache_key(user_id, job_id))

    @classmethod
    def _update_status(cls, user_id: int, job_id: str, **changes: Any) -> None:
        """Merge progress changes and refresh the job's expiration."""
        current = cache.get(cls._status_cache_key(user_id, job_id), {}) or {}
        current.update(changes)
        cache.set(cls._status_cache_key(user_id, job_id), current, timeout=cls.IMPORT_STATUS_TIMEOUT)

    def run_import_job(
        self, *, job_id: str, user_id: int, file_bytes: bytes, close_connections: Callable[[], None],
    ) -> None:
        """Parse, atomically replace eligible rows, and record the job outcome."""
        close_connections()
        try:
            actor = User.objects.get(pk=user_id)
            row_payloads = self._parse_import_file(file_bytes=file_bytes, actor=actor, user_id=user_id, job_id=job_id)
            total = len(row_payloads)
            if total <= 0:
                raise ValueError("Import failed: the file does not contain any data rows.")

            self._update_status(
                user_id,
                job_id,
                state="running",
                total=total,
                processed=0,
                created=0,
                percent=5,
                message="Replacing current SQR table…",
            )

            created_count = 0
            with transaction.atomic():
                # Delete only the locked snapshot; a newly inserted row was not checked.
                existing_ids = list(SqrSubmission.objects.order_by("pk").select_for_update().values_list("pk", flat=True))
                existing = SqrSubmission.objects.filter(pk__in=existing_ids)
                if not User.objects.filter(pk=actor.pk, is_superuser=True).exists() and existing.filter(
                    SqrSubmission.protected_from_deletion(),
                ).exists():
                    raise PermissionDenied("Only Django superusers can replace protected SQRs.")
                existing.delete()
                for index, payload in enumerate(row_payloads, start=1):
                    submission = SqrSubmission()
                    data = payload["data"].copy()
                    created_at_value = data.pop("created_at", None)
                    reference_code = data.pop("reference_code", "")
                    year = data.pop("year", None)
                    sequence_number = data.pop("sequence_number", None)

                    for field_name, field_value in data.items():
                        setattr(submission, field_name, field_value)

                    if year:
                        submission.year = year
                    if sequence_number:
                        submission.sequence_number = sequence_number
                    if reference_code:
                        submission.reference_code = reference_code

                    submission.save()

                    updates = {}
                    if created_at_value:
                        updates["created_at"] = created_at_value
                    if reference_code:
                        updates["reference_code"] = reference_code
                    if sequence_number:
                        updates["sequence_number"] = sequence_number
                    if year:
                        updates["year"] = year
                    if updates:
                        SqrSubmission.objects.filter(pk=submission.pk).update(**updates)

                    created_count += 1
                    percent = min(100, 5 + int((index / total) * 95))
                    self._update_status(
                        user_id,
                        job_id,
                        state="running",
                        processed=index,
                        created=created_count,
                        total=total,
                        percent=percent,
                        message=f"Imported {index} of {total} row(s)…",
                    )

            self._update_status(
                user_id,
                job_id,
                state="completed",
                processed=created_count,
                created=created_count,
                total=created_count,
                percent=100,
                message="Import completed successfully.",
                result={
                    "created": created_count,
                    "updated": 0,
                },
            )
        except Exception as exc:
            logger.exception("SQR import job failed", extra={"job_id": job_id, "user_id": user_id})
            self._update_status(
                user_id,
                job_id,
                state="failed",
                percent=0,
                message=str(exc) or "Import failed.",
                errors=[str(exc) or "Import failed."],
            )
        finally:
            close_connections()

    def _parse_import_file(
        self, *, file_bytes: bytes, actor: User, user_id: int, job_id: str,
    ) -> list[_ImportRow]:
        """Read and validate all nonblank worksheet rows before any replacement."""
        import io
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
            worksheet = workbook.active
        except Exception as exc:
            raise ValueError("The selected file could not be read. Please upload a valid Excel file.") from exc

        request_map = {
            _normalize_import_text(item.reference_code): item
            for item in Request.objects.select_related("account", "engineer").all()
            if item.reference_code
        }
        pm_users = list(User.objects.filter(role=User.Roles.PM_ESG).order_by("first_name", "last_name", "username"))
        engineer_users = list(User.objects.filter(role__in=User.ENGINEER_ACCESS_ROLES).order_by("first_name", "last_name", "username"))
        pm_lookup = self._build_user_lookup(pm_users)
        engineer_lookup = self._build_user_lookup(engineer_users)

        start_row = self._detect_data_start_row(worksheet)
        legacy_headers = [header for header in self.LEGACY_POSITIONAL_HEADERS if header not in {"Remarks", "Hold Reason"}]
        if start_row == 2:
            canonical_headers = {_normalize_import_text(header): header for header in self.EXPECTED_HEADERS}
            header_values = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), [])
            headers = [canonical_headers.get(_normalize_import_text(value), self._stringify(value)) for value in header_values]
        else:
            headers = legacy_headers if worksheet.max_column <= len(legacy_headers) else self.LEGACY_POSITIONAL_HEADERS
        candidate_rows = list(
            worksheet.iter_rows(
                min_row=start_row,
                max_row=worksheet.max_row,
                max_col=max(worksheet.max_column, len(headers)),
                values_only=True,
            )
        )
        non_blank_rows = [row for row in candidate_rows if not self._is_blank_row(row)]

        self._update_status(
            user_id,
            job_id,
            state="running",
            processed=0,
            total=len(non_blank_rows),
            percent=1,
            message="Reading Excel rows…",
            errors=[],
        )

        row_payloads = []
        row_errors = []
        total_rows = max(len(non_blank_rows), 1)
        processed_rows = 0
        for excel_row_number, row in enumerate(candidate_rows, start=start_row):
            if self._is_blank_row(row):
                continue
            row_data = {
                header: row[idx] if idx < len(row) else None
                for idx, header in enumerate(headers) if header
            }
            try:
                row_payloads.append(
                    self._prepare_row_payload(
                        row_data=row_data,
                        request_map=request_map,
                        pm_lookup=pm_lookup,
                        engineer_lookup=engineer_lookup,
                        actor=actor,
                    )
                )
            except ValueError as exc:
                row_errors.append(f"Row {excel_row_number}: {exc}")

            processed_rows += 1
            self._update_status(
                user_id,
                job_id,
                state="running",
                processed=processed_rows,
                total=len(non_blank_rows),
                percent=min(45, int((processed_rows / total_rows) * 45)),
                message=f"Validated {processed_rows} of {len(non_blank_rows)} row(s)…",
            )

        if not row_payloads and not row_errors:
            raise ValueError("Import failed: the file does not contain any data rows.")

        if row_errors:
            preview_errors = row_errors[:5]
            suffix = f" ...and {len(row_errors) - 5} more error(s)." if len(row_errors) > 5 else ""
            raise ValueError(f"Import failed with {len(row_errors)} row error(s). {' | '.join(preview_errors)}{suffix}")

        return row_payloads

    def _detect_data_start_row(self, worksheet: Worksheet) -> int:
        """Distinguish a header row from headerless legacy data."""
        first_row = [self._stringify(value) for value in next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), [])]
        normalized_first_row = [_normalize_import_text(value) for value in first_row]
        expected_prefix = [_normalize_import_text(label) for label in self.EXPECTED_HEADERS[:3]]
        if normalized_first_row[:3] == expected_prefix:
            return 2
        return 1

    @staticmethod
    def _stringify(value: object) -> str:
        """Coerce a cell value to the importer's trimmed display text."""
        return str(value or "").strip()

    @staticmethod
    def _is_blank_row(row: Iterable[object]) -> bool:
        """Check whether a worksheet row contains any importable text."""
        return all(str(value or "").strip() == "" for value in row)

    @staticmethod
    def _build_user_lookup(users: Iterable[User]) -> dict[str, list[User]]:
        """Index users by username and name while preserving ambiguous matches."""
        lookup: dict[str, list[User]] = {}
        for user in users:
            keys = {
                _normalize_import_text(user.username),
                _normalize_import_text(user.get_full_name()),
                _normalize_import_text(f"{user.first_name} {user.last_name}"),
            }
            for key in {item for item in keys if item}:
                lookup.setdefault(key, []).append(user)
        return lookup

    @staticmethod
    def _first_lookup_user(lookup: Mapping[str, list[User]]) -> User | None:
        """Return the first available fallback assignee from the lookup."""
        for users in lookup.values():
            if users:
                return users[0]
        return None

    def _parse_user_lenient(self, raw_value: object, lookup: Mapping[str, list[User]]) -> User | None:
        """Resolve only unambiguous, nonblank user references."""
        normalized = _normalize_import_text(raw_value)
        if not normalized:
            return None
        matches = lookup.get(normalized, [])
        if not matches:
            return None
        unique_matches = {user.pk: user for user in matches}
        if len(unique_matches) > 1:
            return None
        return next(iter(unique_matches.values()))

    @staticmethod
    def _parse_choice_lenient(
        raw_value: object, mapping: Mapping[str, str], default_blank: bool = True,
    ) -> str | None:
        """Map a known choice or fall back to the requested empty value."""
        normalized = _normalize_import_text(raw_value)
        if not normalized:
            return "" if default_blank else None
        return mapping.get(normalized, "" if default_blank else None)

    @staticmethod
    def _parse_decimal(raw_value: object, label: str) -> Decimal | None:
        """Parse a decimal cell or report an invalid value with its column label."""
        if raw_value in (None, ""):
            return None
        text_value = str(raw_value).replace(",", "").strip()
        if not text_value:
            return None
        try:
            return Decimal(text_value)
        except Exception as exc:
            raise ValueError(f"Invalid {label}: {raw_value}") from exc

    @classmethod
    def _parse_decimal_lenient(cls, raw_value: object, label: str) -> Decimal | None:
        """Parse an optional decimal, treating an invalid value as missing."""
        try:
            return cls._parse_decimal(raw_value, label)
        except ValueError:
            return None

    @staticmethod
    def _parse_int(raw_value: object, label: str) -> int | None:
        """Convert a numeric cell to an integer using the legacy truncation rule."""
        if raw_value in (None, ""):
            return None
        try:
            return int(Decimal(str(raw_value)))
        except Exception as exc:
            raise ValueError(f"Invalid {label}: {raw_value}") from exc

    @classmethod
    def _parse_int_lenient(cls, raw_value: object, label: str) -> int | None:
        """Parse an optional integer, treating an invalid value as missing."""
        try:
            return cls._parse_int(raw_value, label)
        except ValueError:
            return None

    @staticmethod
    def _parse_date(raw_value: object, label: str) -> date | None:
        """Accept an Excel date or a supported date string for the named column."""
        if raw_value in (None, ""):
            return None
        if isinstance(raw_value, datetime):
            return raw_value.date()
        if isinstance(raw_value, date):
            return raw_value
        text_value = str(raw_value).strip()
        if not text_value:
            return None
        try:
            return date.fromisoformat(text_value)
        except Exception:
            pass
        for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text_value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Invalid {label}: {raw_value}")

    @classmethod
    def _parse_date_lenient(cls, raw_value: object, label: str) -> date | None:
        """Parse an optional date, treating an invalid value as missing."""
        try:
            return cls._parse_date(raw_value, label)
        except ValueError:
            return None

    @staticmethod
    def _to_datetime(value: date | None) -> datetime | None:
        """Interpret a worksheet date as midnight in Manila."""
        if not value:
            return None
        return timezone.make_aware(datetime.combine(value, datetime.min.time()), MANILA_TZ)

    def _prepare_row_payload(
        self, *, row_data: Mapping[str, object], request_map: Mapping[str, Request],
        pm_lookup: Mapping[str, list[User]], engineer_lookup: Mapping[str, list[User]], actor: User,
    ) -> _ImportRow:
        """Build and validate a new submission and its worksheet field values."""
        sqr_id = self._stringify(row_data.get("SQR ID"))
        request_code = self._stringify(row_data.get("RQ ID"))
        linked_request = request_map.get(_normalize_import_text(request_code)) if request_code else None

        default_engineer = getattr(linked_request, "engineer", None) or self._first_lookup_user(engineer_lookup) or actor
        default_pm = self._first_lookup_user(pm_lookup) or actor

        engineer = self._parse_user_lenient(row_data.get("Requester Name"), engineer_lookup)
        pm_reviewer = self._parse_user_lenient(row_data.get("Approver Name"), pm_lookup)
        assigned_pm = self._parse_user_lenient(row_data.get("Assigned PM"), pm_lookup)
        assigned_sse = self._parse_user_lenient(row_data.get("Assigned SSE"), engineer_lookup)

        status = self._parse_choice_lenient(row_data.get("SQR Status"), self.STATUS_MAP) or SqrSubmission.Status.FOR_PROCESSING
        hold_reason = self._stringify(row_data.get("Hold Reason"))
        if status == SqrSubmission.Status.ON_HOLD and not hold_reason:
            raise ValueError("Hold Reason is required when status is On Hold.")
        proposal_status = self._parse_choice_lenient(row_data.get("Proposal Status"), self.PROPOSAL_STATUS_MAP)
        overall_status = self._parse_choice_lenient(row_data.get("Overall Status"), self.OVERALL_STATUS_MAP)
        delivery_health = self._parse_choice_lenient(
            row_data.get("Legacy Health Status", row_data.get("Health Status")), self.DELIVERY_HEALTH_MAP)
        assessments = {}
        for field, label, choices in (
            ("schedule_performance", "Schedule Performance", SqrSubmission.SchedulePerformance.choices),
            ("budget_effort_performance", "Budget/Effort Performance", SqrSubmission.BudgetEffortPerformance.choices),
            ("quality_performance", "Quality Performance", SqrSubmission.QualityPerformance.choices),
        ):
            raw = self._stringify(row_data.get(label))
            choice_map = _build_choice_import_map(choices)
            value = choice_map.get(_normalize_import_text(raw)) if raw else ""
            if raw and value is None:
                raise ValueError(f"Invalid {label}.")
            assessments[field] = value
        manual_start = self._parse_date(row_data.get("Maintenance Start Date"), "Maintenance Start Date")
        manual_end = self._parse_date(row_data.get("Maintenance End Date"), "Maintenance End Date")
        from hub.services.sqr_maintenance import validate_manual_maintenance_dates
        try:
            validate_manual_maintenance_dates(manual_start, manual_end)
        except ValidationError as exc:
            raise ValueError("Invalid Maintenance date pair.") from exc
        revenue_declaration = self._parse_choice_lenient(row_data.get("Revenue Declaration"), self.REVENUE_DECLARATION_MAP)

        customer_name = self._stringify(row_data.get("Account Name"))
        project_title = self._stringify(row_data.get("Service Description"))
        project_details = self._stringify(row_data.get("Scope of Services"))
        sqr_folder_link = self._stringify(row_data.get("SQR Doc. Ref. Link"))

        sse_manhrs = self._parse_decimal_lenient(row_data.get("SSE Man-hrs"), "SSE Man-hrs")
        pm_manhrs = self._parse_decimal_lenient(row_data.get("PM Man-hrs"), "PM Man-hrs")
        discount_rate = self._parse_int_lenient(row_data.get("Discount Rate (%)"), "Discount Rate (%)")
        discount_rate = 0 if discount_rate is None else max(0, min(100, discount_rate))
        delivery_progress = self._parse_int_lenient(row_data.get("Overall Progress %"), "Overall Progress %")
        if delivery_progress is not None:
            delivery_progress = max(0, min(100, delivery_progress))

        reviewed_date_value = row_data.get("Approval Date", row_data.get("Reviewed Date"))
        approval_date = self._parse_date_lenient(reviewed_date_value, "Approval Date")
        validity_due_date = self._parse_date_lenient(row_data.get("Validity Due Date"), "Validity Due Date")
        created_date = self._parse_date_lenient(row_data.get("SQR Date"), "SQR Date")

        instance = SqrSubmission()
        reference_meta = self._parse_reference_code(sqr_id)
        data = {
            "linked_request": linked_request,
            "engineer": engineer or default_engineer,
            "pm_esg_reviewer": pm_reviewer or default_pm,
            "customer_name": customer_name or "Legacy Imported Record",
            "customer_company": self._stringify(row_data.get("Group Name")),
            "customer_contact": self._stringify(row_data.get("Account Manager")),
            "project_title": project_title or "Legacy Imported Service",
            "project_details": project_details or "Imported legacy data with incomplete details.",
            "sse_manhrs": sse_manhrs,
            "sqr_folder_link": sqr_folder_link,
            "discount_rate": discount_rate,
            "pm_manhrs": pm_manhrs,
            "proposal_status": proposal_status,
            "po_pnl_date": self._parse_date_lenient(row_data.get("PO / PNL Date"), "PO / PNL Date"),
            "assigned_pm": assigned_pm or pm_reviewer or default_pm,
            "assigned_sse": assigned_sse or engineer or default_engineer,
            "delivery_start_date": self._parse_date_lenient(row_data.get("Start Date"), "Start Date"),
            "delivery_target_finish_date": self._parse_date_lenient(row_data.get("Legacy Target Finish Date", row_data.get("Target Finish Date")), "Legacy Target Finish Date"),
            **assessments,
            "managed_support_start_date": manual_start,
            "managed_support_end_date": manual_end,
            "warranty_end_date": self._parse_date_lenient(row_data.get("Legacy Stored Warranty End Date"), "Legacy Stored Warranty End Date"),
            "overall_status": overall_status,
            "delivery_health": delivery_health,
            "delivery_progress": delivery_progress,
            "key_updates_risks": self._stringify(row_data.get("Key Updates / Risks")),
            "delivery_actual_finish_date": self._parse_date(row_data["Finish Date"], "Finish Date") if "Finish Date" in row_data else self._parse_date_lenient(row_data.get("Actual Finish Date"), "Actual Finish Date"),
            "delivery_completion_signed_date": self._parse_date_lenient(row_data.get("Legacy Completion Signed Date", row_data.get("Completion Signed Date")), "Legacy Completion Signed Date"),
            "revenue_date": self._parse_date_lenient(
                row_data.get("Billed Date")
                or row_data.get("Date")
                or row_data.get("SI / Revenue Date"),
                "Billed Date",
            ),
            "revenue_source": self._stringify(
                row_data.get("Billing Type") or row_data.get("Source")
            ),
            "revenue_reference_no": self._stringify(
                row_data.get("Billing Reference") or row_data.get("Reference No.")
            ),
            "revenue_status": self._stringify(
                row_data.get("Billing Status") or row_data.get("Revenue Status")
            ),
            "revenue_remarks": self._stringify(
                row_data.get("PO Remarks") if "PO Remarks" in row_data else row_data.get("Remarks")
            ),
            "revenue_overview": self._stringify(
                row_data.get("Billing Remarks") or row_data.get("Revenue Declaration")
            ),
            "revenue_declaration": revenue_declaration,
            "status": status,
            "documentation_links": "",
            "remarks": self._stringify(row_data.get("Remarks")) if "PO Remarks" in row_data or "Hold Reason" in row_data else "",
            "hold_reason": hold_reason,
            "reference_code": sqr_id,
            "year": reference_meta["year"],
            "sequence_number": reference_meta["sequence_number"],
        }

        if status == SqrSubmission.Status.APPROVED or (status == SqrSubmission.Status.ON_HOLD and approval_date):
            reviewed_at = self._to_datetime(approval_date) or timezone.now()
            data["reviewed_at"] = reviewed_at
            data["reviewed_by"] = actor
            data["validity_due_date"] = validity_due_date or (reviewed_at.date() + timedelta(days=45))
        else:
            data["reviewed_at"] = None
            data["reviewed_by"] = None
            data["validity_due_date"] = validity_due_date

        scope = data["project_details"]
        group = data["customer_company"]
        data["managed_support_amount"] = sqr_workflow.managed_support_amount(scope, group, scopes=self.MANAGED_SCOPES)

        if created_date:
            data["created_at"] = self._to_datetime(created_date)

        return {"instance": instance, "data": data}

    @staticmethod
    def _parse_reference_code(reference_code: str) -> dict[str, int | None]:
        """Extract the year and sequence or leave the sequence for allocation."""
        cleaned = str(reference_code or "").strip()
        if not cleaned:
            current_year = timezone.now().astimezone(MANILA_TZ).year
            return {"year": current_year, "sequence_number": None}
        match = re.fullmatch(r"SQR-(\d{4})-(\d{4})", cleaned, flags=re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid SQR ID format: {reference_code}")
        return {
            "year": int(match.group(1)),
            "sequence_number": int(match.group(2)),
        }
