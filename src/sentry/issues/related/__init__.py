"""This module exports a function to find related issues. It groups them by type."""

from typing import Any

from sentry.models.group import Group

from .same_root_cause import same_root_cause_analysis

__all__ = ["find_related_issues"]

RELATED_ISSUES_ALGORITHMS = {
    "same_root_cause": same_root_cause_analysis,
}


def find_related_issues(group: Group) -> list[dict[str, Any]]:
    related_issues = []
    for key, func in RELATED_ISSUES_ALGORITHMS.items():
        related_issues.append({"type": key, "data": func(group)})

    return related_issues
