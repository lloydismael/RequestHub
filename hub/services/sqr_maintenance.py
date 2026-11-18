from datetime import date

from django.core.exceptions import ValidationError


def validate_manual_maintenance_dates(start: date | None, end: date | None) -> None:
    if end is not None and start is None:
        raise ValidationError({"managed_support_start_date": "Maintenance Start Date is required when an End Date is set."})
    if start is not None and end is not None and end < start:
        raise ValidationError({"managed_support_end_date": "Maintenance End Date cannot be before Start Date."})
