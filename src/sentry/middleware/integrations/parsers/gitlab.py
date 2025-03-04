from __future__ import annotations

import logging

from django.http.response import HttpResponseBase
from django.urls import resolve

from sentry import options
from sentry.integrations.gitlab.webhooks import GitlabWebhookEndpoint, GitlabWebhookMixin
from sentry.integrations.utils.scope import clear_tags_and_context
from sentry.middleware.integrations.parsers.base import BaseRequestParser
from sentry.models.integrations.integration import Integration
from sentry.models.integrations.organization_integration import OrganizationIntegration
from sentry.models.outbox import WebhookProviderIdentifier
from sentry.ratelimits import backend as ratelimiter
from sentry.services.hybrid_cloud.integration.model import RpcIntegration
from sentry.services.hybrid_cloud.util import control_silo_function
from sentry.types.integrations import EXTERNAL_PROVIDERS, ExternalProviders
from sentry.utils import json

logger = logging.getLogger(__name__)


class GitlabRequestParser(BaseRequestParser, GitlabWebhookMixin):
    provider = EXTERNAL_PROVIDERS[ExternalProviders.GITLAB]
    webhook_identifier = WebhookProviderIdentifier.GITLAB
    _integration: Integration | None = None

    def _resolve_external_id(self) -> tuple[str, str] | HttpResponseBase:
        clear_tags_and_context()
        extra = {
            # This tells us the Gitlab version being used (e.g. current gitlab.com version -> GitLab/15.4.0-pre)
            "user-agent": self.request.META.get("HTTP_USER_AGENT"),
            # Gitlab does not seem to be the only host sending events
            # AppPlatformEvents also hit this API
            "event-type": self.request.META.get("HTTP_X_GITLAB_EVENT"),
        }
        return super()._get_external_id(request=self.request, extra=extra)

    @control_silo_function
    def get_integration_from_request(self) -> Integration | None:
        if self._integration:
            return self._integration
        if not self.is_json_request():
            return None
        try:
            _view, _args, kwargs = resolve(self.request.path)
            # Non-webhook endpoints
            if "integration_id" in kwargs and "organization_slug" in kwargs:
                self._integration = Integration.objects.filter(
                    id=kwargs["integration_id"],
                    organization_slug=kwargs["organization_slug"],
                ).first()
                return self._integration

            # Webhook endpoints
            result = self._resolve_external_id()
            if isinstance(result, tuple):
                (external_id, _secret) = result
                self._integration = Integration.objects.filter(
                    external_id=external_id, provider=self.provider
                ).first()
                return self._integration
        except Exception:
            pass

        return None

    def get_response_from_gitlab_webhook(self):
        maybe_http_response = self._resolve_external_id()
        if isinstance(maybe_http_response, HttpResponseBase):
            return maybe_http_response

        try:
            integration = self.get_integration_from_request()
            if not integration:
                return self.get_default_missing_integration_response()

            regions = self.get_regions_from_organizations()
        except (Integration.DoesNotExist, OrganizationIntegration.DoesNotExist):
            return self.get_default_missing_integration_response()

        identifier = self.get_mailbox_identifier(integration)
        return self.get_response_from_webhookpayload(
            regions=regions, identifier=identifier, integration_id=integration.id
        )

    def get_mailbox_identifier(self, integration: RpcIntegration) -> str:
        try:
            data = json.loads(self.request.body)
        except ValueError:
            data = {}
        enabled = options.get("hybridcloud.webhookpayload.use_mailbox_buckets")
        project_id = data.get("project", {}).get("id", None)
        if not project_id or not enabled:
            return str(integration.id)

        # If we get fewer than 3000 in 1 hour we don't need to split into buckets
        ratelimit_key = f"webhookpayload:{self.provider}:{integration.id}"
        if not ratelimiter.is_limited(key=ratelimit_key, window=60 * 60, limit=3000):
            return str(integration.id)

        # Split high volume integrations into 100 buckets.
        # 100 is arbitrary but we can't leave it unbounded.
        bucket_number = project_id % 100

        return f"{integration.id}:{bucket_number}"

    def get_response(self) -> HttpResponseBase:
        if self.view_class == GitlabWebhookEndpoint:
            return self.get_response_from_gitlab_webhook()
        return self.get_response_from_control_silo()
