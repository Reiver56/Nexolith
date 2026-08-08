-- task3: normalization/consistency fixes.
-- call_type/cell_tower casing collapsed to a single convention; occurred_at
-- parsed to a real timestamp regardless of source precision/separator
-- (date-only, 'T'-separated, microsecond-suffixed all cast cleanly).
-- Rows whose occurred_at isn't even date-shaped (25 expected: 'N/A', '',
-- 'unknown', 'TBD', 'null') can't be normalized -- dropped here, not
-- silently coerced to some placeholder date.
SELECT
    id,
    subscriber_id,
    LOWER(TRIM(call_type)) AS call_type,
    duration_or_volume,
    occurred_at::timestamp AS occurred_at,
    UPPER(TRIM(cell_tower)) AS cell_tower
FROM cdr_stage2
WHERE occurred_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
