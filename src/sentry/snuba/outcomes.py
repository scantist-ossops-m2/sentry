from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any, Generic, TypeVar

from django.http import QueryDict
from snuba_sdk import Request
from snuba_sdk.column import Column
from snuba_sdk.conditions import Condition, Op
from snuba_sdk.entity import Entity
from snuba_sdk.expressions import Granularity, Limit, Offset
from snuba_sdk.function import Function
from snuba_sdk.query import Query

from sentry.api.utils import get_date_range_from_params
from sentry.constants import DataCategory
from sentry.release_health.base import AllowedResolution
from sentry.search.utils import InvalidQuery
from sentry.sentry_metrics.querying.data import MetricsQueriesPlan, run_metrics_queries_plan
from sentry.sentry_metrics.querying.data.transformation.stats import MetricsStatsTransformer
from sentry.snuba.referrer import Referrer
from sentry.snuba.sessions_v2 import (
    InvalidField,
    SimpleGroupBy,
    get_constrained_date_range,
    massage_sessions_result,
)
from sentry.utils.dates import parse_stats_period
from sentry.utils.outcomes import Outcome
from sentry.utils.snuba import raw_snql_query

from .dataset import Dataset

"""
The new Outcomes API defines a "metrics"-like interface which is can be used in
a similar way to "sessions" and "discover"

We have 2 "fields" that we can query:

- `sum(quantity)`: The relevant stat based on category:
        standard event: # of outcomes
        session: number of events within the session
        attachment: quantity in bytes of attachments
- `sum(times_seen)`: The number of outcomes that occurred --
        differs from quantity in that:
            it would count # of attachments, and sessions
            if we ever aggregate multiple events into one outcome,
            this would be used to get the number of outcomes

# Tags / Grouping

The Outcomes data can be grouped / filtered
by these fields
- `project`
- `outcome`
- `reason`
- `category`
- `key_id`
"""

ResultSet = list[dict[str, Any]]
QueryCondition = tuple[str, str, list[Any]]

T = TypeVar("T")


class Field(ABC):
    @abstractmethod
    def get_snuba_columns(self, raw_groupby: Sequence[str] | None = None) -> list[str]:
        raise NotImplementedError()

    @abstractmethod
    def extract_from_row(
        self, row: Mapping[str, Any] | None, group: Mapping[str, Any] | None = None
    ) -> int:
        raise NotImplementedError()

    @abstractmethod
    def select_params(self, dataset: Dataset) -> Function:
        raise NotImplementedError()


class QuantityField(Field):
    def get_snuba_columns(self, raw_groupby: Sequence[str] | None = None) -> list[str]:
        return ["quantity"]

    def extract_from_row(
        self, row: Mapping[str, Any] | None, group: Mapping[str, Any] | None = None
    ) -> int:
        if row is None:
            return 0
        return int(row["quantity"])

    def select_params(self, dataset: Dataset) -> Function:
        return Function("sum", [Column("quantity")], "quantity")


class TimesSeenField(Field):
    def get_snuba_columns(self, raw_groupby: Sequence[str] | None = None) -> list[str]:
        return ["times_seen"]

    def extract_from_row(
        self, row: Mapping[str, Any] | None, group: Mapping[str, Any] | None = None
    ) -> int:
        if row is None:
            return 0
        return int(row["times_seen"])

    def select_params(self, dataset: Dataset) -> Function:
        if dataset == Dataset.Outcomes:
            return Function("sum", [Column("times_seen")], "times_seen")
        else:
            # RawOutcomes doesnt have times_seen, do a count instead
            return Function("count", [], "times_seen")


class Dimension(SimpleGroupBy, ABC, Generic[T]):
    @abstractmethod
    def resolve_filter(self, raw_filter: Sequence[str]) -> list[T]:
        """
        Based on the input filter, map it back to the clickhouse representation
        """
        raise NotImplementedError()

    @abstractmethod
    def map_row(self, row: MutableMapping[str, Any]) -> None:
        """
        map clickhouse representation back to presentation
        """
        raise NotImplementedError()


class CategoryDimension(Dimension[DataCategory]):
    def resolve_filter(self, raw_filter: Sequence[str]) -> list[DataCategory]:
        resolved_categories = set()
        for category in raw_filter:
            # combine DEFAULT, ERROR, and SECURITY as errors.
            # see relay: py/sentry_relay/consts.py and relay-cabi/include/relay.h
            parsed_category = DataCategory.parse(category)
            if parsed_category is None and parsed_category != "metrics":
                raise InvalidField(f'Invalid category: "{category}"')
            elif parsed_category == DataCategory.ERROR:
                resolved_categories.update(DataCategory.error_categories())
            else:
                resolved_categories.add(parsed_category)
        if DataCategory.ATTACHMENT in resolved_categories and len(resolved_categories) > 1:
            raise InvalidQuery("if filtering by attachment no other category may be present")
        return list(resolved_categories)

    def map_row(self, row: MutableMapping[str, Any]) -> None:
        if "category" in row:
            category = (
                DataCategory.ERROR
                if row["category"] in DataCategory.error_categories()
                else DataCategory(row["category"])
            )
            row["category"] = category.api_name()


class OutcomeDimension(Dimension[Outcome]):
    def resolve_filter(self, raw_filter: Sequence[str]) -> list[Outcome]:
        def _parse_outcome(outcome: str) -> Outcome:
            try:
                return Outcome.parse(outcome)
            except KeyError:
                raise InvalidField(f'Invalid outcome: "{outcome}"')

        return [_parse_outcome(o) for o in raw_filter]

    def map_row(self, row: MutableMapping[str, Any]) -> None:
        if "outcome" in row:
            row["outcome"] = Outcome(row["outcome"]).api_name()


class KeyDimension(Dimension[int]):
    def resolve_filter(self, raw_filter: Sequence[str]) -> list[int]:
        def _parse_value(key_id: str) -> int:
            try:
                return int(key_id)
            except ValueError:
                raise InvalidQuery(f'Invalid key: "{key_id}"')

        return [_parse_value(o) for o in raw_filter]

    def map_row(self, row: MutableMapping[str, Any]) -> None:
        # No changes are required to map key_id values.
        pass


class ReasonDimension(Dimension):
    def resolve_filter(self, raw_filter: Sequence[str]) -> list[str]:
        return [
            "smart_rate_limit" if reason == "spike_protection" else reason for reason in raw_filter
        ]

    def map_row(self, row: MutableMapping[str, Any]) -> None:
        if "reason" in row:
            row["reason"] = (
                "spike_protection" if row["reason"] == "smart_rate_limit" else row["reason"]
            )  # return spike protection to be consistent with naming in other places


COLUMN_MAP = {
    "sum(quantity)": QuantityField(),
    "sum(times_seen)": TimesSeenField(),
}

DIMENSION_MAP: Mapping[str, Dimension] = {
    "outcome": OutcomeDimension("outcome"),
    "category": CategoryDimension("category"),
    "reason": ReasonDimension("reason"),
    "key_id": KeyDimension("key_id"),
}

GROUPBY_MAP = {
    **DIMENSION_MAP,
    "project": SimpleGroupBy("project_id", "project"),
}
# We don't have any scenarios where we need to group by key right now.
GROUPBY_MAP.pop("key_id")

TS_COL = "time"

ONE_HOUR = 3600


class QueryDefinition:
    """
    This is the definition of the query the user wants to execute.
    This is constructed out of the request params, and also contains a list of
    `fields` and `groupby` definitions as [`ColumnDefinition`] objects.
    """

    @classmethod
    def from_query_dict(
        cls,
        query: QueryDict,
        params: Mapping[Any, Any],
        allow_minute_resolution: bool | None = True,
    ) -> QueryDefinition:
        """
        Create a QueryDefinition from a Django request QueryDict

        Useful when you want to convert request data into an outcomes.QueryDefinition.
        """
        return QueryDefinition(
            fields=query.getlist("field", []) or [],
            start=query.get("start", None),
            end=query.get("end", None),
            stats_period=query.get("statsPeriod", None),
            organization_id=params.get("organization_id", None),
            project_ids=params.get("project_id", None),
            key_id=query.get("key_id", None),
            interval=query.get("interval"),
            outcome=query.getlist("outcome", []),
            group_by=query.getlist("groupBy", []),
            category=query.getlist("category"),
            reason=query.get("reason"),
            allow_minute_resolution=allow_minute_resolution,
        )

    def __init__(
        self,
        fields: list[str],
        start: str | None = None,
        end: str | None = None,
        stats_period: str | None = None,
        organization_id: int | None = None,
        project_ids: list[int] | None = None,
        key_id: str | int | None = None,
        interval: str | None = None,
        outcome: list[str] | None = None,
        group_by: list[str] | None = None,
        category: list[str] | None = None,
        reason: str | None = None,
        allow_minute_resolution: bool | None = True,
    ):
        params: MutableMapping[str, Any] = {"organization_id": organization_id}
        if project_ids is not None:
            params["project_id"] = project_ids

        if len(fields) == 0:
            raise InvalidField('At least one "field" is required.')
        self.fields = {}
        self.query: list[Any] = []  # not used but needed for compat with sessions logic
        allowed_resolution = (
            AllowedResolution.one_minute if allow_minute_resolution else AllowedResolution.one_hour
        )
        date_params = {
            "start": start,
            "end": end,
            "interval": interval or "",
            "statsPeriod": stats_period,
        }

        self.start, self.end, self.rollup = get_constrained_date_range(
            date_params, allowed_resolution
        )
        self.dataset, self.match = _outcomes_dataset(self.rollup)
        self.select_params = []
        for key in fields:
            if key not in COLUMN_MAP:
                raise InvalidField(f'Invalid field: "{key}"')
            field = COLUMN_MAP[key]
            self.select_params.append(field.select_params(self.dataset))
            self.fields[key] = field

        group_by = group_by or []
        self.groupby = []
        for key in group_by:
            if key not in GROUPBY_MAP:
                raise InvalidField(f'Invalid groupBy: "{key}"')
            self.groupby.append(GROUPBY_MAP[key])

        if (category is None or len(category) == 0) and "category" not in group_by:
            raise InvalidQuery("Query must have category as groupby or filter")

        query_columns = set()
        for field in self.fields.values():
            query_columns.update(field.get_snuba_columns(group_by))
        for groupby in self.groupby:
            query_columns.update(groupby.get_snuba_columns())
        self.query_columns = list(query_columns)

        query_groupby = set()
        for groupby in self.groupby:
            query_groupby.update(groupby.get_snuba_groupby())
        self.query_groupby = list(query_groupby)

        self.group_by = []
        for key in self.query_groupby:
            self.group_by.append(Column(key))

        condition_data = {
            "outcome": outcome,
            "key_id": key_id,
            "category": category,
            "reason": reason,
        }
        self.conditions = self.get_conditions(condition_data, params)

    def get_conditions(self, query: Mapping[str, Any], params: Mapping[Any, Any]) -> list[Any]:
        query_conditions = [
            Condition(Column("timestamp"), Op.GTE, self.start),
            Condition(Column("timestamp"), Op.LT, self.end),
        ]
        for filter_name in DIMENSION_MAP:
            raw_filter = query.get(filter_name, []) or []
            if not isinstance(raw_filter, list):
                raw_filter = [raw_filter]
            resolved_filter = DIMENSION_MAP[filter_name].resolve_filter(raw_filter)
            if len(resolved_filter) > 0:
                query_conditions.append(Condition(Column(filter_name), Op.IN, resolved_filter))
        if "project_id" in params:
            query_conditions.append(
                Condition(Column("project_id"), Op.IN, params["project_id"]),
            )
        if "organization_id" in params:
            query_conditions.append(
                Condition(Column("org_id"), Op.EQ, params["organization_id"]),
            )
        return query_conditions


def run_outcomes_query_totals(
    query: QueryDefinition,
    *,
    tenant_ids: dict[str, int | str],
) -> ResultSet:
    snql_query = Query(
        match=Entity(query.match),
        select=query.select_params,
        groupby=query.group_by,
        where=query.conditions,
        limit=Limit(10000),
        offset=Offset(0),
        granularity=Granularity(query.rollup),
    )
    request = Request(
        dataset=query.dataset.value, app_id="default", query=snql_query, tenant_ids=tenant_ids
    )
    result = raw_snql_query(request, referrer="outcomes.totals")
    return _format_rows(result["data"], query)


def run_outcomes_query_timeseries(
    query: QueryDefinition,
    *,
    tenant_ids: dict[str, int | str],
    referrer: str = "outcomes.timeseries",
) -> ResultSet:
    """
    Runs an outcomes query. By default the referrer is `outcomes.timeseries` and this should not change
    unless there is a very specific reason to do so. Eg. getsentry uses this function for billing
    metrics, so the referrer is different as it's no longer a "product" query.
    """
    snql_query = Query(
        match=Entity(query.match),
        select=query.select_params,
        groupby=query.group_by + [Column(TS_COL)],
        where=query.conditions,
        limit=Limit(10000),
        offset=Offset(0),
        granularity=Granularity(query.rollup),
    )
    request = Request(
        dataset=query.dataset.value, app_id="default", query=snql_query, tenant_ids=tenant_ids
    )
    result_timeseries = raw_snql_query(request, referrer=referrer)
    return _format_rows(result_timeseries["data"], query)


def _format_rows(rows: ResultSet, query: QueryDefinition) -> ResultSet:
    category_grouping: dict[str, Any] = {}

    def _group_row(row: dict[str, Any]) -> None:
        # Combine rows with the same group key into one.
        # Needed to combine "ERROR", "DEFAULT" and "SECURITY" rows and sum aggregations.
        if TS_COL in row:
            grouping_key = "-".join([row[TS_COL]] + [str(row[col]) for col in query.query_groupby])
        else:
            grouping_key = "-".join(str(row[col]) for col in query.query_groupby)

        if grouping_key in category_grouping:
            for field_name, field in query.fields.items():
                row_field = field.get_snuba_columns()[0]
                category_grouping[grouping_key][row_field] += field.extract_from_row(row)
        else:
            category_grouping[grouping_key] = row
        return

    for row in rows:
        _rename_row_fields(row)
        _group_row(row)

    return list(category_grouping.values())


def _rename_row_fields(row: dict[str, Any]) -> None:
    for dimension in DIMENSION_MAP:
        DIMENSION_MAP[dimension].map_row(row)


def _outcomes_dataset(rollup: int) -> tuple[Dataset, str]:
    if rollup >= ONE_HOUR:
        # "Outcomes" is the hourly rollup table
        dataset = Dataset.Outcomes
        match = "outcomes"
    else:
        dataset = Dataset.OutcomesRaw
        match = "outcomes_raw"
    return dataset, match


def massage_outcomes_result(
    query: QueryDefinition,
    result_totals: ResultSet,
    result_timeseries: ResultSet | None,
) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = massage_sessions_result(
        query, result_totals, result_timeseries, ts_col=TS_COL
    )
    if result_timeseries is None:
        del result["intervals"]
    del result["query"]
    return result


def run_metrics_outcomes_query(
    query: QueryDict, organization, projects, environments
) -> dict[str, list]:
    start, end = get_date_range_from_params(query)
    interval = parse_stats_period(query.get("interval", "1h"))

    # TODO(metrics): add support for reason tag
    plan = MetricsQueriesPlan().apply_formula("sum(c:metric_stats/volume@none) by (outcome.id)")
    rows = run_metrics_queries_plan(
        plan,
        start=start,
        end=end,
        interval=int(3600 if interval is None else interval.total_seconds()),
        organization=organization,
        projects=projects,
        environments=environments,
        referrer=Referrer.OUTCOMES_TIMESERIES.value,
    ).apply_transformer(MetricsStatsTransformer())

    # Dummy query definition to pass to the _format_rows, as it expects a QueryDefinition object
    # TODO(metrics): add a `metrics` category or refactor _format_rows
    copied = query.copy()
    copied["category"] = "error"
    query_def = QueryDefinition.from_query_dict(copied, {"organization_id": organization.id})

    series = _format_rows(rows["series"], query_def)
    totals = _format_rows(rows["totals"], query_def)

    return massage_outcomes_result(query_def, totals, series)
