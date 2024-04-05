# Generated by Django 5.0.3 on 2024-03-22 22:36

import logging

from django.db import migrations

from sentry.new_migrations.migrations import CheckedMigration
from sentry.utils.query import RangeQuerySetWrapper

logger = logging.getLogger(__name__)


def _backfill_alert_rule_projects(apps, schema_editor):
    QuerySubscriptions = apps.get_model("sentry", "QuerySubscription")
    AlertRuleProjects = apps.get_model("sentry", "AlertRuleProjects")

    # use RangeQuerySetWrapper to avoid loading all subscriptions into memory
    for subscription in RangeQuerySetWrapper(QuerySubscriptions.objects.all()):

        snuba_query = subscription.snuba_query
        if not snuba_query:
            logger.warning(
                "QuerySubscription found with no snuba_query",
                extra={"query_subscription_id": subscription.id},
            )
            continue

        alert_rule_set = list(snuba_query.alertrule_set.all())
        if not len(alert_rule_set):
            logger.warning(
                "QuerySubscription + SnubaQuery found with no alert_rule",
                extra={
                    "query_subscription_id": subscription.id,
                    "snuba_query_id": snuba_query.id,
                },
            )
            continue
        elif len(alert_rule_set) > 1:
            logger.warning(
                "QuerySubscription + SnubaQuery found with multiple alert_rules",
                extra={
                    "query_subscription_id": subscription.id,
                    "snuba_query_id": snuba_query.id,
                    "alert_rule_ids": [alert_rule.id for alert_rule in alert_rule_set],
                },
            )

        # Default to the first alert rule
        alert_rule = alert_rule_set[0]

        existing_alert_rule_projects = list(AlertRuleProjects.objects.filter(alert_rule=alert_rule))
        should_create_new = True

        if len(existing_alert_rule_projects) > 0:
            for arp in existing_alert_rule_projects:
                if arp.project_id != subscription.project_id:
                    logger.warning(
                        "AlertRuleProject found with different project than subscription",
                        extra={
                            "alert_rule_id": alert_rule.id,
                            "subscription_id": subscription.id,
                            "subscription_project": subscription.project_id,
                            "alert_rule_project": arp.project_id,
                        },
                    )
                    arp.delete()
                else:
                    should_create_new = False

        if should_create_new:
            AlertRuleProjects.objects.create(
                alert_rule=alert_rule,
                project=subscription.project,
            )


class Migration(CheckedMigration):
    is_dangerous = True

    dependencies = [
        ("sentry", "0686_remove_config_from_checkin_state_operation"),
    ]

    operations = [
        # Run the data migration
        migrations.RunPython(
            _backfill_alert_rule_projects,
            migrations.RunPython.noop,
            hints={"tables": ["sentry_alertruleprojects", "sentry_querysubscription"]},
        ),
    ]
