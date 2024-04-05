import logging

import sentry_sdk
from django.apps import apps
from django.conf import settings

from sentry.tasks.base import instrumented_task
from sentry.utils.locking import UnableToAcquireLock

logger = logging.getLogger(__name__)


def get_process_lock(partition: str = None):
    from sentry.locks import locks

    if partition is None:
        lock_key = "buffer:process_pending"
    else:
        lock_key = f"buffer:process_pending:{partition}"

    return locks.get(lock_key, duration=60, name="process_pending")


@instrumented_task(
    name="sentry.tasks.process_buffer.process_pending", queue="buffers.process_pending"
)
def process_pending(partition=None):
    """
    Process pending buffers.
    """
    from sentry import buffer

    lock = get_process_lock(partition)

    try:
        with lock.acquire():
            buffer.process_pending(partition=partition)
    except UnableToAcquireLock as error:
        logger.warning("process_pending.fail", extra={"error": error, "partition": partition})


@instrumented_task(
    name="sentry.tasks.process_buffer.process_pending_batch", queue="buffers.process_pending_batch"
)
def process_pending_batch(partition=None):
    """
    Process pending buffers in a batch.
    """

    # TODO(ceo): There isn't actually a task in server.py for this yet. will need to add that and put this behind a flag when we're ready
    from sentry import buffer

    lock = get_process_lock(partition)

    try:
        with lock.acquire():
            buffer.process_batch(partition=partition)
    except UnableToAcquireLock as error:
        logger.warning("process_pending_batch.fail", extra={"error": error, "partition": partition})


@instrumented_task(name="sentry.tasks.process_buffer.process_incr", queue="counters-0")
def process_incr(**kwargs):
    """
    Processes a buffer event.
    """
    from sentry import buffer

    sentry_sdk.set_tag("model", kwargs.get("model", "Unknown"))

    buffer.process(**kwargs)


def buffer_incr(model, *args, **kwargs):
    """
    Call `buffer.incr` task, resolving the model name first.

    `model_name` must be in form `app_label.model_name` e.g. `sentry.group`.
    """
    (buffer_incr_task.delay if settings.SENTRY_BUFFER_INCR_AS_CELERY_TASK else buffer_incr_task)(
        app_label=model._meta.app_label, model_name=model._meta.model_name, args=args, kwargs=kwargs
    )


@instrumented_task(
    name="sentry.tasks.process_buffer.buffer_incr_task",
    queue="buffers.incr",
)
def buffer_incr_task(app_label, model_name, args, kwargs):
    """
    Call `buffer.incr`, resolving the model first.
    """
    from sentry import buffer

    sentry_sdk.set_tag("model", model_name)

    buffer.incr(apps.get_model(app_label=app_label, model_name=model_name), *args, **kwargs)
