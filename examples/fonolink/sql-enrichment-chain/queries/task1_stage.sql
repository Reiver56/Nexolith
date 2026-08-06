-- task1: the raw, messy staged data as-is (a light initial pass -- just
-- naming it into the chain's own first-stage table, no cleanup yet).
--
-- duration_or_volume is cast to float8 here, not left as the source
-- column's native numeric/text. This works around a real bug found while
-- building this example: Nexolith's SQL destination `replace` mode infers
-- column types from Python values but only recognizes bool/int/float
-- (nexolith/connectors/sql.py:61) -- a Decimal (Postgres NUMERIC) or
-- datetime (TIMESTAMP) falls through to String(), silently downgrading the
-- column. float8 rows come back as Python `float`, which IS recognized, so
-- SUM() in task5 keeps working. See the README for the datetime half of
-- this same bug (occurred_at), which this chain does NOT work around.
SELECT
    id,
    subscriber_id,
    call_type,
    duration_or_volume::float8 AS duration_or_volume,
    occurred_at,
    cell_tower
FROM cdr_enrichment_stage1
