from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from sentry_sdk import Scope

from sentry import options
from sentry.api.base import Endpoint
from sentry.api.exceptions import ProjectMoved, ResourceDoesNotExist
from sentry.api.helpers.environments import get_environments
from sentry.api.permissions import StaffPermissionMixin
from sentry.api.utils import get_date_range_from_params
from sentry.constants import ObjectStatus
from sentry.exceptions import InvalidParams
from sentry.models.project import Project
from sentry.models.projectredirect import ProjectRedirect
from sentry.utils.sdk import bind_organization_context, configure_scope

from .organization import OrganizationPermission


class ProjectEventsError(Exception):
    pass


class ProjectPermission(OrganizationPermission):
    scope_map = {
        "GET": ["project:read", "project:write", "project:admin"],
        "POST": ["project:write", "project:admin"],
        "PUT": ["project:write", "project:admin"],
        "DELETE": ["project:admin"],
    }

    def has_object_permission(self, request: Request, view, project):
        has_org_scope = super().has_object_permission(request, view, project.organization)

        # If allow_joinleave is False, some org-roles will not have project:read for all projects
        if has_org_scope and request.access.has_project_access(project):
            return has_org_scope

        allowed_scopes = set(self.scope_map.get(request.method, []))
        return request.access.has_any_project_scope(project, allowed_scopes)


class ProjectAndStaffPermission(StaffPermissionMixin, ProjectPermission):
    """Allows staff to access project endpoints."""

    pass


class StrictProjectPermission(ProjectPermission):
    scope_map = {
        "GET": ["project:write", "project:admin"],
        "POST": ["project:write", "project:admin"],
        "PUT": ["project:write", "project:admin"],
        "DELETE": ["project:admin"],
    }


class ProjectReleasePermission(ProjectPermission):
    scope_map = {
        "GET": ["project:read", "project:write", "project:admin", "project:releases", "org:ci"],
        "POST": ["project:write", "project:admin", "project:releases", "org:ci"],
        "PUT": ["project:write", "project:admin", "project:releases", "org:ci"],
        "DELETE": ["project:admin", "project:releases"],
    }


class ProjectEventPermission(ProjectPermission):
    scope_map = {
        "GET": ["event:read", "event:write", "event:admin"],
        "POST": ["event:write", "event:admin"],
        "PUT": ["event:write", "event:admin"],
        "DELETE": ["event:admin"],
    }


class ProjectSettingPermission(ProjectPermission):
    scope_map = {
        "GET": ["project:read", "project:write", "project:admin"],
        "POST": ["project:write", "project:admin"],
        "PUT": ["project:write", "project:admin"],
        "DELETE": ["project:write", "project:admin"],
    }


class ProjectAlertRulePermission(ProjectPermission):
    scope_map = {
        "GET": ["project:read", "project:write", "project:admin", "alerts:read", "alerts:write"],
        "POST": ["project:write", "project:admin", "alerts:write"],
        "PUT": ["project:write", "project:admin", "alerts:write"],
        "DELETE": ["project:write", "project:admin", "alerts:write"],
    }


class ProjectOwnershipPermission(ProjectPermission):
    scope_map = {
        "GET": ["project:read", "project:write", "project:admin"],
        "POST": ["project:write", "project:admin"],
        "PUT": ["project:read", "project:write", "project:admin"],
        "DELETE": ["project:admin"],
    }


class ProjectEndpoint(Endpoint):
    permission_classes: tuple[type[BasePermission], ...] = (ProjectPermission,)

    def convert_args(
        self,
        request: Request,
        organization_slug: str | int,
        project_slug: str | int,
        *args,
        **kwargs,
    ):
        try:
            if options.get("api.id-or-slug-enabled"):
                project = (
                    Project.objects.filter(
                        organization__slug__id_or_slug=organization_slug,
                        slug__id_or_slug=project_slug,
                    )
                    .select_related("organization")
                    .prefetch_related("teams")
                    .get()
                )
            else:
                project = (
                    Project.objects.filter(organization__slug=organization_slug, slug=project_slug)
                    .select_related("organization")
                    .prefetch_related("teams")
                    .get()
                )
        except Project.DoesNotExist:
            try:
                # Project may have been renamed
                redirect = ProjectRedirect.objects.select_related("project")
                if options.get("api.id-or-slug-enabled"):
                    redirect = redirect.get(
                        organization__id=organization_slug,
                        redirect_slug__id_or_slug=project_slug,
                    )
                else:
                    redirect = redirect.get(
                        organization__slug=organization_slug, redirect_slug=project_slug
                    )
                # Without object permissions don't reveal the rename
                self.check_object_permissions(request, redirect.project)

                # get full path so that we keep query strings
                requested_url = request.get_full_path()
                new_url = requested_url.replace(
                    f"projects/{organization_slug}/{project_slug}/",
                    f"projects/{organization_slug}/{redirect.project.slug}/",
                )

                # Resource was moved/renamed if the requested url is different than the new url
                if requested_url != new_url:
                    raise ProjectMoved(new_url, redirect.project.slug)

                # otherwise project doesn't exist
                raise ResourceDoesNotExist
            except ProjectRedirect.DoesNotExist:
                raise ResourceDoesNotExist

        if project.status != ObjectStatus.ACTIVE:
            raise ResourceDoesNotExist

        self.check_object_permissions(request, project)

        with configure_scope() as scope:
            scope.set_tag("project", project.id)

        bind_organization_context(project.organization)

        request._request.organization = project.organization

        kwargs["project"] = project
        return (args, kwargs)

    def get_filter_params(self, request: Request, project, date_filter_optional=False):
        """Similar to the version on the organization just for a single project."""
        # get the top level params -- projects, time range, and environment
        # from the request
        try:
            start, end = get_date_range_from_params(request.GET, optional=date_filter_optional)
        except InvalidParams as e:
            raise ProjectEventsError(str(e))

        environments = [env.name for env in get_environments(request, project.organization)]
        params = {"start": start, "end": end, "project_id": [project.id]}
        if environments:
            params["environment"] = environments

        return params

    def handle_exception(
        self,
        request: Request,
        exc: Exception,
        handler_context: Mapping[str, Any] | None = None,
        scope: Scope | None = None,
    ) -> Response:
        if isinstance(exc, ProjectMoved):
            response = Response(
                {"slug": exc.detail["detail"]["extra"]["slug"], "detail": exc.detail["detail"]},
                status=exc.status_code,
            )
            response["Location"] = exc.detail["detail"]["extra"]["url"]
            return response
        return super().handle_exception(request, exc, handler_context, scope)
