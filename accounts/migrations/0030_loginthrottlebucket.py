from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0029_christmas_default_database_default")]

    operations = [
        migrations.CreateModel(
            name="LoginThrottleBucket",
            fields=[
                ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("source_attempts", models.PositiveSmallIntegerField(default=0)),
                ("reservations", models.JSONField(default=list)),
                ("failed_tokens", models.JSONField(default=list)),
            ],
        ),
    ]
