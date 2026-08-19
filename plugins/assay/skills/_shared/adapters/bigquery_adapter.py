"""bigquery_adapter.py — the production adapter.

A faithful port of what `spine.run_query` did before the adapter seam existed:
lazy-import the client (so nothing here is needed offline), assert the caller is
an individual human via ADC, bind every parameter by type, label the job, and
return plain dicts.

The identity assertion is the plugin's headline governance claim, so it lives at
the *last* point before the query leaves the process — after that there is
nothing left to enforce it.
"""

from __future__ import annotations

import datetime as dt
import sys

from ..identity import assert_user_identity
from .base import (
    CAP_DATE_TYPE,
    CAP_NATIVE_PERCENTILES,
    CAP_PUSHDOWN_AGGREGATION,
    CAP_USER_IDENTITY,
    Adapter,
    normalize_groups,
)

JOB_LABEL = "assay-spine-skill"


class BigQueryAdapter(Adapter):
    name = "bigquery"
    CAPABILITIES = frozenset(
        {
            CAP_NATIVE_PERCENTILES,
            CAP_PUSHDOWN_AGGREGATION,
            CAP_USER_IDENTITY,
            CAP_DATE_TYPE,
        }
    )
    fixture_backed = False

    # -- execution ---------------------------------------------------------

    def _client_and_module(self, project: str):
        try:
            from google.cloud import bigquery
        except ImportError:
            sys.stderr.write(
                "Missing deps. Install with:\n"
                "  pip install google-cloud-bigquery pyyaml\n"
            )
            sys.exit(3)

        # Identity is asserted here, immediately before the query leaves the
        # process. Exits 2 (no ADC) or 4 (service account / impersonation).
        assert_user_identity()
        return bigquery, bigquery.Client(project=project)

    @staticmethod
    def _scalar(bigquery, name: str, value):
        """Bind by declared type. DATE for dates, DATETIME for datetimes,
        INT64/FLOAT64/BOOL for numerics, STRING otherwise — matching the
        pre-adapter behavior, which bound dates as DATE and everything else as
        STRING, extended to the numeric cases the spine builders can produce."""
        if isinstance(value, bool):
            return bigquery.ScalarQueryParameter(name, "BOOL", value)
        if isinstance(value, dt.datetime):
            return bigquery.ScalarQueryParameter(name, "DATETIME", value)
        if isinstance(value, dt.date):
            return bigquery.ScalarQueryParameter(name, "DATE", value)
        if isinstance(value, int):
            return bigquery.ScalarQueryParameter(name, "INT64", value)
        if isinstance(value, float):
            return bigquery.ScalarQueryParameter(name, "FLOAT64", value)
        return bigquery.ScalarQueryParameter(name, "STRING", value)

    def run_query(self, project: str, sql: str, params: dict) -> list[dict]:
        bigquery, client = self._client_and_module(project)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[self._scalar(bigquery, n, v) for n, v in params.items()],
            labels={"source": JOB_LABEL},
        )
        rows = client.query(sql, job_config=job_config).result()
        return [dict(r) for r in rows]

    # -- percentiles: real push-down --------------------------------------

    def percentiles(
        self,
        project: str,
        *,
        from_sql: str,
        where_sql: str,
        value_expr: str,
        percentiles: list[int],
        params: dict,
        group_expr: str | None = None,
        group_by: dict | list | None = None,
    ) -> list[dict]:
        """Computed inside BigQuery via APPROX_QUANTILES — no row transfer.

        `APPROX_QUANTILES(x, 100)` returns 101 boundaries; `[OFFSET(p)]` is the
        p-th percentile. NULLs are ignored by the aggregate, which is the
        intended semantics: a transition's timing is over the population that
        reached both of its endpoints.

        Multi-column grouping groups and orders by every alias, so the row shape
        matches the sqlite adapter's: one row per group tuple with each group
        column under its own key.
        """
        groups = normalize_groups(group_expr, group_by)
        select_parts = [f"{expr} AS {alias}" for alias, expr in groups]
        select_parts.append(f"COUNT({value_expr}) AS n")
        select_parts.append(f"AVG({value_expr}) AS mean")
        for pct in percentiles:
            select_parts.append(
                f"APPROX_QUANTILES({value_expr}, 100)[OFFSET({pct})] AS p{pct}"
            )
        sql = (
            "SELECT\n  "
            + ",\n  ".join(select_parts)
            + f"\nFROM {from_sql}\n{where_sql}"
        )
        if groups:
            aliases = ", ".join(alias for alias, _ in groups)
            sql += f"\nGROUP BY {aliases}\nORDER BY {aliases}"
        rows = self.run_query(project, sql, params)
        for r in rows:
            r["_percentile_basis"] = "APPROX_QUANTILES (pushed down)"
        return rows
