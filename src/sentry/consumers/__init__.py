from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence

import click
from arroyo.backends.abstract import Consumer
from arroyo.backends.kafka import KafkaProducer
from arroyo.backends.kafka.configuration import build_kafka_consumer_configuration
from arroyo.backends.kafka.consumer import KafkaConsumer
from arroyo.commit import ONCE_PER_SECOND
from arroyo.dlq import DlqLimit, DlqPolicy, KafkaDlqProducer
from arroyo.processing.processor import StreamProcessor
from arroyo.processing.strategies import Healthcheck
from arroyo.processing.strategies.abstract import ProcessingStrategy, ProcessingStrategyFactory
from arroyo.types import Topic as ArroyoTopic
from django.conf import settings

from sentry.conf.types.kafka_definition import (
    ConsumerDefinition,
    Topic,
    validate_consumer_definition,
)
from sentry.consumers.validate_schema import ValidateSchema
from sentry.ingest.types import ConsumerType
from sentry.utils.imports import import_string
from sentry.utils.kafka_config import get_kafka_producer_cluster_options, get_topic_definition

logger = logging.getLogger(__name__)


def convert_max_batch_time(ctx, param, value):
    if value <= 0:
        raise click.BadParameter("--max-batch-time must be greater than 0")

    # Our CLI arguments are written in ms, but the strategy requires seconds
    return int(value / 1000.0)


def multiprocessing_options(
    default_max_batch_size: int | None = None, default_max_batch_time_ms: int | None = 1000
):
    return [
        click.Option(["--processes", "num_processes"], default=1, type=int),
        click.Option(["--input-block-size"], type=int, default=None),
        click.Option(["--output-block-size"], type=int, default=None),
        click.Option(
            ["--max-batch-size"],
            default=default_max_batch_size,
            type=int,
            help="Maximum number of messages to batch before flushing.",
        ),
        click.Option(
            ["--max-batch-time-ms", "max_batch_time"],
            default=default_max_batch_time_ms,
            callback=convert_max_batch_time,
            type=int,
            help="Maximum time (in milliseconds) to wait before flushing a batch.",
        ),
    ]


def ingest_replay_recordings_options() -> list[click.Option]:
    """Return a list of ingest-replay-recordings options."""
    options = multiprocessing_options(default_max_batch_size=10)
    options.append(click.Option(["--threads", "num_threads"], type=int, default=4))
    return options


def ingest_replay_recordings_buffered_options() -> list[click.Option]:
    """Return a list of ingest-replay-recordings-buffered options."""
    options = [
        click.Option(
            ["--max-buffer-message-count", "max_buffer_message_count"],
            type=int,
            default=100,
        ),
        click.Option(
            ["--max-buffer-size-in-bytes", "max_buffer_size_in_bytes"],
            type=int,
            default=2_500_000,
        ),
        click.Option(
            ["--max-buffer-time-in-seconds", "max_buffer_time_in_seconds"],
            type=int,
            default=1,
        ),
    ]
    return options


def ingest_monitors_options() -> list[click.Option]:
    """Return a list of ingest-monitors options."""
    options = [
        click.Option(
            ["--mode", "mode"],
            type=click.Choice(["serial", "parallel"]),
            default="serial",
            help="The mode to process check-ins in. Parallel uses multithreading.",
        ),
        click.Option(
            ["--max-batch-size", "max_batch_size"],
            type=int,
            default=500,
            help="Maximum number of check-ins to batch before processing in parallel.",
        ),
        click.Option(
            ["--max-batch-time", "max_batch_time"],
            type=int,
            default=10,
            help="Maximum time spent batching check-ins to batch before processing in parallel.",
        ),
    ]
    return options


def ingest_events_options() -> list[click.Option]:
    """
    Options for the "events"-like consumers: `events`, `attachments`, `transactions`.

    This adds a `--reprocess-only-stuck-events`option. If that option is specified, *only* events
    that were already persisted in the `processing_store` will be processed.
    Events that never made it to the store, and ones that already made it out of the store are skipped,
    same as attachments (which are not idempotent, and we would rather not duplicate them).
    """
    options = multiprocessing_options(default_max_batch_size=100)
    options.append(
        click.Option(
            ["--reprocess-only-stuck-events", "reprocess_only_stuck_events"],
            type=bool,
            is_flag=True,
            default=False,
        )
    )
    return options


_METRICS_INDEXER_OPTIONS = [
    click.Option(["--input-block-size"], type=int, default=None),
    click.Option(["--output-block-size"], type=int, default=None),
    click.Option(["--indexer-db"], default="postgres"),
    click.Option(["max_msg_batch_size", "--max-msg-batch-size"], type=int, default=50),
    click.Option(["max_msg_batch_time", "--max-msg-batch-time-ms"], type=int, default=10000),
    click.Option(["max_parallel_batch_size", "--max-parallel-batch-size"], type=int, default=50),
    click.Option(
        ["max_parallel_batch_time", "--max-parallel-batch-time-ms"], type=int, default=10000
    ),
    click.Option(
        ["--processes"],
        default=1,
        type=int,
    ),
]

_METRICS_LAST_SEEN_UPDATER_OPTIONS = [
    click.Option(
        ["--max-batch-size"],
        default=100,
        type=int,
        help="Maximum number of messages to batch before flushing.",
    ),
    click.Option(
        ["--max-batch-time-ms", "max_batch_time"],
        default=1000,
        callback=convert_max_batch_time,
        type=int,
        help="Maximum time (in milliseconds) to wait before flushing a batch.",
    ),
    click.Option(["--indexer-db"], default="postgres"),
]

_POST_PROCESS_FORWARDER_OPTIONS = multiprocessing_options(
    default_max_batch_size=1000, default_max_batch_time_ms=1000
) + [
    click.Option(
        ["--concurrency"],
        default=5,
        type=int,
        help="Thread pool size for post process worker.",
    ),
    click.Option(
        ["--mode"],
        default="multithreaded",
        type=click.Choice(["multithreaded", "multiprocess"]),
        help="Mode to run post process forwarder in.",
    ),
]

# consumer name -> consumer definition
KAFKA_CONSUMERS: Mapping[str, ConsumerDefinition] = {
    "ingest-profiles": {
        "topic": Topic.PROFILES,
        "strategy_factory": "sentry.profiles.consumers.process.factory.ProcessProfileStrategyFactory",
    },
    "ingest-replay-recordings": {
        "topic": Topic.INGEST_REPLAYS_RECORDINGS,
        "strategy_factory": "sentry.replays.consumers.recording.ProcessReplayRecordingStrategyFactory",
        "click_options": ingest_replay_recordings_options(),
    },
    "ingest-replay-recordings-buffered": {
        "topic": Topic.INGEST_REPLAYS_RECORDINGS,
        "strategy_factory": "sentry.replays.consumers.recording_buffered.RecordingBufferedStrategyFactory",
        "click_options": ingest_replay_recordings_buffered_options(),
    },
    "ingest-monitors": {
        "topic": Topic.INGEST_MONITORS,
        "strategy_factory": "sentry.monitors.consumers.monitor_consumer.StoreMonitorCheckInStrategyFactory",
        "click_options": ingest_monitors_options(),
    },
    "billing-metrics-consumer": {
        "topic": Topic.SNUBA_GENERIC_METRICS,
        "strategy_factory": "sentry.ingest.billing_metrics_consumer.BillingMetricsConsumerStrategyFactory",
    },
    # Known differences to 'sentry run occurrences-ingest-consumer':
    # - ingest_consumer_types metric tag is missing. Use the kafka_topic and
    #   group_id tags provided by run_basic_consumer instead
    "ingest-occurrences": {
        "topic": Topic.INGEST_OCCURRENCES,
        "strategy_factory": "sentry.issues.run.OccurrenceStrategyFactory",
        "click_options": multiprocessing_options(default_max_batch_size=20),
    },
    "events-subscription-results": {
        "topic": Topic.EVENTS_SUBSCRIPTIONS_RESULTS,
        "strategy_factory": "sentry.snuba.query_subscriptions.run.QuerySubscriptionStrategyFactory",
        "click_options": multiprocessing_options(default_max_batch_size=100),
        "static_args": {"dataset": "events"},
    },
    "transactions-subscription-results": {
        "topic": Topic.TRANSACTIONS_SUBSCRIPTIONS_RESULTS,
        "strategy_factory": "sentry.snuba.query_subscriptions.run.QuerySubscriptionStrategyFactory",
        "click_options": multiprocessing_options(default_max_batch_size=100),
        "static_args": {"dataset": "transactions"},
    },
    "generic-metrics-subscription-results": {
        "topic": Topic.GENERIC_METRICS_SUBSCRIPTIONS_RESULTS,
        "validate_schema": True,
        "strategy_factory": "sentry.snuba.query_subscriptions.run.QuerySubscriptionStrategyFactory",
        "click_options": multiprocessing_options(default_max_batch_size=100),
        "static_args": {"dataset": "generic_metrics"},
    },
    "metrics-subscription-results": {
        "topic": Topic.METRICS_SUBSCRIPTIONS_RESULTS,
        "strategy_factory": "sentry.snuba.query_subscriptions.run.QuerySubscriptionStrategyFactory",
        "click_options": multiprocessing_options(default_max_batch_size=100),
        "static_args": {"dataset": "metrics"},
    },
    "ingest-events": {
        "topic": Topic.INGEST_EVENTS,
        "strategy_factory": "sentry.ingest.consumer.factory.IngestStrategyFactory",
        "click_options": ingest_events_options(),
        "static_args": {
            "consumer_type": ConsumerType.Events,
        },
        "dlq_topic": Topic.INGEST_EVENTS_DLQ,
    },
    "ingest-feedback-events": {
        "topic": Topic.INGEST_FEEDBACK_EVENTS,
        "strategy_factory": "sentry.ingest.consumer.factory.IngestStrategyFactory",
        "click_options": ingest_events_options(),
        "static_args": {
            "consumer_type": ConsumerType.Feedback,
        },
        "dlq_topic": Topic.INGEST_FEEDBACK_EVENTS_DLQ,
    },
    "ingest-attachments": {
        "topic": Topic.INGEST_ATTACHMENTS,
        "strategy_factory": "sentry.ingest.consumer.factory.IngestStrategyFactory",
        "click_options": ingest_events_options(),
        "static_args": {
            "consumer_type": ConsumerType.Attachments,
        },
        "dlq_topic": Topic.INGEST_ATTACHMENTS_DLQ,
    },
    "ingest-transactions": {
        "topic": Topic.INGEST_TRANSACTIONS,
        "strategy_factory": "sentry.ingest.consumer.factory.IngestStrategyFactory",
        "click_options": ingest_events_options(),
        "static_args": {
            "consumer_type": ConsumerType.Transactions,
        },
        "dlq_topic": Topic.INGEST_TRANSACTIONS_DLQ,
    },
    "ingest-metrics": {
        "topic": Topic.INGEST_METRICS,
        "strategy_factory": "sentry.sentry_metrics.consumers.indexer.parallel.MetricsConsumerStrategyFactory",
        "click_options": _METRICS_INDEXER_OPTIONS,
        "static_args": {
            "ingest_profile": "release-health",
        },
        "dlq_topic": Topic.INGEST_METRICS_DLQ,
        "dlq_max_invalid_ratio": 0.01,
        "dlq_max_consecutive_count": 1000,
    },
    "ingest-generic-metrics": {
        "topic": Topic.INGEST_PERFORMANCE_METRICS,
        "strategy_factory": "sentry.sentry_metrics.consumers.indexer.parallel.MetricsConsumerStrategyFactory",
        "click_options": _METRICS_INDEXER_OPTIONS,
        "static_args": {
            "ingest_profile": "performance",
        },
        "dlq_topic": Topic.INGEST_GENERIC_METRICS_DLQ,
        "dlq_max_invalid_ratio": None,
        "dlq_max_consecutive_count": None,
    },
    "generic-metrics-last-seen-updater": {
        "topic": Topic.SNUBA_GENERIC_METRICS,
        "strategy_factory": "sentry.sentry_metrics.consumers.last_seen_updater.LastSeenUpdaterStrategyFactory",
        "click_options": _METRICS_LAST_SEEN_UPDATER_OPTIONS,
        "static_args": {
            "ingest_profile": "performance",
        },
    },
    "metrics-last-seen-updater": {
        "topic": Topic.SNUBA_METRICS,
        "strategy_factory": "sentry.sentry_metrics.consumers.last_seen_updater.LastSeenUpdaterStrategyFactory",
        "click_options": _METRICS_LAST_SEEN_UPDATER_OPTIONS,
        "static_args": {
            "ingest_profile": "release-health",
        },
    },
    "post-process-forwarder-issue-platform": {
        "topic": Topic.EVENTSTREAM_GENERIC,
        "strategy_factory": "sentry.eventstream.kafka.dispatch.EventPostProcessForwarderStrategyFactory",
        "synchronize_commit_log_topic_default": "snuba-generic-events-commit-log",
        "synchronize_commit_group_default": "generic_events_group",
        "click_options": _POST_PROCESS_FORWARDER_OPTIONS,
    },
    "post-process-forwarder-transactions": {
        "topic": Topic.TRANSACTIONS,
        "strategy_factory": "sentry.eventstream.kafka.dispatch.EventPostProcessForwarderStrategyFactory",
        "synchronize_commit_log_topic_default": "snuba-transactions-commit-log",
        "synchronize_commit_group_default": "transactions_group",
        "click_options": _POST_PROCESS_FORWARDER_OPTIONS,
    },
    "post-process-forwarder-errors": {
        "topic": Topic.EVENTS,
        "strategy_factory": "sentry.eventstream.kafka.dispatch.EventPostProcessForwarderStrategyFactory",
        "synchronize_commit_log_topic_default": "snuba-commit-log",
        "synchronize_commit_group_default": "snuba-consumers",
        "click_options": _POST_PROCESS_FORWARDER_OPTIONS,
    },
    "process-spans": {
        "topic": Topic.SNUBA_SPANS,
        "strategy_factory": "sentry.spans.consumers.process.factory.ProcessSpansStrategyFactory",
        "click_options": multiprocessing_options(default_max_batch_size=100),
    },
    "detect-performance-issues": {
        "topic": Topic.BUFFERED_SEGMENTS,
        "strategy_factory": "sentry.spans.consumers.detect_performance_issues.factory.DetectPerformanceIssuesStrategyFactory",
        "click_options": multiprocessing_options(default_max_batch_size=100),
    },
    **settings.SENTRY_KAFKA_CONSUMERS,
}


def print_deprecation_warning(name, group_id):
    import click

    click.echo(
        f"WARNING: Deprecated command, use sentry run consumer {name} "
        f"--consumer-group {group_id} ..."
    )


def get_stream_processor(
    consumer_name: str,
    consumer_args: Sequence[str],
    topic: str | None,
    cluster: str | None,
    group_id: str,
    auto_offset_reset: str,
    strict_offset_reset: bool,
    join_timeout: float | None = None,
    max_poll_interval_ms: int | None = None,
    synchronize_commit_log_topic: str | None = None,
    synchronize_commit_group: str | None = None,
    healthcheck_file_path: str | None = None,
    enable_dlq: bool = False,
    enforce_schema: bool = False,
    group_instance_id: str | None = None,
) -> StreamProcessor:
    from sentry.utils import kafka_config

    try:
        consumer_definition = KAFKA_CONSUMERS[consumer_name]
    except KeyError:
        raise click.ClickException(
            f"No consumer named {consumer_name} in sentry.consumers.KAFKA_CONSUMERS. "
            f"Most likely there is another subcommand in 'sentry run' "
            f"responsible for this consumer"
        )
    try:
        validate_consumer_definition(consumer_definition)
    except ValueError as e:
        raise click.ClickException(
            f"Invalid consumer definition configured for {consumer_name}"
        ) from e

    strategy_factory_cls = import_string(consumer_definition["strategy_factory"])
    consumer_topic = consumer_definition["topic"]

    topic_defn = get_topic_definition(consumer_topic)
    real_topic = topic_defn["real_topic_name"]
    cluster = topic_defn["cluster"]

    if topic is None:
        topic = real_topic

    cmd = click.Command(
        name=consumer_name, params=list(consumer_definition.get("click_options") or ())
    )
    cmd_context = cmd.make_context(consumer_name, list(consumer_args))
    strategy_factory = cmd_context.invoke(
        strategy_factory_cls, **cmd_context.params, **consumer_definition.get("static_args") or {}
    )

    def build_consumer_config(group_id: str):
        assert cluster is not None

        consumer_config = build_kafka_consumer_configuration(
            kafka_config.get_kafka_consumer_cluster_options(
                cluster,
            ),
            group_id=group_id,
            auto_offset_reset=auto_offset_reset,
            strict_offset_reset=strict_offset_reset,
        )

        if max_poll_interval_ms is not None:
            consumer_config["max.poll.interval.ms"] = max_poll_interval_ms
            # HACK: If the max poll interval is less than 45 seconds, set the session timeout
            # to the same. (it's default is 45 seconds and it must be <= to max.poll.interval.ms)
            if max_poll_interval_ms < 45000:
                consumer_config["session.timeout.ms"] = max_poll_interval_ms

        if group_instance_id is not None:
            consumer_config["group.instance.id"] = group_instance_id

        return consumer_config

    consumer: Consumer = KafkaConsumer(build_consumer_config(group_id))

    if synchronize_commit_group is None:
        synchronize_commit_group = consumer_definition.get("synchronize_commit_group_default")

    if synchronize_commit_log_topic is None:
        synchronize_commit_log_topic = consumer_definition.get(
            "synchronize_commit_log_topic_default"
        )

    if synchronize_commit_group or synchronize_commit_log_topic:
        if bool(synchronize_commit_log_topic) != bool(synchronize_commit_group):
            raise click.BadParameter(
                "Both --synchronize_commit_group and --synchronize_commit_log_topic must be passed, or neither."
            )

        assert synchronize_commit_group is not None
        assert synchronize_commit_log_topic is not None

        commit_log_consumer = KafkaConsumer(
            build_consumer_config(f"sentry-commit-log-{uuid.uuid1().hex}")
        )

        from sentry.consumers.synchronized import SynchronizedConsumer

        consumer = SynchronizedConsumer(
            consumer=consumer,
            commit_log_consumer=commit_log_consumer,
            commit_log_topic=ArroyoTopic(synchronize_commit_log_topic),
            commit_log_groups={synchronize_commit_group},
        )
    elif consumer_definition.get("require_synchronization"):
        click.BadParameter(
            "--synchronize_commit_group and --synchronize_commit_log_topic are required arguments for this consumer"
        )

    # Validate schema if enforce_schema is true or "validate_schema" is set
    validate_schema = enforce_schema or consumer_definition.get("validate_schema") or False

    if validate_schema:
        strategy_factory = ValidateSchemaStrategyFactoryWrapper(
            consumer_topic.value, enforce_schema, strategy_factory
        )

    if healthcheck_file_path is not None:
        strategy_factory = HealthcheckStrategyFactoryWrapper(
            healthcheck_file_path, strategy_factory
        )

    if enable_dlq:
        try:
            dlq_topic = consumer_definition["dlq_topic"]
        except KeyError as e:
            raise click.BadParameter(
                f"Cannot enable DLQ for consumer: {consumer_name}, no DLQ topic has been defined for it"
            ) from e
        try:
            dlq_topic_defn = get_topic_definition(dlq_topic)
            cluster_setting = dlq_topic_defn["cluster"]
        except ValueError as e:
            raise click.BadParameter(
                f"Cannot enable DLQ for consumer: {consumer_name}, DLQ topic {dlq_topic} is not configured in this environment"
            ) from e

        producer_config = get_kafka_producer_cluster_options(cluster_setting)
        dlq_producer = KafkaProducer(producer_config)

        dlq_policy = DlqPolicy(
            KafkaDlqProducer(dlq_producer, ArroyoTopic(dlq_topic_defn["real_topic_name"])),
            DlqLimit(
                max_invalid_ratio=consumer_definition["dlq_max_invalid_ratio"],
                max_consecutive_count=consumer_definition["dlq_max_consecutive_count"],
            ),
            None,
        )
    else:
        dlq_policy = None

    return StreamProcessor(
        consumer=consumer,
        topic=ArroyoTopic(topic),
        processor_factory=strategy_factory,
        commit_policy=ONCE_PER_SECOND,
        join_timeout=join_timeout,
        dlq_policy=dlq_policy,
    )


class ValidateSchemaStrategyFactoryWrapper(ProcessingStrategyFactory):
    """
    This wrapper is used to validate the schema of the event before
    passing to the rest of the pipeline. Since the message is currently decoded
    twice, it should only be run in dev or on a small fraction of prod data.
    """

    def __init__(self, topic: str, enforce_schema: bool, inner: ProcessingStrategyFactory) -> None:
        self.topic = topic
        self.enforce_schema = enforce_schema
        self.inner = inner

    def create_with_partitions(self, commit, partitions) -> ProcessingStrategy:
        rv = self.inner.create_with_partitions(commit, partitions)

        return ValidateSchema(self.topic, self.enforce_schema, rv)


class HealthcheckStrategyFactoryWrapper(ProcessingStrategyFactory):
    def __init__(self, healthcheck_file_path: str, inner: ProcessingStrategyFactory):
        self.healthcheck_file_path = healthcheck_file_path
        self.inner = inner

    def create_with_partitions(self, commit, partitions):
        rv = self.inner.create_with_partitions(commit, partitions)
        return Healthcheck(self.healthcheck_file_path, rv)
