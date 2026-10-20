from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('hub', '0048_form_editor'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sqrprojectperformance',
            name='budget_health',
            field=models.CharField(choices=[('ok', 'OK'), ('at_risk', 'At Risk'), ('over', 'Overdue')], default='ok', max_length=20),
        ),
        migrations.AlterField(
            model_name='sqrprojectperformance',
            name='schedule_health',
            field=models.CharField(choices=[('ok', 'OK'), ('at_risk', 'At Risk'), ('over', 'Overdue')], default='ok', max_length=20),
        ),
        migrations.AlterField(
            model_name='sqrprojectperformance',
            name='scope_health',
            field=models.CharField(choices=[('ok', 'OK'), ('at_risk', 'At Risk'), ('over', 'Overdue')], default='ok', max_length=20),
        ),
    ]