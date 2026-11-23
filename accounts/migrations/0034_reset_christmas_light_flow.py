from django.db import migrations


def reset_light_flow(apps, schema_editor):
    users = apps.get_model('accounts', 'User').objects.using(schema_editor.connection.alias)
    users.exclude(pref_christmas_light_flow=20).update(pref_christmas_light_flow=20)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0033_christmas_light_flow_default'),
    ]

    operations = [
        migrations.RunPython(reset_light_flow, migrations.RunPython.noop),
    ]
