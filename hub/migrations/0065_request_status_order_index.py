from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = (('hub', '0064_backfill_sqr_schedule_performance'),)

    operations = (
        AddIndexConcurrently(
            model_name='request',
            index=models.Index(
                'status', models.F('created_at').desc(nulls_last=True), models.F('id').desc(),
                condition=models.Q(is_deleted=False), name='req_active_status_new_idx',
            ),
        ),
    )
