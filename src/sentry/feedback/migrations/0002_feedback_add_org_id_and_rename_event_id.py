# Generated by Django 3.2.20 on 2023-09-13 21:50

from django.db import migrations, models

import sentry.db.models.fields.bounded
from sentry.new_migrations.migrations import CheckedMigration


class Migration(CheckedMigration):
    # This flag is used to mark that a migration shouldn't be automatically run in production. For
    # the most part, this should only be used for operations where it's safe to run the migration
    # after your code has deployed. So this should not be used for most operations that alter the
    # schema of a table.
    # Here are some things that make sense to mark as post deployment:
    # - Large data migrations. Typically we want these to be run manually by ops so that they can
    #   be monitored and not block the deploy for a long period of time while they run.
    # - Adding indexes to large tables. Since this can take a long time, we'd generally prefer to
    #   have ops run this and not block the deploy. Note that while adding an index is a schema
    #   change, it's completely safe to run the operation after the code has deployed.
    is_post_deployment = False
    checked = False

    dependencies = [
        ("feedback", "0001_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedback",
            name="organization_id",
            field=sentry.db.models.fields.bounded.BoundedBigIntegerField(db_index=True, default=1),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="feedback",
            name="replay_id",
            field=models.CharField(db_index=True, max_length=100, null=True),
        ),
    ]
