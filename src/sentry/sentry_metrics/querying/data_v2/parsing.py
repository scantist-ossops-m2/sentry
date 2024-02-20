from collections.abc import Generator, Sequence

from parsimonious.exceptions import IncompleteParseError
from snuba_sdk.mql.mql import InvalidMQLQueryError, parse_mql

from sentry.models.environment import Environment
from sentry.models.project import Project
from sentry.sentry_metrics.querying.data_v2.plan import MetricsQueriesPlan, QueryOrder
from sentry.sentry_metrics.querying.errors import InvalidMetricsQueryError
from sentry.sentry_metrics.querying.registry.base import ExpressionRegistry
from sentry.sentry_metrics.querying.types import QueryExpression
from sentry.sentry_metrics.querying.visitors import (
    EnvironmentsInjectionVisitor,
    LatestReleaseTransformationVisitor,
    QueryConditionsCompositeVisitor,
    QueryValidationV2Visitor,
    VisitableQueryExpression,
)
from sentry.sentry_metrics.querying.visitors.query_expression import ExpansionVisitor


class QueryParser:
    def __init__(
        self,
        projects: Sequence[Project],
        environments: Sequence[Environment],
        metrics_queries_plan: MetricsQueriesPlan,
        expression_registry: ExpressionRegistry,
    ):
        self._projects = projects
        self._environments = environments
        self._metrics_queries_plan = metrics_queries_plan
        self._expression_registry = expression_registry

    def _parse_mql(self, mql: str) -> VisitableQueryExpression:
        """
        Parses the field with the MQL grammar.
        """
        try:
            query = parse_mql(mql)
        except InvalidMQLQueryError as e:
            cause = e.__cause__
            if cause and isinstance(cause, IncompleteParseError):
                error_context = cause.text[cause.pos : cause.pos + 20]
                # We expose the entire MQL string to give more context when solving the error, since in the future we
                # expect that MQL will be directly fed into the endpoint instead of being built from the supplied
                # fields.
                raise InvalidMetricsQueryError(
                    f"The query '{mql}' could not be matched starting from '{error_context}...'"
                ) from e

            raise InvalidMetricsQueryError("The supplied query is not valid") from e

        return VisitableQueryExpression(query=query)

    def generate_queries(
        self,
    ) -> Generator[tuple[QueryExpression, QueryOrder | None, int | None], None, None]:
        """
        Generates multiple timeseries queries given a base query.
        """
        for formula_definition in self._metrics_queries_plan.get_replaced_formulas():
            query_expression = (
                self._parse_mql(formula_definition.mql)
                # We validate the query.
                .add_visitor(QueryValidationV2Visitor())
                # We expand the components of the query given an expression registry.
                .add_visitor(ExpansionVisitor(self._expression_registry))
                # We inject the environment filter in each timeseries.
                .add_visitor(EnvironmentsInjectionVisitor(self._environments))
                # We transform all `release:latest` filters into the actual latest releases.
                .add_visitor(
                    QueryConditionsCompositeVisitor(
                        LatestReleaseTransformationVisitor(self._projects)
                    )
                ).get()
            )
            # TODO: check if we want to use a better data structure for returning queries.
            yield query_expression, formula_definition.order, formula_definition.limit
