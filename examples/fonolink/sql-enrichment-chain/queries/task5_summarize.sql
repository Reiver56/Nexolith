-- task5: final enriched, analytics-ready output -- a per-subscriber usage
-- summary with plan context, one row per subscriber represented in the
-- cleaned/enriched data.
SELECT
    subscriber_id,
    subscriber_name,
    plan,
    subscriber_status,
    COUNT(*) FILTER (WHERE call_type = 'voice') AS voice_calls,
    COUNT(*) FILTER (WHERE call_type = 'data') AS data_sessions,
    COUNT(*) FILTER (WHERE call_type = 'sms') AS sms_messages,
    COALESCE(SUM(duration_or_volume) FILTER (WHERE call_type = 'voice'), 0) AS total_voice_seconds,
    COALESCE(SUM(duration_or_volume) FILTER (WHERE call_type = 'data'), 0) AS total_data_volume,
    MIN(occurred_at) AS first_record_at,
    MAX(occurred_at) AS last_record_at,
    COUNT(*) AS total_records
FROM cdr_stage4
GROUP BY subscriber_id, subscriber_name, plan, subscriber_status
