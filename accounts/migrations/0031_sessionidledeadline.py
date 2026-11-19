from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0030_loginthrottlebucket"),
        ("sessions", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SessionIdleDeadline",
            fields=[
                ("session", models.OneToOneField(
                    on_delete=models.DB_CASCADE, primary_key=True,
                    related_name="idle_timeout", serialize=False, to="sessions.session",
                )),
                ("deadline", models.DateTimeField()),
            ],
        ),
    ]
