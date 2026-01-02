# Generated manually

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('team', '0009_add_form_link_model'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='FormLink',
            new_name='TeamLink',
        ),
    ]
