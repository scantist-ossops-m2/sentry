from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from django.conf import settings
from django.db import models, router, transaction
from django.utils import timezone

from sentry.backup.scopes import RelocationScope
from sentry.db.models import (
    BaseManager,
    FlexibleForeignKey,
    Model,
    region_silo_only_model,
    sane_repr,
)
from sentry.db.models.fields.hybrid_cloud_foreign_key import HybridCloudForeignKey
from sentry.models.grouphistory import GroupHistoryStatus, record_group_history
from sentry.models.groupowner import GroupOwner
from sentry.models.groupsubscription import GroupSubscription
from sentry.notifications.types import GroupSubscriptionReason
from sentry.signals import issue_assigned, issue_unassigned
from sentry.types.activity import ActivityType
from sentry.utils import metrics

if TYPE_CHECKING:
    from sentry.models.actor import ActorTuple
    from sentry.models.group import Group
    from sentry.models.team import Team
    from sentry.models.user import User
    from sentry.services.hybrid_cloud.user import RpcUser

logger = logging.getLogger(__name__)


class GroupAssigneeManager(BaseManager):
    def get_assigned_to_data(
        self, assigned_to: Team | RpcUser, assignee_type: str, extra: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        data = {
            "assignee": str(assigned_to.id),
            "assigneeEmail": getattr(assigned_to, "email", None),
            "assigneeType": assignee_type,
        }
        if extra:
            data.update(extra)

        return data

    def get_assignee_data(self, assigned_to: Team | RpcUser) -> tuple[str, str, str]:
        from sentry.models.team import Team
        from sentry.models.user import User
        from sentry.services.hybrid_cloud.user import RpcUser

        if isinstance(assigned_to, (User, RpcUser)):
            assignee_type = "user"
            assignee_type_attr = "user_id"
            other_type = "team"
        elif isinstance(assigned_to, Team):
            assignee_type = "team"
            assignee_type_attr = "team_id"
            other_type = "user_id"
        else:
            raise AssertionError(f"Invalid type to assign to: {type(assigned_to)}")

        return (assignee_type, assignee_type_attr, other_type)

    def assign(
        self,
        group: Group,
        assigned_to: Team | RpcUser,
        acting_user: User | None = None,
        create_only: bool = False,
        extra: Dict[str, str] | None = None,
        force_autoassign: bool = False,
    ):
        from sentry import features
        from sentry.integrations.utils import sync_group_assignee_outbound
        from sentry.models.activity import Activity
        from sentry.models.groupsubscription import GroupSubscription

        GroupSubscription.objects.subscribe_actor(
            group=group, actor=assigned_to, reason=GroupSubscriptionReason.assigned
        )

        assigned_to_id = assigned_to.id
        assignee_type, assignee_type_attr, other_type = self.get_assignee_data(assigned_to)

        now = timezone.now()
        assignee, created = self.get_or_create(
            group=group,
            defaults={
                "project": group.project,
                assignee_type_attr: assigned_to_id,
                "date_added": now,
            },
        )

        if not created:
            affected = not create_only and (
                self.filter(group=group)
                .exclude(**{assignee_type_attr: assigned_to_id})
                .update(**{assignee_type_attr: assigned_to_id, other_type: None, "date_added": now})
                or force_autoassign
            )
        else:
            affected = True

        if affected:
            transaction.on_commit(
                lambda: issue_assigned.send_robust(
                    project=group.project, group=group, user=acting_user, sender=self.__class__
                ),
                router.db_for_write(GroupAssignee),
            )
            data = self.get_assigned_to_data(assigned_to, assignee_type, extra)

            Activity.objects.create_group_activity(
                group,
                ActivityType.ASSIGNED,
                user=acting_user,
                data=data,
            )
            record_group_history(group, GroupHistoryStatus.ASSIGNED, actor=acting_user)

            metrics.incr("group.assignee.change", instance="assigned", skip_internal=True)
            # sync Sentry assignee to external issues
            if assignee_type == "user" and features.has(
                "organizations:integrations-issue-sync", group.organization, actor=acting_user
            ):
                sync_group_assignee_outbound(group, assigned_to.id, assign=True)

        return {"new_assignment": created, "updated_assignment": bool(not created and affected)}

    def deassign(
        self,
        group: Group,
        acting_user: User | RpcUser | None = None,
        assigned_to: Team | RpcUser | None = None,
        extra: Dict[str, str] | None = None,
    ) -> None:
        from sentry import features
        from sentry.integrations.utils import sync_group_assignee_outbound
        from sentry.models.activity import Activity
        from sentry.models.projectownership import ProjectOwnership
        from sentry.models.team import Team
        from sentry.services.hybrid_cloud.user.service import user_service

        previous_assignee: User | RpcUser | Team | None = None
        try:
            previous_groupassignee = self.get(group=group)
        except GroupAssignee.DoesNotExist:
            previous_groupassignee = None
        else:
            if previous_groupassignee.user_id:
                previous_assignee = user_service.get_user(previous_groupassignee.user_id)
            else:
                previous_assignee = Team.objects.get(id=previous_groupassignee.team.id)

        affected = self.filter(group=group)[:1].count()
        self.filter(group=group).delete()

        if affected > 0:
            if features.has("organizations:participants-purge", group.organization) and assigned_to:
                assignee_type, _, _ = self.get_assignee_data(assigned_to)
                data = self.get_assigned_to_data(assigned_to, assignee_type, extra)
                Activity.objects.create_group_activity(
                    group, ActivityType.ASSIGNED, user=acting_user, data=data
                )
            else:
                Activity.objects.create_group_activity(
                    group, ActivityType.UNASSIGNED, user=acting_user
                )

            record_group_history(group, GroupHistoryStatus.UNASSIGNED, actor=acting_user)

            # Clear ownership cache for the deassigned group
            ownership = ProjectOwnership.get_ownership_cached(group.project.id)
            if not ownership:
                ownership = ProjectOwnership(project_id=group.project.id)
            autoassignment_types = ProjectOwnership._get_autoassignment_types(ownership)
            if autoassignment_types:
                GroupOwner.invalidate_autoassigned_owner_cache(
                    group.project.id, autoassignment_types, group.id
                )
            GroupOwner.invalidate_assignee_exists_cache(group.project.id, group.id)
            GroupOwner.invalidate_debounce_issue_owners_evaluation_cache(group.project.id, group.id)

            if (
                features.has("organizations:participants-purge", group.organization)
                and previous_assignee
            ):
                if (
                    features.has("organizations:team-workflow-notifications", group.organization)
                    and type(previous_assignee) is Team
                ):
                    GroupSubscription.objects.filter(
                        group=group,
                        project=group.project,
                        team=previous_assignee,
                        reason=GroupSubscriptionReason.assigned,
                    ).delete()
                elif type(previous_assignee) is Team:
                    team_members = list(
                        previous_assignee.member_set.values_list("user_id", flat=True)
                    )
                    for member in team_members:
                        GroupSubscription.objects.filter(
                            group=group, project=group.project, user_id=member
                        ).delete()
                else:
                    GroupSubscription.objects.filter(
                        group=group, project=group.project, user_id=previous_assignee.id
                    ).delete()

            metrics.incr("group.assignee.change", instance="deassigned", skip_internal=True)
            # sync Sentry assignee to external issues
            if features.has(
                "organizations:integrations-issue-sync", group.organization, actor=acting_user
            ):
                sync_group_assignee_outbound(group, None, assign=False)

            issue_unassigned.send_robust(
                project=group.project, group=group, user=acting_user, sender=self.__class__
            )


@region_silo_only_model
class GroupAssignee(Model):
    """
    Identifies an assignment relationship between a user/team and an
    aggregated event (Group).
    """

    __relocation_scope__ = RelocationScope.Excluded

    objects = GroupAssigneeManager()

    project = FlexibleForeignKey("sentry.Project", related_name="assignee_set")
    group = FlexibleForeignKey("sentry.Group", related_name="assignee_set", unique=True)
    user_id = HybridCloudForeignKey(settings.AUTH_USER_MODEL, on_delete="CASCADE", null=True)
    team = FlexibleForeignKey("sentry.Team", related_name="sentry_assignee_set", null=True)
    date_added = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "sentry"
        db_table = "sentry_groupasignee"
        unique_together = [("project", "group")]

    __repr__ = sane_repr("group_id", "user_id", "team_id")

    def save(self, *args, **kwargs):
        assert not (self.user_id is not None and self.team_id is not None) and not (
            self.user_id is None and self.team_id is None
        ), "Must have Team or User, not both"
        super().save(*args, **kwargs)

    def assigned_actor_id(self) -> str:
        # TODO(mgaeta): Create migration for GroupAssignee to use the Actor model.
        if self.user_id:
            return f"user:{self.user_id}"

        if self.team:
            return f"team:{self.team_id}"

        raise NotImplementedError("Unknown Assignee")

    def assigned_actor(self) -> ActorTuple:
        from sentry.models.actor import ActorTuple

        return ActorTuple.from_actor_identifier(self.assigned_actor_id())
