from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = (('hub', '0065_request_status_order_index'),)

    operations = (
        AddIndexConcurrently(
            model_name='request',
            index=models.Index(
                fields=['due_date', 'priority', 'reference_code'],
                condition=models.Q(is_deleted=False, status='ongoing', due_date__isnull=False),
                name='req_ongoing_sla_idx',
            ),
        ),
    )
