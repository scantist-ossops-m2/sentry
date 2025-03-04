from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from time import time
from typing import Any
from uuid import UUID

import msgpack
import sentry_sdk
from django.conf import settings

from sentry import options, quotas
from sentry.constants import DataCategory
from sentry.lang.java.proguard import open_proguard_mapper
from sentry.lang.javascript.processing import _handles_frame as is_valid_javascript_frame
from sentry.lang.native.processing import _merge_image
from sentry.lang.native.symbolicator import Symbolicator, SymbolicatorPlatform, SymbolicatorTaskKind
from sentry.lang.native.utils import native_images_from_data
from sentry.models.debugfile import ProjectDebugFile
from sentry.models.eventerror import EventError
from sentry.models.organization import Organization
from sentry.models.project import Project
from sentry.models.projectkey import ProjectKey, UseCase
from sentry.profiles.device import classify_device
from sentry.profiles.java import (
    convert_android_methods_to_jvm_frames,
    deobfuscate_signature,
    format_signature,
    merge_jvm_frames_with_android_methods,
)
from sentry.profiles.utils import get_from_profiling_service
from sentry.signals import first_profile_received
from sentry.silo import SiloMode
from sentry.tasks.base import instrumented_task
from sentry.utils import json, metrics
from sentry.utils.outcomes import Outcome, track_outcome
from sentry.utils.sdk import set_measurement

Profile = MutableMapping[str, Any]
CallTrees = Mapping[str, list[Any]]


class VroomTimeout(Exception):
    pass


@instrumented_task(
    name="sentry.profiles.task.process_profile",
    queue="profiles.process",
    autoretry_for=(VroomTimeout,),  # Retry when vroom returns a GCS timeout
    retry_backoff=True,
    retry_backoff_max=60,  # up to 1 min
    retry_jitter=True,
    default_retry_delay=5,  # retries after 5s
    max_retries=5,
    acks_late=True,
    task_time_limit=60,
    task_acks_on_failure_or_timeout=False,
    silo_mode=SiloMode.REGION,
)
def process_profile_task(
    profile: Profile | None = None,
    payload: Any = None,
    sampled: bool = True,
    **kwargs: Any,
) -> None:
    if not sampled and not options.get("profiling.profile_metrics.unsampled_profiles.enabled"):
        return

    if payload:
        message_dict = msgpack.unpackb(payload, use_list=False)
        profile = json.loads(message_dict["payload"], use_rapid_json=True)

        assert profile is not None

        profile.update(
            {
                "organization_id": message_dict["organization_id"],
                "project_id": message_dict["project_id"],
                "received": message_dict["received"],
                "sampled": sampled,
            }
        )

    assert profile is not None

    if not sampled:
        metrics.incr(
            "process_profile.unsampled_profiles",
            tags={"platform": profile["platform"]},
        )

    organization = Organization.objects.get_from_cache(id=profile["organization_id"])

    sentry_sdk.set_tag("organization", organization.id)
    sentry_sdk.set_tag("organization.slug", organization.slug)

    project = Project.objects.get_from_cache(id=profile["project_id"])

    sentry_sdk.set_tag("project", project.id)
    sentry_sdk.set_tag("project.slug", project.slug)

    profile_context = {
        "organization_id": profile["organization_id"],
        "project_id": profile["project_id"],
    }

    if "profile_id" in profile:
        profile["event_id"] = profile["profile_id"]

    if "event_id" in profile:
        profile_context["profile_id"] = profile["event_id"]
    elif "chunk_id" in profile:
        profile_context["chunk_id"] = profile["chunk_id"]

    sentry_sdk.set_context(
        "profile",
        profile_context,
    )

    sentry_sdk.set_tag("platform", profile["platform"])

    if "version" in profile:
        version = profile["version"]
        sentry_sdk.set_tag("format", f"sample_v{version}")

        set_measurement("profile.samples", len(profile["profile"]["samples"]))
        set_measurement("profile.stacks", len(profile["profile"]["stacks"]))
        set_measurement("profile.frames", len(profile["profile"]["frames"]))
    else:
        sentry_sdk.set_tag("format", "legacy")

    if not _symbolicate_profile(profile, project):
        return

    if not _deobfuscate_profile(profile, project):
        return

    if "js_profile" in profile:
        prepare_android_js_profile(profile)
        if not _symbolicate_profile(profile["js_profile"], project):
            return
        clean_android_js_profile(profile)

    if not _normalize_profile(profile, organization, project):
        return

    if "version" in profile:
        set_measurement("profile.samples.processed", len(profile["profile"]["samples"]))
        set_measurement("profile.stacks.processed", len(profile["profile"]["stacks"]))
        set_measurement("profile.frames.processed", len(profile["profile"]["frames"]))

    if (
        profile.get("version") != "2"
        and options.get("profiling.generic_metrics.functions_ingestion.enabled")
        and organization.id
        in options.get("profiling.generic_metrics.functions_ingestion.allowed_org_ids")
    ):
        try:
            with metrics.timer("process_profile.get_metrics_dsn"):
                dsn = get_metrics_dsn(project.id)
            profile["options"] = {
                "dsn": dsn,
            }
        except Exception as e:
            sentry_sdk.capture_exception(e)

    if not _push_profile_to_vroom(profile, project):
        return

    with metrics.timer("process_profile.track_outcome.accepted"):
        try:
            _track_duration_outcome(profile=profile, project=project)
        except Exception as e:
            sentry_sdk.capture_exception(e)
        if profile.get("version") != "2":
            _track_outcome(profile=profile, project=project, outcome=Outcome.ACCEPTED)


JS_PLATFORMS = ["javascript", "node"]
SHOULD_SYMBOLICATE_JS = frozenset(JS_PLATFORMS)
SHOULD_SYMBOLICATE = frozenset(["cocoa", "rust"] + JS_PLATFORMS)
SHOULD_DEOBFUSCATE = frozenset(["android"])


def _should_symbolicate(profile: Profile) -> bool:
    platform: str = profile["platform"]
    return platform in SHOULD_SYMBOLICATE and not profile.get("processed_by_symbolicator", False)


def _should_deobfuscate(profile: Profile) -> bool:
    platform: str = profile["platform"]
    return platform in SHOULD_DEOBFUSCATE and not profile.get("deobfuscated", False)


def get_profile_platforms(profile: Profile) -> list[str]:
    platforms = [profile["platform"]]

    if "version" in profile and profile["platform"] in SHOULD_SYMBOLICATE_JS:
        for frame in profile["profile"]["frames"]:
            if frame.get("platform", "") == "cocoa":
                platforms.append(frame["platform"])
                break

    return platforms


def get_debug_images_for_platform(profile, platform):
    if platform in SHOULD_SYMBOLICATE_JS:
        return [image for image in profile["debug_meta"]["images"] if image["type"] == "sourcemap"]
    return native_images_from_data(profile)


def _symbolicate_profile(profile: Profile, project: Project) -> bool:
    if not _should_symbolicate(profile):
        return True

    with sentry_sdk.start_span(op="task.profiling.symbolicate"):
        try:
            if "debug_meta" not in profile or not profile["debug_meta"]:
                metrics.incr(
                    "process_profile.missing_keys.debug_meta",
                    tags={"platform": profile["platform"]},
                    sample_rate=1.0,
                )
                return True

            platforms = get_profile_platforms(profile)
            original_images = profile["debug_meta"]["images"]
            images = dict()
            for platform in platforms:
                images[platform] = get_debug_images_for_platform(profile, platform)

            for platform in platforms:
                profile["debug_meta"]["images"] = images[platform]
                # WARNING(loewenheim): This function call may mutate `profile`'s frame list!
                # See comments in the function for why this happens.
                raw_modules, raw_stacktraces, frames_sent = _prepare_frames_from_profile(
                    profile, platform
                )

                set_measurement(
                    f"profile.frames.sent.{platform}",
                    len(frames_sent),
                )

                modules, stacktraces, success = run_symbolicate(
                    project=project,
                    profile=profile,
                    modules=raw_modules,
                    stacktraces=raw_stacktraces,
                    platform=platform,
                )

                assert len(images[platform]) == len(modules)
                for raw_image, complete_image in zip(images[platform], modules):
                    _merge_image(raw_image, complete_image, None, profile)

                if success:
                    _process_symbolicator_results(
                        profile=profile,
                        modules=modules,
                        stacktraces=stacktraces,
                        frames_sent=frames_sent,
                        platform=platform,
                    )

        except Exception as e:
            sentry_sdk.capture_exception(e)
            metrics.incr("process_profile.symbolicate.error", sample_rate=1.0)
            _track_outcome(
                profile=profile,
                project=project,
                outcome=Outcome.INVALID,
                reason="profiling_failed_symbolication",
            )
            return False
        profile["debug_meta"]["images"] = original_images
        profile["processed_by_symbolicator"] = True
        return True


def _deobfuscate_profile(profile: Profile, project: Project) -> bool:
    if not _should_deobfuscate(profile):
        return True

    with sentry_sdk.start_span(op="task.profiling.deobfuscate"):
        try:
            if "profile" not in profile or not profile["profile"]:
                metrics.incr(
                    "process_profile.missing_keys.profile",
                    tags={"platform": profile["platform"]},
                    sample_rate=1.0,
                )
                return True

            _deobfuscate(profile=profile, project=project)

            profile["deobfuscated"] = True
            return True
        except Exception as e:
            sentry_sdk.capture_exception(e)
            _track_outcome(
                profile=profile,
                project=project,
                outcome=Outcome.INVALID,
                reason="profiling_failed_deobfuscation",
            )
            return False


def _normalize_profile(profile: Profile, organization: Organization, project: Project) -> bool:
    if profile.get("normalized", False):
        return True

    with sentry_sdk.start_span(op="task.profiling.normalize"):
        try:
            _normalize(profile=profile, organization=organization)
            profile["normalized"] = True
            return True
        except Exception as e:
            sentry_sdk.capture_exception(e)
            _track_outcome(
                profile=profile,
                project=project,
                outcome=Outcome.INVALID,
                reason="profiling_failed_normalization",
            )
            return False


@metrics.wraps("process_profile.normalize")
def _normalize(profile: Profile, organization: Organization) -> None:
    profile["retention_days"] = quotas.get_event_retention(organization=organization) or 90
    platform = profile["platform"]
    version = profile.get("version")

    if platform not in {"cocoa", "android"} or version == "2":
        return

    classification_options = dict()

    if platform == "android":
        classification_options.update(
            {
                "cpu_frequencies": profile["device_cpu_frequencies"],
                "physical_memory_bytes": profile["device_physical_memory_bytes"],
            }
        )

    if version == "1":
        classification_options.update(
            {
                "model": profile["device"]["model"],
                "os_name": profile["os"]["name"],
                "is_emulator": profile["device"]["is_emulator"],
            }
        )
    elif version is None:
        classification_options.update(
            {
                "model": profile["device_model"],
                "os_name": profile["device_os_name"],
                "is_emulator": profile["device_is_emulator"],
            }
        )

    classification = str(classify_device(**classification_options))

    if version == "1":
        profile["device"]["classification"] = classification
    else:
        profile["device_classification"] = classification


def _prepare_frames_from_profile(
    profile: Profile, platform: str
) -> tuple[list[Any], list[Any], set[int]]:
    with sentry_sdk.start_span(op="task.profiling.symbolicate.prepare_frames"):
        modules = profile["debug_meta"]["images"]
        frames: list[Any] = []
        frames_sent: set[int] = set()

        if platform is None:
            platform = profile["platform"]

        # NOTE: the usage of `adjust_instruction_addr` assumes that all
        # the profilers on all the platforms are walking stacks right from a
        # suspended threads cpu context

        # in the sample format, we have a frames key containing all the frames
        if "version" in profile:
            if platform in JS_PLATFORMS:
                for idx, f in enumerate(profile["profile"]["frames"]):
                    if is_valid_javascript_frame(f, profile):
                        frames_sent.add(idx)

                frames = [profile["profile"]["frames"][idx] for idx in frames_sent]
            else:
                if profile["platform"] != platform:
                    # we might have both js and cocoa frames (react native)
                    # and we need to filter only for the cocoa ones
                    for idx, f in enumerate(profile["profile"]["frames"]):
                        if (
                            f.get("platform", "") == platform
                            and f.get("instruction_addr") is not None
                        ):
                            frames_sent.add(idx)
                    frames = [profile["profile"]["frames"][idx] for idx in frames_sent]
                else:
                    # if the root platform is cocoa, then we know we have only cocoa frames
                    frames = profile["profile"]["frames"]

                for stack in profile["profile"]["stacks"]:
                    if len(stack) > 0:
                        # Make a deep copy of the leaf frame with adjust_instruction_addr = False
                        # and append it to the list. This ensures correct behavior
                        # if the leaf frame also shows up in the middle of another stack.
                        first_frame_idx = stack[0]
                        frame = deepcopy(profile["profile"]["frames"][first_frame_idx])
                        frame["adjust_instruction_addr"] = False
                        if profile["platform"] not in JS_PLATFORMS:
                            frames.append(frame)
                            stack[0] = len(frames) - 1
                        else:
                            # In case where root platform is not cocoa, but we're dealing
                            # with a cocoa stack (as in react-native), since we're relying
                            # on frames_sent instead of sending back the whole
                            # profile["profile"]["frames"], we have to append the deepcopy
                            # frame both to the original frames and to the list frames.
                            # see _process_symbolicator_results_for_sample method's logic
                            if first_frame_idx in frames_sent:
                                profile["profile"]["frames"].append(frame)
                                frames.append(frame)
                                stack[0] = len(profile["profile"]["frames"]) - 1
                                frames_sent.add(stack[0])

            stacktraces = [{"frames": frames}]
        # in the original format, we need to gather frames from all samples
        else:
            stacktraces = []
            for s in profile["sampled_profile"]["samples"]:
                frames = s["frames"]

                if len(frames) > 0:
                    frames[0]["adjust_instruction_addr"] = False

                stacktraces.append(
                    {
                        "frames": frames,
                    }
                )
        return (modules, stacktraces, frames_sent)


def symbolicate(
    symbolicator: Symbolicator,
    profile: Profile,
    modules: list[Any],
    stacktraces: list[Any],
    platform: str,
) -> Any:
    if platform in SHOULD_SYMBOLICATE_JS:
        return symbolicator.process_js(
            stacktraces=stacktraces,
            modules=modules,
            release=profile.get("release"),
            dist=profile.get("dist"),
            apply_source_context=False,
        )
    elif platform == "android":
        return symbolicator.process_jvm(
            exceptions=[],
            stacktraces=stacktraces,
            modules=modules,
            release_package=profile.get("transaction_metadata", {}).get("app.identifier"),
            apply_source_context=False,
        )
    return symbolicator.process_payload(
        stacktraces=stacktraces, modules=modules, apply_source_context=False
    )


class SymbolicationTimeout(Exception):
    pass


@metrics.wraps("process_profile.symbolicate.request")
def run_symbolicate(
    project: Project,
    profile: Profile,
    modules: list[Any],
    stacktraces: list[Any],
    platform: str,
) -> tuple[list[Any], list[Any], bool]:
    symbolication_start_time = time()

    def on_symbolicator_request():
        duration = time() - symbolication_start_time
        if duration > settings.SYMBOLICATOR_PROCESS_EVENT_HARD_TIMEOUT:
            raise SymbolicationTimeout

    if platform in SHOULD_SYMBOLICATE_JS:
        symbolicator_platform = SymbolicatorPlatform.js
    else:
        symbolicator_platform = SymbolicatorPlatform.native
    symbolicator = Symbolicator(
        task_kind=SymbolicatorTaskKind(platform=symbolicator_platform),
        on_request=on_symbolicator_request,
        project=project,
        event_id=profile["event_id"],
    )

    try:
        with sentry_sdk.start_span(op="task.profiling.symbolicate.process_payload"):
            response = symbolicate(
                symbolicator=symbolicator,
                profile=profile,
                stacktraces=stacktraces,
                modules=modules,
                platform=platform,
            )

            if not response:
                profile["symbolicator_error"] = {
                    "type": EventError.NATIVE_INTERNAL_FAILURE,
                }
                return modules, stacktraces, False
            elif response["status"] == "completed":
                return (
                    response.get("modules", modules),
                    response.get("stacktraces", stacktraces),
                    True,
                )
            elif response["status"] == "failed":
                profile["symbolicator_error"] = {
                    "type": EventError.NATIVE_SYMBOLICATOR_FAILED,
                    "status": response.get("status"),
                    "message": response.get("message"),
                }
                return modules, stacktraces, False
            else:
                profile["symbolicator_error"] = {
                    "status": response.get("status"),
                    "type": EventError.NATIVE_INTERNAL_FAILURE,
                }
                return modules, stacktraces, False
    except SymbolicationTimeout:
        metrics.incr("process_profile.symbolicate.timeout", sample_rate=1.0)

    # returns the unsymbolicated data to avoid errors later
    return modules, stacktraces, False


@metrics.wraps("process_profile.symbolicate.process")
def _process_symbolicator_results(
    profile: Profile,
    modules: list[Any],
    stacktraces: list[Any],
    frames_sent: set[int],
    platform: str,
) -> None:
    with sentry_sdk.start_span(op="task.profiling.symbolicate.process_results"):
        # update images with status after symbolication
        profile["debug_meta"]["images"] = modules

        if "version" in profile:
            _process_symbolicator_results_for_sample(
                profile,
                stacktraces,
                frames_sent,
                platform,
            )
            return

        if platform == "rust":
            _process_symbolicator_results_for_rust(profile, stacktraces)
        elif platform == "cocoa":
            _process_symbolicator_results_for_cocoa(profile, stacktraces)

        # rename the profile key to suggest it has been processed
        profile["profile"] = profile.pop("sampled_profile")


def _process_symbolicator_results_for_sample(
    profile: Profile, stacktraces: list[Any], frames_sent: set[int], platform: str
) -> None:
    if platform == "rust":

        def truncate_stack_needed(frames: list[dict[str, Any]], stack: list[Any]) -> list[Any]:
            # remove top frames related to the profiler (top of the stack)
            if frames[stack[0]].get("function", "") == "perf_signal_handler":
                stack = stack[2:]
            # remove unsymbolicated frames before the runtime calls (bottom of the stack)
            if frames[stack[len(stack) - 2]].get("function", "") == "":
                stack = stack[:-2]
            return stack

    elif platform == "cocoa":

        def truncate_stack_needed(
            frames: list[dict[str, Any]],
            stack: list[Any],
        ) -> list[Any]:
            # remove bottom frames we can't symbolicate
            if frames[stack[-1]].get("instruction_addr", "") == "0xffffffffc":
                return stack[:-2]
            return stack

    else:

        def truncate_stack_needed(
            frames: list[dict[str, Any]],
            stack: list[Any],
        ) -> list[Any]:
            return stack

    symbolicated_frames = stacktraces[0]["frames"]
    symbolicated_frames_dict = get_frame_index_map(symbolicated_frames)

    if len(frames_sent) > 0:
        raw_frames = profile["profile"]["frames"]
        new_frames = []
        symbolicated_frame_idx = 0

        for idx in range(len(raw_frames)):
            # If we didn't send the frame to symbolicator, add the raw frame.
            if idx not in frames_sent:
                new_frames.append(raw_frames[idx])
                continue

            # If we sent it to symbolicator, add the current symbolicated frame
            # to new_frames.
            # This works since symbolicated_frames are in the same order
            # as raw_frames (except some frames are not sent).
            for frame_idx in symbolicated_frames_dict[symbolicated_frame_idx]:
                new_frames.append(symbolicated_frames[frame_idx])

            # go to the next symbolicated frame result
            symbolicated_frame_idx += 1

        new_frames_count = (
            len(raw_frames)
            + sum([len(frames) for frames in symbolicated_frames_dict.values()])
            - len(symbolicated_frames_dict)
        )

        # in case we're dealing with a cocoa stack, we previously made a copy
        # of the leaf frame with adjust_instruction_addr = False.
        # If the original frame doesn't happen to shows up in the middle
        # of another stack, then it'll never be used.
        # Therefore we skip this sanity check for cocoa stacks
        if platform in SHOULD_SYMBOLICATE_JS:
            assert len(new_frames) == new_frames_count

        profile["profile"]["frames"] = new_frames
    elif symbolicated_frames:
        profile["profile"]["frames"] = symbolicated_frames

    if platform in SHOULD_SYMBOLICATE:

        def get_stack(stack: list[int]) -> list[int]:
            new_stack: list[int] = []
            for index in stack:
                if index in symbolicated_frames_dict:
                    # the new stack extends the older by replacing
                    # a specific frame index with the indices of
                    # the frames originated from the original frame
                    # should inlines be present
                    new_stack.extend(symbolicated_frames_dict[index])
                else:
                    new_stack.append(index)
            return new_stack

    else:

        def get_stack(stack: list[int]) -> list[int]:
            return stack

    stacks = []

    for stack in profile["profile"]["stacks"]:
        new_stack = get_stack(stack)

        if len(new_stack) >= 2:
            # truncate some unneeded frames in the stack (related to the profiler itself or impossible to symbolicate)
            new_stack = truncate_stack_needed(profile["profile"]["frames"], new_stack)

        stacks.append(new_stack)

    profile["profile"]["stacks"] = stacks


def _process_symbolicator_results_for_cocoa(profile: Profile, stacktraces: list[Any]) -> None:
    for original, symbolicated in zip(profile["sampled_profile"]["samples"], stacktraces):
        # remove bottom frames we can't symbolicate
        if (
            len(symbolicated["frames"]) > 1
            and symbolicated["frames"][-1].get("instruction_addr", "") == "0xffffffffc"
        ):
            original["frames"] = symbolicated["frames"][:-2]
        else:
            original["frames"] = symbolicated["frames"]


def _process_symbolicator_results_for_rust(profile: Profile, stacktraces: list[Any]) -> None:
    for original, symbolicated in zip(profile["sampled_profile"]["samples"], stacktraces):
        for frame in symbolicated["frames"]:
            frame.pop("pre_context", None)
            frame.pop("context_line", None)
            frame.pop("post_context", None)

        # exclude the top frames of the stack as it's related to the profiler itself and we don't want them.
        if (
            len(symbolicated["frames"]) > 1
            and symbolicated["frames"][0].get("function", "") == "perf_signal_handler"
        ):
            original["frames"] = symbolicated["frames"][2:]
        else:
            original["frames"] = symbolicated["frames"]


"""
This function returns a map {index: [indexes]} that will let us replace a specific
frame index with (potentially) a list of frames indices that originated from that frame.

The reason for this is that the frame from the SDK exists "physically",
and symbolicator then synthesizes other frames for calls that have been inlined
into the physical frame.

Example:

`
fn a() {
b()
}
fb b() {
fn c_inlined() {}
c_inlined()
}
`

this would yield the following from the SDK:
b -> a

after symbolication you would have:
c_inlined -> b -> a

The sorting order is callee to caller (child to parent)
"""


def get_frame_index_map(frames: list[dict[str, Any]]) -> dict[int, list[int]]:
    index_map: dict[int, list[int]] = {}
    for i, frame in enumerate(frames):
        # In case we don't have an `original_index` field, we default to using
        # the index of the frame in order to still produce a data structure
        # with the right shape.
        index_map.setdefault(frame.get("original_index", i), []).append(i)
    return index_map


@metrics.wraps("process_profile.deobfuscate_using_symbolicator")
def _deobfuscate_using_symbolicator(project: Project, profile: Profile, debug_file_id: str) -> bool:
    symbolication_start_time = time()

    def on_symbolicator_request():
        duration = time() - symbolication_start_time
        if duration > settings.SYMBOLICATOR_PROCESS_EVENT_HARD_TIMEOUT:
            raise SymbolicationTimeout

    symbolicator = Symbolicator(
        task_kind=SymbolicatorTaskKind(platform=SymbolicatorPlatform.jvm),
        on_request=on_symbolicator_request,
        project=project,
        event_id=profile["event_id"],
    )

    try:
        with sentry_sdk.start_span(op="task.profiling.deobfuscate.process_payload"):
            response = symbolicate(
                symbolicator=symbolicator,
                profile=profile,
                modules=[
                    {
                        "uuid": UUID(debug_file_id).hex,
                        "type": "proguard",
                    }
                ],
                stacktraces=[
                    {
                        "frames": convert_android_methods_to_jvm_frames(
                            profile["profile"]["methods"]
                        )
                    },
                ],
                platform=profile["platform"],
            )
            if response:
                deobfuscation_context = {}
                if response["status"] == "failed":
                    deobfuscation_context["status"] = response["status"]
                    deobfuscation_context["message"] = response["message"]
                if "errors" in response:
                    deobfuscation_context["errors"] = response["errors"]
                sentry_sdk.set_context("profile deobfuscation", deobfuscation_context)
                if "stacktraces" in response:
                    merge_jvm_frames_with_android_methods(
                        frames=response["stacktraces"][0]["frames"],
                        methods=profile["profile"]["methods"],
                    )
                    return True
            else:
                sentry_sdk.capture_message("No response from Symbolicator")
    except SymbolicationTimeout:
        metrics.incr("process_profile.symbolicate.timeout", sample_rate=1.0)
    sentry_sdk.capture_message("Deobfuscation via Symbolicator failed")
    return False


@metrics.wraps("process_profile.deobfuscate")
def _deobfuscate(profile: Profile, project: Project) -> None:
    debug_file_id = profile.get("build_id")
    if debug_file_id is None or debug_file_id == "":
        # we still need to decode signatures
        for m in profile["profile"]["methods"]:
            if m.get("signature"):
                types = deobfuscate_signature(m["signature"])
                m["signature"] = format_signature(types)
        return

    if project.id in options.get("profiling.deobfuscate-using-symbolicator.enable-for-project"):
        try:
            with sentry_sdk.start_span(op="deobfuscate_with_symbolicator"):
                success = _deobfuscate_using_symbolicator(
                    project=project,
                    profile=profile,
                    debug_file_id=debug_file_id,
                )
                sentry_sdk.set_tag("deobfuscated_with_symbolicator_with_success", success)
                if success:
                    return
        except Exception as e:
            sentry_sdk.capture_exception(e)
    else:
        _deobfuscate_locally(profile=profile, project=project, debug_file_id=debug_file_id)


@metrics.wraps("process_profile.deobfuscate.locally")
def _deobfuscate_locally(profile: Profile, project: Project, debug_file_id: str) -> None:
    with sentry_sdk.start_span(op="proguard.fetch_debug_files"):
        dif_paths = ProjectDebugFile.difcache.fetch_difs(
            project, [debug_file_id], features=["mapping"]
        )
        debug_file_path = dif_paths.get(debug_file_id)
        if debug_file_path is None:
            return

    mapper = open_proguard_mapper(debug_file_path, initialize_param_mapping=True)
    if not mapper.has_line_info:
        return

    with sentry_sdk.start_span(op="proguard.remap"):
        for method in profile["profile"]["methods"]:
            method.setdefault("data", {})
            types = None
            if method.get("signature"):
                types = deobfuscate_signature(method["signature"], mapper)
                method["signature"] = format_signature(types)

            # in case we don't have line numbers but we do have the signature,
            # we do a best-effort deobfuscation exploiting function parameters
            if (
                method.get("source_line") is None
                and method.get("signature") is not None
                and types is not None
            ):
                param_type, _ = types
                params = ",".join(param_type)
                mapped = mapper.remap_frame(method["class_name"], method["name"], 0, params)
            else:
                mapped = mapper.remap_frame(
                    method["class_name"], method["name"], method["source_line"] or 0
                )

            if len(mapped) >= 1:
                new_frame = mapped[-1]
                method["class_name"] = new_frame.class_name
                method["name"] = new_frame.method
                method["data"] = {
                    "deobfuscation_status": (
                        "deobfuscated" if method.get("signature", None) else "partial"
                    )
                }

                if new_frame.file:
                    method["source_file"] = new_frame.file

                if new_frame.line:
                    method["source_line"] = new_frame.line

                bottom_class = mapped[-1].class_name

                if method.get("source_line") is None and method.get("signature") is not None:
                    # if we used parameters-based deobfuscation we won't have to deal with
                    # inlines so we can just skip
                    continue

                method["inline_frames"] = [
                    {
                        "class_name": new_frame.class_name,
                        "data": {"deobfuscation_status": "deobfuscated"},
                        "name": new_frame.method,
                        "source_file": (
                            method["source_file"] if bottom_class == new_frame.class_name else ""
                        ),
                        "source_line": new_frame.line,
                    }
                    for new_frame in reversed(mapped)
                ]

                # vroom will only take into account frames in this list
                # if it exists. since symbolic does not return a signature for
                # the frame we deobfuscated, we update it to set
                # the deobfuscated signature.
                if len(method["inline_frames"]) > 0:
                    method["inline_frames"][0]["data"] = method["data"]
                    method["inline_frames"][0]["signature"] = method.get("signature", "")
            else:
                mapped_class = mapper.remap_class(method["class_name"])
                if mapped_class:
                    method["class_name"] = mapped_class
                    method["data"]["deobfuscation_status"] = "partial"
                else:
                    method["data"]["deobfuscation_status"] = "missing"


def get_event_id(profile: Profile) -> str | None:
    if "transaction_id" in profile:
        return profile["transaction_id"]
    elif "event_id" in profile:
        return profile["event_id"]
    elif "chunk_id" in profile:
        return profile["chunk_id"]
    return


def get_data_category(profile: Profile) -> DataCategory:
    if profile.get("version") == "2":
        return DataCategory.PROFILE_CHUNK
    return DataCategory.PROFILE_INDEXED


@metrics.wraps("process_profile.track_outcome")
def _track_outcome(
    profile: Profile,
    project: Project,
    outcome: Outcome,
    reason: str | None = None,
) -> None:
    if not project.flags.has_profiles:
        first_profile_received.send_robust(project=project, sender=Project)

    track_outcome(
        org_id=project.organization_id,
        project_id=project.id,
        key_id=None,
        outcome=outcome,
        reason=reason,
        timestamp=datetime.now(timezone.utc),
        event_id=get_event_id(profile),
        category=get_data_category(profile),
        quantity=1,
    )


@metrics.wraps("process_profile.insert_vroom_profile")
def _insert_vroom_profile(profile: Profile) -> bool:
    with sentry_sdk.start_span(op="task.profiling.insert_vroom"):
        try:
            response = get_from_profiling_service(method="POST", path="/profile", json_data=profile)

            if response.status == 204:
                return True
            elif response.status == 429:
                raise VroomTimeout
            elif response.status == 412:
                metrics.incr(
                    "process_profile.insert_vroom_profile.error",
                    tags={"platform": profile["platform"], "reason": "duplicate profile"},
                    sample_rate=1.0,
                )
                return False
            else:
                metrics.incr(
                    "process_profile.insert_vroom_profile.error",
                    tags={"platform": profile["platform"], "reason": "bad status"},
                    sample_rate=1.0,
                )
                return False
        except VroomTimeout:
            raise
        except Exception as e:
            sentry_sdk.capture_exception(e)
            metrics.incr(
                "process_profile.insert_vroom_profile.error",
                tags={"platform": profile["platform"], "reason": "encountered error"},
                sample_rate=1.0,
            )
            return False


def _push_profile_to_vroom(profile: Profile, project: Project) -> bool:
    if _insert_vroom_profile(profile=profile):
        return True
    _track_outcome(
        profile=profile,
        project=project,
        outcome=Outcome.INVALID,
        reason="profiling_failed_vroom_insertion",
    )
    return False


def prepare_android_js_profile(profile: Profile):
    profile["js_profile"] = {"profile": profile["js_profile"]}
    p = profile["js_profile"]
    p["platform"] = "javascript"
    p["debug_meta"] = profile["debug_meta"]
    p["version"] = "1"
    p["event_id"] = profile["event_id"]
    p["release"] = profile["release"]
    p["dist"] = profile["dist"]


def clean_android_js_profile(profile: Profile):
    p = profile["js_profile"]
    del p["platform"]
    del p["debug_meta"]
    del p["version"]
    del p["event_id"]
    del p["release"]
    del p["dist"]


@lru_cache(maxsize=100)
def get_metrics_dsn(project_id: int) -> str:
    project_key, _ = ProjectKey.objects.get_or_create(
        project_id=project_id, use_case=UseCase.PROFILING.value
    )
    return project_key.get_dsn(public=True)


@metrics.wraps("process_profile.track_outcome")
def _track_duration_outcome(
    profile: Profile,
    project: Project,
) -> None:
    duration_ms = _calculate_profile_duration_ms(profile)
    if duration_ms <= 0:
        return
    track_outcome(
        org_id=project.organization_id,
        project_id=project.id,
        key_id=None,
        outcome=Outcome.ACCEPTED,
        timestamp=datetime.now(timezone.utc),
        category=DataCategory.PROFILE_DURATION,
        quantity=duration_ms,
    )


def _calculate_profile_duration_ms(profile: Profile) -> int:
    version = profile.get("version")
    if version:
        if version == "1":
            return _calculate_duration_for_sample_format_v1(profile)
        elif version == "2":
            return _calculate_duration_for_sample_format_v2(profile)
    else:
        platform = profile["platform"]
        if platform == "android":
            return _calculate_duration_for_android_format(profile)
    return 0


def _calculate_duration_for_sample_format_v1(profile: Profile) -> int:
    start_ns = int(profile["transaction"].get("relative_start_ns", 0))
    end_ns = int(profile["transaction"].get("relative_end_ns", 0))
    duration_ns = end_ns - start_ns
    # try another method to determine the duration in case it's negative or 0.
    if duration_ns <= 0:
        samples = sorted(profile["profile"]["samples"], key=lambda s: s["elapsed_since_start_ns"])
        if len(samples) < 2:
            return 0
        first, last = samples[0], samples[-1]
        first_ns = int(first["elapsed_since_start_ns"])
        last_ns = int(last["elapsed_since_start_ns"])
        duration_ns = last_ns - first_ns
    duration_ms = int(duration_ns * 1e-6)
    return min(duration_ms, 30000)


def _calculate_duration_for_sample_format_v2(profile: Profile) -> int:
    samples = sorted(profile["profile"]["samples"], key=lambda s: s["timestamp"])
    if len(samples) < 2:
        return 0
    first, last = samples[0], samples[-1]
    return int((last["timestamp"] - first["timestamp"]) * 1e3)


def _calculate_duration_for_android_format(profile: Profile) -> int:
    return int(profile["duration_ns"] * 1e-6)
