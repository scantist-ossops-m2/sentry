# Generated by Django 5.0.2 on 2024-02-16 04:58

from django.db import migrations

from sentry.new_migrations.migrations import CheckedMigration
from sentry.utils.query import RangeQuerySetWrapperWithProgressBarApprox


def rename_priority_sort_to_trends(apps, schema_editor):
    # We need to use apps.get_model to ensure we get the correct model for the
    # migration. This is especially important in the case of a renamed model.
    SavedSearch = apps.get_model("sentry", "SavedSearch")
    for search in RangeQuerySetWrapperWithProgressBarApprox(SavedSearch.objects.all()):
        if search.sort == "priority":
            search.sort = "trends"
            search.save()


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
    is_post_deployment = True
    dependencies = [
        ("sentry", "0653_apitoken_add_token_type"),
    ]

    operations = [
        migrations.RunPython(
            rename_priority_sort_to_trends,
            migrations.RunPython.noop,
            hints={"tables": ["sentry_savedsearch"]},
        ),
    ]
