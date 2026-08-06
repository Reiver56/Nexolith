-- task4: enrichment -- join subscribers to add real business context.
-- Inner join is safe here: subscriber_id is a NOT NULL FK on cdr_raw itself,
-- so every surviving row is guaranteed a match. Row count must equal
-- task3's exactly -- any drop here would mean a real data-integrity gap.
SELECT
    c.id,
    c.subscriber_id,
    s.name AS subscriber_name,
    s.plan,
    s.status AS subscriber_status,
    c.call_type,
    c.duration_or_volume,
    c.occurred_at,
    c.cell_tower
FROM cdr_stage3 c
JOIN subscribers s ON s.id = c.subscriber_id
