from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = (('hub', '0068_sqr_creation_order_index'),)

    operations = (
        AddIndexConcurrently(
            model_name='sqrsubmission',
            index=models.Index(fields=['engineer', '-created_at', '-id'], name='sqr_engineer_new_idx'),
        ),
    )
