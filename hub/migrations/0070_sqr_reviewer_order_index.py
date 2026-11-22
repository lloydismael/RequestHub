from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = (('hub', '0069_sqr_engineer_order_index'),)

    operations = (
        AddIndexConcurrently(
            model_name='sqrsubmission',
            index=models.Index(fields=['pm_esg_reviewer', '-created_at', '-id'], name='sqr_reviewer_new_idx'),
        ),
    )
