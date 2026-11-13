import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from hub.models import SqrSubmission
from hub.services.sqr_delivery_transition import plan_delivery_transition


class Command(BaseCommand):
    help = "Preview the legacy Delivery transition without writing submission data."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Local JSON report path.")

    def handle(self, *args, **options):
        rows, warnings, changes = [], Counter(), Counter()
        for row in SqrSubmission.objects.order_by("pk").iterator(chunk_size=500):
            planned = plan_delivery_transition(row)
            rows.append({"pk": row.pk, **planned})
            warnings.update(planned["provenance"]["warnings"])
            changes.update(planned["updates"].keys())
        report = {"rows": rows, "counts": {"total": len(rows),
                  "changed_rows": sum(bool(row["updates"]) for row in rows),
                  "fields": dict(changes), "warnings": dict(warnings)}}
        try:
            Path(options["output"]).write_text(
                json.dumps(report, cls=DjangoJSONEncoder, indent=2), encoding="utf-8")
        except OSError as exc:
            raise CommandError("Could not write the local transition report.") from exc
        self.stdout.write(f"Read-only preview: {len(rows)} submissions; {report['counts']['changed_rows']} with proposed values.")
