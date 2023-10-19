import logging
import re

from django.db import router
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from sentry.api.api_owners import ApiOwner
from sentry.api.api_publish_status import ApiPublishStatus
from sentry.api.base import Endpoint, region_silo_endpoint
from sentry.api.permissions import TestPermission
from sentry.models.files.file import File
from sentry.models.relocation import Relocation, RelocationFile
from sentry.models.user import MAX_USERNAME_LENGTH
from sentry.services.hybrid_cloud.user.service import user_service
from sentry.utils.db import atomic_transaction

# Relocation input files are uploaded as tarballs, and chunked and stored using the normal
# `File`/`AbstractFile` mechanism, which has a hard limit of 2GiB, because we need to represent the
# offset into it as a 32-bit int. This means that the largest tarball we are able to import at this
# time is 2GiB. When validating this tarball, we will need to make a "composite object" from the
# uploaded blobs in Google Cloud Storage, which has a limit of 32 components. Thus, we get our blob
# size of the maximum overall file size (2GiB) divided by the maximum number of blobs (32): 65536MiB
# per blob.
RELOCATION_BLOB_SIZE = int((2**31) / 32)

ERR_DUPLICATE_RELOCATION = "An in-progress relocation already exists for this owner"
_filename_re = re.compile(r"[\n\t\r\f\v\\]")

logger = logging.getLogger(__name__)


class RelocationPostSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)
    orgs = serializers.ListField(required=True, allow_empty=False)
    owner = serializers.CharField(
        max_length=MAX_USERNAME_LENGTH, required=True, allow_blank=False, allow_null=False
    )


class RelocationMixin:
    @staticmethod
    def post_relocation(request, logger):
        serializer = RelocationPostSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        validated = serializer.validated_data
        fileobj = validated.get("file")
        owner_username = validated.get("owner")
        org_slugs = validated.get("orgs")
        try:
            owner = user_service.get_by_username(username=owner_username)[0]
        except IndexError:
            return Response({"detail": f"Could not find user `{owner_username}`"}, status=400)

        # Quickly check that this `owner` does not have more than one active `Relocation` in flight.
        if Relocation.objects.filter(
            owner=owner.id, status=Relocation.Status.IN_PROGRESS.value
        ).exists():
            return Response({"detail": ERR_DUPLICATE_RELOCATION}, status=409)

        # TODO(getsentry/team-ospo#203): check import size, and maybe do throttle based on that
        # information.

        headers = {"Content-Type": fileobj.content_type}
        for headerval in request.data.getlist("header") or ():
            try:
                k, v = headerval.split(":", 1)
            except ValueError:
                return Response({"detail": "header value was not formatted correctly"}, status=400)
            else:
                if _filename_re.search(v):
                    return Response(
                        {"detail": "header value must not contain special whitespace characters"},
                        status=400,
                    )
                headers[k] = v.strip()

        full_name = request.data.get("name", fileobj.name)
        if not full_name or full_name == "file":
            return Response({"detail": "File name must be specified"}, status=400)
        file_name = full_name.rsplit("/", 1)[-1]
        file = File.objects.create(name=file_name, type="relocation.file", headers=headers)
        file.putfile(fileobj, blob_size=RELOCATION_BLOB_SIZE, logger=logger)

        with atomic_transaction(
            using=(router.db_for_write(Relocation), router.db_for_write(RelocationFile))
        ):
            relocation = Relocation.objects.create(
                creator=request.user.id,
                owner=owner.id,
                want_org_slugs=org_slugs,
                step=Relocation.Step.UPLOADING.value,
            )
            RelocationFile.objects.create(
                relocation=relocation,
                file=file,
            )

        return Response(status=201)


@region_silo_endpoint
class RelocationEndpoint(Endpoint, RelocationMixin):
    owner = ApiOwner.RELOCATION
    publish_status = {
        "POST": ApiPublishStatus.EXPERIMENTAL,
    }
    # TODO(azaslavsky): this is clearly wrong
    permission_classes = (TestPermission,)
    # permission_classes = (SuperuserPermission,)
    # rate_limits = RateLimitConfig(
    #     group="CLI", limit_overrides={"GET": SENTRY_RATELIMITER_GROUP_DEFAULTS["default"]}
    # )

    def post(self, request: Request) -> Response:
        """
        Upload an encrypted export tarball for relocation.
        ``````````````````````````````````````````````````

        Upload an encrypted relocation tarball for relocation.

        This is currently an experimental API, and for the time being is only meant to be called by
        admins.

        :param file file: the multipart encoded tarball file.
        :param string owner: the username of the "owner" of this relocation; not necessarily
                             identical to the user who made the API call.
        :param list[string] orgs: A list of org slugs from those included in the associated
                                  encrypted backup tarball that should be imported.
        :auth: required
        """

        logger = logging.getLogger("sentry.files")
        logger.info("relocation.start")

        return self.post_relocation(request, logger)
