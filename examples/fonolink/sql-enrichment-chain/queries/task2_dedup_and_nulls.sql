-- task2: deduplication and null-handling.
-- DISTINCT ON (id) drops exact-duplicate rows (150 expected). The WHERE
-- drops any row missing a field we can't safely default (call_type,
-- duration_or_volume, cell_tower -- 30 + 40 + 80 = 150 rows expected).
-- Malformed/inconsistent occurred_at is NOT this stage's job (that's task3).
SELECT DISTINCT ON (id)
    id,
    subscriber_id,
    call_type,
    duration_or_volume,
    occurred_at,
    cell_tower
FROM cdr_stage1
WHERE call_type IS NOT NULL
  AND duration_or_volume IS NOT NULL
  AND cell_tower IS NOT NULL
ORDER BY id
