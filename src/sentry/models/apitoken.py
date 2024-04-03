from __future__ import annotations

import hashlib
import secrets
from collections.abc import Collection
from datetime import timedelta
from typing import Any, ClassVar

from django.db import models, router, transaction
from django.utils import timezone
from django.utils.encoding import force_str

from sentry import options
from sentry.backup.dependencies import ImportKind
from sentry.backup.helpers import ImportFlags
from sentry.backup.scopes import ImportScope, RelocationScope
from sentry.constants import SentryAppStatus
from sentry.db.models import FlexibleForeignKey, control_silo_only_model, sane_repr
from sentry.db.models.outboxes import ControlOutboxProducingManager, ReplicatedControlModel
from sentry.models.apiscopes import HasApiScopes
from sentry.models.outbox import OutboxCategory
from sentry.types.region import find_all_region_names
from sentry.types.token import AuthTokenType

DEFAULT_EXPIRATION = timedelta(days=30)


def default_expiration():
    return timezone.now() + DEFAULT_EXPIRATION


def generate_token():
    return secrets.token_hex(nbytes=32)


class PlaintextSecretAlreadyRead(Exception):
    def __init__(
        self,
        message="the secret you are trying to read is read-once and cannot be accessed directly again",
    ):
        super().__init__(message)


class ApiTokenManager(ControlOutboxProducingManager):
    def create(self, *args, **kwargs):
        token_type: AuthTokenType | None = kwargs.get("token_type", None)

        # Typically the .create() method is called with `refresh_token=None` as an
        # argument when we specifically do not want a refresh_token.
        #
        # But if it is not None or not specified, we should generate a token since
        # that is the expected behavior... the refresh_token field on ApiToken has
        # a default of generate_token()
        #
        # TODO(mdtro): All of these if/else statements will be cleaned up at a later time
        # to use a match statment on the AuthTokenType. Move each of the various token type
        # create calls one at a time.
        if "refresh_token" in kwargs:
            plaintext_refresh_token = kwargs["refresh_token"]
        else:
            plaintext_refresh_token = generate_token()

        if token_type == AuthTokenType.USER:
            plaintext_token = f"{token_type}{generate_token()}"
            plaintext_refresh_token = None  # user auth tokens do not have refresh tokens
        else:
            plaintext_token = generate_token()

        if options.get("apitoken.save-hash-on-create"):
            kwargs["hashed_token"] = hashlib.sha256(plaintext_token.encode()).hexdigest()

            if plaintext_refresh_token is not None:
                kwargs["hashed_refresh_token"] = hashlib.sha256(
                    plaintext_refresh_token.encode()
                ).hexdigest()

        kwargs["token"] = plaintext_token
        kwargs["refresh_token"] = plaintext_refresh_token

        if plaintext_refresh_token is not None:
            kwargs["refresh_token"] = plaintext_refresh_token
            kwargs["hashed_refresh_token"] = hashlib.sha256(
                plaintext_refresh_token.encode()
            ).hexdigest()

        api_token = super().create(*args, **kwargs)

        # Store the plaintext tokens for one-time retrieval
        api_token.__plaintext_token = plaintext_token
        api_token.__plaintext_refresh_token = plaintext_refresh_token

        return api_token


@control_silo_only_model
class ApiToken(ReplicatedControlModel, HasApiScopes):
    __relocation_scope__ = {RelocationScope.Global, RelocationScope.Config}
    category = OutboxCategory.API_TOKEN_UPDATE

    # users can generate tokens without being application-bound
    application = FlexibleForeignKey("sentry.ApiApplication", null=True)
    user = FlexibleForeignKey("sentry.User")
    name = models.CharField(max_length=255, null=True)
    token = models.CharField(max_length=71, unique=True, default=generate_token)
    hashed_token = models.CharField(max_length=128, unique=True, null=True)
    token_type = models.CharField(max_length=7, choices=AuthTokenType, null=True)
    token_last_characters = models.CharField(max_length=4, null=True)
    refresh_token = models.CharField(max_length=71, unique=True, null=True, default=generate_token)
    hashed_refresh_token = models.CharField(max_length=128, unique=True, null=True)
    expires_at = models.DateTimeField(null=True, default=default_expiration)
    date_added = models.DateTimeField(default=timezone.now)

    objects: ClassVar[ControlOutboxProducingManager[ApiToken]] = ApiTokenManager(
        cache_fields=("token",)
    )

    class Meta:
        app_label = "sentry"
        db_table = "sentry_apitoken"

    __repr__ = sane_repr("user_id", "token", "application_id")

    def __str__(self):
        return force_str(self.token)

    @property
    def _plaintext_token(self):
        """
        To be called immediately after creation of a new token to return the
        plaintext token to the user. After reading the token, it will be set
        to `None` to prevent future accidental leaking of the token in logs,
        exceptions, etc.
        """
        manager_class_name = self.objects.__class__.__name__
        plaintext_token: str | None = getattr(self, f"_{manager_class_name}__plaintext_token", None)

        if plaintext_token is not None:
            setattr(self, f"_{manager_class_name}__plaintext_token", None)
        else:
            raise PlaintextSecretAlreadyRead()

        return plaintext_token

    @property
    def _plaintext_refresh_token(self):
        """
        To be called immediately after creation of a new token to return the
        plaintext refresh token to the user. After reading the refresh token, it will be set
        to `None` to prevent future accidental leaking of the refresh token in logs,
        exceptions, etc.
        """
        manager_class_name = self.objects.__class__.__name__
        plaintext_refresh_token: str | None = getattr(
            self, f"_{manager_class_name}__plaintext_refresh_token", None
        )

        if plaintext_refresh_token:
            setattr(self, f"_{manager_class_name}__plaintext_refresh_token", None)

        # some token types do not have refresh tokens, so we check to see
        # if there's a hash value that exists for the refresh token.
        #
        # if there is a hash value, then a refresh token is expected
        # and if the plaintext_refresh_token is None, then it has already
        # been read once so we should throw the exception
        if not plaintext_refresh_token and self.refresh_token:
            raise PlaintextSecretAlreadyRead()

        return plaintext_refresh_token

    def save(self, *args: Any, **kwargs: Any) -> None:
        if options.get("apitoken.auto-add-last-chars"):
            token_last_characters = self.token[-4:]
            self.token_last_characters = token_last_characters

        return super().save(**kwargs)

    def outbox_region_names(self) -> Collection[str]:
        return list(find_all_region_names())

    def handle_async_replication(self, region_name: str, shard_identifier: int) -> None:
        from sentry.services.hybrid_cloud.auth.serial import serialize_api_token
        from sentry.services.hybrid_cloud.replica import region_replica_service

        region_replica_service.upsert_replicated_api_token(
            api_token=serialize_api_token(self),
            region_name=region_name,
        )

    @classmethod
    def from_grant(cls, grant):
        with transaction.atomic(router.db_for_write(cls)):
            return cls.objects.create(
                application=grant.application, user=grant.user, scope_list=grant.get_scopes()
            )

    def is_expired(self):
        if not self.expires_at:
            return False

        return timezone.now() >= self.expires_at

    def get_audit_log_data(self):
        return {"scopes": self.get_scopes()}

    def get_allowed_origins(self):
        if self.application:
            return self.application.get_allowed_origins()
        return ()

    def refresh(self, expires_at=None):
        if expires_at is None:
            expires_at = timezone.now() + DEFAULT_EXPIRATION

        self.update(token=generate_token(), refresh_token=generate_token(), expires_at=expires_at)

    def get_relocation_scope(self) -> RelocationScope:
        if self.application_id is not None:
            # TODO(getsentry/team-ospo#188): this should be extension scope once that gets added.
            return RelocationScope.Global

        return RelocationScope.Config

    def write_relocation_import(
        self, scope: ImportScope, flags: ImportFlags
    ) -> tuple[int, ImportKind] | None:
        # If there is a token collision, generate new tokens.
        query = models.Q(token=self.token) | models.Q(
            refresh_token__isnull=False, refresh_token=self.refresh_token
        )
        existing = self.__class__.objects.filter(query).first()
        if existing:
            self.token = generate_token()
            if self.refresh_token is not None:
                self.refresh_token = generate_token()
            if self.expires_at is not None:
                self.expires_at = timezone.now() + DEFAULT_EXPIRATION

        return super().write_relocation_import(scope, flags)

    @property
    def organization_id(self) -> int | None:
        from sentry.models.integrations.sentry_app_installation import SentryAppInstallation
        from sentry.models.integrations.sentry_app_installation_token import (
            SentryAppInstallationToken,
        )

        try:
            installation = SentryAppInstallation.objects.get_by_api_token(self.id).get()
        except SentryAppInstallation.DoesNotExist:
            installation = None

        # TODO(nisanthan): Right now, Internal Integrations can have multiple ApiToken, so we use the join table `SentryAppInstallationToken` to map the one to many relationship. However, for Public Integrations, we can only have 1 ApiToken per installation. So we currently don't use the join table for Public Integrations. We should update to make records in the join table for Public Integrations so that we can have a common abstraction for finding an installation by ApiToken.
        if not installation or installation.sentry_app.status == SentryAppStatus.INTERNAL:
            try:
                install_token = SentryAppInstallationToken.objects.select_related(
                    "sentry_app_installation"
                ).get(api_token_id=self.id)
            except SentryAppInstallationToken.DoesNotExist:
                return None
            return install_token.sentry_app_installation.organization_id

        return installation.organization_id


def is_api_token_auth(auth: object) -> bool:
    """:returns True when an API token is hitting the API."""
    from sentry.hybridcloud.models.apitokenreplica import ApiTokenReplica
    from sentry.services.hybrid_cloud.auth import AuthenticatedToken

    if isinstance(auth, AuthenticatedToken):
        return auth.kind == "api_token"
    return isinstance(auth, ApiToken) or isinstance(auth, ApiTokenReplica)
