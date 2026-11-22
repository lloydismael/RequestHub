from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = (('hub', '0067_notification_recipient_read_index'),)

    operations = (
        AddIndexConcurrently(
            model_name='sqrsubmission',
            index=models.Index(fields=['-created_at', '-id'], name='sqr_new_idx'),
        ),
    )
