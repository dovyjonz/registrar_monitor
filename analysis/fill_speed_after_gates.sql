-- Overall seat fill when each registration gate opens and how far it climbs
-- afterwards. Gates come from settings.toml (view registration_gate); each
-- probe uses the last snapshot at or before the probe time.
WITH totals AS (
    SELECT semester,
           observed_at,
           sum(enrollment_count) AS enrolled,
           sum(capacity_count) AS capacity
    FROM section_history
    GROUP BY semester, observed_at
),
probe AS (
    SELECT g.semester, g.priority, g.label, g.gate_at,
           o.hours,
           g.gate_at + to_hours(o.hours) AS probe_at
    FROM registration_gate g
    CROSS JOIN (VALUES (0), (1), (6), (24)) AS o(hours)
),
fill_at AS (
    SELECT p.semester, p.priority, p.label, p.gate_at, p.hours,
           t.enrolled, t.capacity
    FROM probe p
    ASOF LEFT JOIN totals t
      ON t.semester = p.semester AND p.probe_at >= t.observed_at
)
SELECT semester,
       priority,
       label,
       gate_at,
       max(enrolled) FILTER (WHERE hours = 0) AS enrolled_at_gate,
       round(max(enrolled * 1.0 / capacity) FILTER (WHERE hours = 0), 4) AS fill_at_gate,
       round(max(enrolled * 1.0 / capacity) FILTER (WHERE hours = 1), 4) AS fill_plus_1h,
       round(max(enrolled * 1.0 / capacity) FILTER (WHERE hours = 6), 4) AS fill_plus_6h,
       round(max(enrolled * 1.0 / capacity) FILTER (WHERE hours = 24), 4) AS fill_plus_24h,
       max(enrolled) FILTER (WHERE hours = 24)
         - max(enrolled) FILTER (WHERE hours = 0) AS seats_taken_24h
FROM fill_at
GROUP BY semester, priority, label, gate_at
ORDER BY semester, gate_at;
