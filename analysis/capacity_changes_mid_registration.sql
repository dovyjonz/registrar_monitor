-- Section capacity changes, additions and removals observed after the first
-- snapshot of a semester and inside its registration window (first gate to the
-- "Close" deadline, or open-ended when none is configured). The first snapshot
-- is excluded because every section appears there as an ADD.
WITH first_seq AS (
    SELECT semester, min(sequence_no) AS seq FROM snapshots GROUP BY semester
)
SELECT s.semester,
       s.observed_at,
       c.course_code,
       sc.section_code,
       CASE
           WHEN e.event_kind = 'ADD' THEN 'section_added'
           WHEN e.event_kind = 'REMOVE' THEN 'section_removed'
           ELSE 'capacity_changed'
       END AS change,
       e.old_capacity_count,
       e.new_capacity_count,
       e.new_capacity_count - e.old_capacity_count AS capacity_delta,
       coalesce(e.new_enrollment_count, e.old_enrollment_count) AS enrollment,
       g.label AS gate_in_force
FROM section_change_event e
JOIN snapshots s
  ON s.semester = e.semester AND s.snapshot_id = e.snapshot_id
JOIN first_seq f ON f.semester = e.semester
JOIN section_catalog sc
  ON sc.semester = e.semester AND sc.section_id = e.section_id
JOIN course_catalog c
  ON c.semester = e.semester AND c.course_id = sc.course_id
JOIN registration_window w ON w.semester = e.semester
ASOF LEFT JOIN registration_gate g
  ON g.semester = e.semester AND s.observed_at >= g.gate_at
WHERE s.sequence_no > f.seq
  AND s.observed_at >= w.opens_at
  AND (w.closes_at IS NULL OR s.observed_at <= w.closes_at)
  AND (e.event_kind <> 'UPDATE'
       OR e.old_capacity_count IS DISTINCT FROM e.new_capacity_count)
ORDER BY s.semester, s.observed_at, c.course_code, sc.section_code;
