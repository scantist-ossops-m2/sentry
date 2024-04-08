# Generated by Django 3.2.23 on 2024-01-23 23:32

import logging
from enum import Enum

from django.conf import settings
from django.db import connection
from psycopg2.extras import execute_values

from sentry.issues.grouptype import get_group_type_by_type_id
from sentry.new_migrations.migrations import CheckedMigration
from sentry.utils import json, redis
from sentry.utils.query import RangeQuerySetWrapperWithProgressBarApprox

# copied to ensure migraitons work if the enums change #

logger = logging.getLogger(__name__)


class GroupSubStatus:
    # GroupStatus.IGNORED
    UNTIL_ESCALATING = 1
    # Group is ignored/archived for a count/user count/duration
    UNTIL_CONDITION_MET = 4
    # Group is ignored/archived forever
    FOREVER = 5

    # GroupStatus.UNRESOLVED
    ESCALATING = 2
    ONGOING = 3
    REGRESSED = 6
    NEW = 7


class PriorityLevel:
    LOW = 25
    MEDIUM = 50
    HIGH = 75


class GroupCategory(Enum):
    ERROR = 1
    PERFORMANCE = 2
    PROFILE = 3  # deprecated, merging with PERFORMANCE
    CRON = 4
    REPLAY = 5
    FEEDBACK = 6


PERFORMANCE_P95_ENDPOINT_REGRESSION_GROUPTYPE_ID = 1018
PROFILE_FUNCTION_REGRESSION_TYPE_ID = 2011


# end copy #

BATCH_SIZE = 100

UPDATE_QUERY = """
    UPDATE sentry_groupedmessage
    SET priority = new_data.priority,
    data = new_data.data::text
    FROM (VALUES %s) AS new_data(id, priority, data)
    WHERE sentry_groupedmessage.id = new_data.id AND sentry_groupedmessage.priority IS NULL
"""

REDIS_KEY = "priority_backfill.last_processed_id"


def _get_priority_level(group_id, level, type_id, substatus):
    group_type = get_group_type_by_type_id(type_id)

    # Replay and Feedback issues are medium priority
    if group_type.category in [GroupCategory.REPLAY.value, GroupCategory.FEEDBACK.value]:
        return PriorityLevel.MEDIUM

    # All escalating issues are high priority for all other issue categories
    if substatus == GroupSubStatus.ESCALATING:
        return PriorityLevel.HIGH

    if group_type.category == GroupCategory.ERROR.value:
        if level in [logging.INFO, logging.DEBUG]:
            return PriorityLevel.LOW
        elif level == logging.WARNING:
            return PriorityLevel.MEDIUM
        elif level in [logging.ERROR, logging.FATAL]:
            return PriorityLevel.HIGH

        logging.warning('Unknown log level "%s" for group %s', level, group_id)
        return PriorityLevel.MEDIUM

    if group_type.category == GroupCategory.CRON.value:
        if level == logging.WARNING:
            return PriorityLevel.MEDIUM

        return PriorityLevel.HIGH

    # Profiling issues should be treated the same as Performance issues since they are merging
    if group_type.category in [GroupCategory.PERFORMANCE.value, GroupCategory.PROFILE.value]:
        # Statistical detectors are medium priority
        if type_id in [
            PROFILE_FUNCTION_REGRESSION_TYPE_ID,
            PERFORMANCE_P95_ENDPOINT_REGRESSION_GROUPTYPE_ID,
        ]:
            return PriorityLevel.MEDIUM
        return PriorityLevel.LOW

    # All other issues are the default medium priority
    return PriorityLevel.MEDIUM


def update_group_priority(apps, schema_editor):
    Group = apps.get_model("sentry", "Group")

    redis_client = redis.redis_clusters.get(settings.SENTRY_MONITORS_REDIS_CLUSTER)
    cursor = connection.cursor()
    batch = []

    last_processed_id = int(redis_client.get(REDIS_KEY) or 0)
    logger.info("Starting group priority backfill from id %s", last_processed_id)
    for (
        group_id,
        data,
        level,
        group_type,
        substatus,
        priority,
    ) in RangeQuerySetWrapperWithProgressBarApprox(
        Group.objects.filter(id__gt=last_processed_id).values_list(
            "id", "data", "level", "type", "substatus", "priority"
        ),
        result_value_getter=lambda item: item[0],
    ):
        if priority is not None:
            continue

        priority = _get_priority_level(group_id, level, group_type, substatus)
        data.get("metadata", {})["initial_priority"] = priority
        data = json.dumps(data)
        batch.append((group_id, priority, data))

        if len(batch) >= BATCH_SIZE:
            logger.info(
                "Processing batch for group priority backfill with %s items",
                BATCH_SIZE,
                extra={"group_id": group_id},
            )
            execute_values(cursor, UPDATE_QUERY, batch, page_size=BATCH_SIZE)
            redis_client.set(REDIS_KEY, group_id, ex=60 * 60 * 24 * 7)
            batch = []

    if batch:
        execute_values(cursor, UPDATE_QUERY, batch, page_size=BATCH_SIZE)


class Migration(CheckedMigration):
    # This flag is used to mark that a migration shouldn't be automatically run in production. For
    # the most part, this should only be used for operations where it's safe to run the migration
    # after your code has deployed. So this should not be used for most operations that alter the
    # schema of a table.
    # Here are some things that make sense to mark as dangerous:
    # - Large data migrations. Typically we want these to be run manually by ops so that they can
    #   be monitored and not block the deploy for a long period of time while they run.
    # - Adding indexes to large tables. Since this can take a long time, we'd generally prefer to
    #   have ops run this and not block the deploy. Note that while adding an index is a schema
    #   change, it's completely safe to run the operation after the code has deployed.
    is_dangerous = True

    dependencies = [
        ("sentry", "0643_add_date_modified_col_dashboard_widget_query"),
    ]

    operations = []
