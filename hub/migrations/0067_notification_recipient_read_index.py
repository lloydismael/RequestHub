from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = (('hub', '0066_request_sla_attention_index'),)

    operations = (
        AddIndexConcurrently(
            model_name='notification',
            index=models.Index(fields=['recipient', 'is_read', '-created_at'], name='notif_rec_read_new_idx'),
        ),
    )
