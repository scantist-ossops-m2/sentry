# Generated by Django 5.0.2 on 2024-03-08 16:15

from django.db import migrations
from django.db.models import Count

from sentry.new_migrations.migrations import CheckedMigration


def remove_duplicate_incidents(apps, schema_editor):
    MonitorIncident = apps.get_model("sentry", "MonitorIncident")

    monitor_envs_with_duplicate_incidents = (
        MonitorIncident.objects.values("monitor_environment_id")
        .annotate(incidents=Count("monitor_environment_id"))
        .filter(incidents__gt=1, resolving_checkin=None)
        .values_list("monitor_environment_id", flat=True)
    )

    for monitor_env_id in monitor_envs_with_duplicate_incidents:
        incidents = MonitorIncident.objects.filter(
            monitor_environment_id=monitor_env_id,
            resolving_checkin=None,
        ).order_by("-date_added")

        # Remove all but the first incident
        for incident in incidents[1:]:
            incident.delete()


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
        ("sentry", "0669_alert_rule_activation"),
    ]

    operations = [
        migrations.RunPython(
            remove_duplicate_incidents,
            migrations.RunPython.noop,
            hints={"tables": ["sentry_monitorincident"]},
        ),
    ]
