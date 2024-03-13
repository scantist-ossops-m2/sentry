from __future__ import annotations

import logging

from arroyo import Topic as ArroyoTopic
from arroyo.backends.kafka import KafkaPayload, KafkaProducer, build_kafka_configuration
from arroyo.types import Message, Value
from confluent_kafka import KafkaException
from django.conf import settings

from sentry.conf.types.kafka_definition import Topic
from sentry.spans.consumers.recombine.message import process_segment
from sentry.utils import json
from sentry.utils.arroyo_producer import SingletonProducer
from sentry.utils.kafka_config import get_kafka_producer_cluster_options, get_topic_definition

logger = logging.getLogger(__name__)


def _get_producer() -> KafkaProducer:
    cluster_name = get_topic_definition(Topic.BUFFERED_SEGMENT)["cluster"]
    producer_config = get_kafka_producer_cluster_options(cluster_name)
    producer_config.pop("compression.type", None)
    producer_config.pop("message.max.bytes", None)
    return KafkaProducer(build_kafka_configuration(default_config=producer_config))


_segments_producer = SingletonProducer(_get_producer)


def produce_segment_to_kafka(payload_data) -> None:
    if payload_data is None:
        return

    payload = KafkaPayload(None, json.dumps(payload_data).encode("utf-8"), [])
    if settings.SENTRY_EVENTSTREAM != "sentry.eventstream.kafka.KafkaEventStream":
        # If we're not running Kafka then we're just in dev.
        # Skip producing to Kafka and just process the message directly
        process_segment(json.loads(Message(Value(payload=payload, committable={})).payload.value))
        return

    try:
        _segments_producer.produce(ArroyoTopic(Topic.BUFFERED_SEGMENT), payload)
    except KafkaException:
        logger.exception("Failed to produce segment to Kafka")
