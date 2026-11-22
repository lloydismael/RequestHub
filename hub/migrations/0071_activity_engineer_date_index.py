from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = (('hub', '0070_sqr_reviewer_order_index'),)

    operations = (
        AddIndexConcurrently(
            model_name='engineeractivitylog',
            index=models.Index(fields=['engineer', '-request_date', '-created_at'], name='activity_eng_date_new_idx'),
        ),
    )
