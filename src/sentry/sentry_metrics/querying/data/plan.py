import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from sentry.sentry_metrics.querying.data.execution import QueryResult
from sentry.sentry_metrics.querying.data.transformation.base import (
    QueryResultsTransformer,
    QueryTransformerResult,
)
from sentry.sentry_metrics.querying.types import QueryOrder


@dataclass(frozen=True)
class FormulaDefinition:
    """
    Represents the definition of a formula which can be run in a MetricsQueriesPlan.

    Attributes:
        mql: The formula string representation using the MQL language.
        order: The order of the formula.
        limit: The limit of the formula, representing the maximum number of groups that will be returned.
    """

    mql: str
    order: QueryOrder | None
    limit: int | None

    def replace_variables(self, queries: Mapping[str, str]) -> "FormulaDefinition":
        """
        Replaces all variables inside the formulas with the corresponding queries.

        For example, a formula in the form "$a + $b" with queries "a: max(mri_1), b: min(mri_2)" will become
        "max(mri_1) + min(mri_2)".

        The rationale for having queries being defined as variables in formulas is to have a structure which is more
        flexible and allows reuse of the same query across multiple formulas.

        Returns:
            A new FormulaDefinition with the MQL string containing the replaced formula.
        """
        replaced_mql_formula = self.mql
        # We sort query names by length and content with the goal of trying to always match the longest queries first.
        sorted_query_names = sorted(queries.keys(), key=lambda q: (len(q), q), reverse=True)
        for query_name in sorted_query_names:
            replaced_mql_formula = re.sub(
                rf"\${query_name}", queries.get(query_name, ""), replaced_mql_formula
            )

        return replace(self, mql=replaced_mql_formula)


@dataclass(frozen=True)
class MetricsQueriesPlan:
    """
    Represents a plan containing a series of queries and formulas to execute. The queries are defined as variables and
    the formulas will define what is actually executed.

    For example, you could define a simple query "a: max(mri_1)" and use it in the formula as "$a".
    """

    queries: Mapping[str, str] | None = None
    formulas: Sequence[FormulaDefinition] | None = None

    def get_replaced_formulas(self) -> Sequence[FormulaDefinition]:
        """
        Returns a list of formulas with the variables replaced with the actual mql query string.

        The usage of a variable in the formulas is with the `$` + the name of the query. The rationale
        behind choosing `$` is to keep the syntax compatible with the MQL syntax, in case we were to embed
        variables resolution in the layer itself.

        This function naively uses string substitution to replace the contents. In case we see it's too
        fragile, we might want to switch to parsing the actual input and mutating the AST.

        Returns:
            A list of FormulaDefinition objects that contain the final MQL to execute against Snuba.
        """
        if not self.queries:
            return self.formulas

        return list(map(lambda formula: formula.replace_variables(self.queries), self.formulas))

    def is_empty(self) -> bool:
        """
        A query plan is defined to be empty is no formulas have been applied on it.

        Returns:
            A boolean which is True when the plan is empty, or False otherwise.
        """
        return not self.formulas


class MetricsQueriesPlanBuilder:
    """
    Represents a builder for creating a `MetricsQueriesPlan` incrementally.

    As a design decision, the invocation order of the methods doesn't change the final result since we are not
    computing any values when adding queries or formulas.
    """

    def __init__(self):
        self._queries: dict[str, str] = {}
        self._formulas: list[FormulaDefinition] = []

    def declare_query(self, name: str, mql: str) -> "MetricsQueriesPlanBuilder":
        """
        Declares a query with a name and the mql definition.

        Returns:
            The MetricsQueriesPlan instance in which the query was added.
        """
        self._queries[name] = mql
        return self

    def apply_formula(
        self, mql: str, order: QueryOrder | None = None, limit: int | None = None
    ) -> "MetricsQueriesPlanBuilder":
        """
        Defines an mql formula that will be executed. The formula can reference previously defined queries using the
        `$query_name` syntax.

        Returns:
            The MetricsQueriesPlan instance in which the formula was added.
        """
        self._formulas.append(FormulaDefinition(mql=mql, order=order, limit=limit))
        return self

    def build(self) -> MetricsQueriesPlan:
        return MetricsQueriesPlan(queries=self._queries, formulas=self._formulas)


@dataclass(frozen=True)
class MetricsQueriesPlanResult:
    """
    Represents a wrapper around the results of a MetricsQueriesPlan which exposes useful methods to run on the query
    results.
    """

    results: list[QueryResult]

    def apply_transformer(
        self, transformer: QueryResultsTransformer[QueryTransformerResult]
    ) -> QueryTransformerResult:
        """
        Applies a transformer on the `results` and returns the value of the transformation.
        """
        return transformer.transform(self.results)
