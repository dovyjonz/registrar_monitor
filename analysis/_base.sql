-- Derived views shared by every saved query. Loaded by registrarmonitor.analysis
-- after the per-semester union views (course_catalog, section_catalog,
-- state_snapshot, *_change_event, ...) exist. Files starting with "_" are not
-- runnable with `monitor analyze`.
--
-- Snapshot and section ids are only unique within one semester database, so
-- every join below also matches on `semester`.

CREATE OR REPLACE VIEW snapshots AS
SELECT semester,
       snapshot_id,
       sequence_no,
       CAST(observed_at AS TIMESTAMP) AS observed_at,
       overall_fill
FROM state_snapshot;

-- Latest known department and title per course (courses removed from the
-- registrar's feed keep the values they had before removal).
CREATE OR REPLACE VIEW course_attr AS
SELECT e.semester,
       e.course_id,
       c.course_code,
       arg_max(e.new_department, s.sequence_no)
           FILTER (WHERE e.event_kind <> 'REMOVE') AS department,
       arg_max(e.new_title, s.sequence_no)
           FILTER (WHERE e.event_kind <> 'REMOVE') AS course_title
FROM course_change_event e
JOIN state_snapshot s
  ON s.semester = e.semester AND s.snapshot_id = e.snapshot_id
JOIN course_catalog c
  ON c.semester = e.semester AND c.course_id = e.course_id
GROUP BY e.semester, e.course_id, c.course_code;

-- Each section change event holds from its snapshot until the next event for
-- the same section.
CREATE OR REPLACE VIEW section_interval AS
SELECT e.semester,
       e.section_id,
       e.event_kind,
       s.sequence_no AS from_seq,
       lead(s.sequence_no) OVER (
           PARTITION BY e.semester, e.section_id ORDER BY s.sequence_no
       ) AS to_seq,
       e.new_section_type AS section_type,
       e.new_enrollment_count AS enrollment_count,
       e.new_capacity_count AS capacity_count,
       e.new_instructor AS instructor
FROM section_change_event e
JOIN state_snapshot s
  ON s.semester = e.semester AND s.snapshot_id = e.snapshot_id;

-- One row per section present in each snapshot.
CREATE OR REPLACE VIEW section_history AS
SELECT i.semester,
       sn.snapshot_id,
       sn.sequence_no,
       sn.observed_at,
       ca.course_code,
       ca.department,
       ca.course_title,
       sc.section_code,
       i.section_type,
       i.instructor,
       i.enrollment_count,
       i.capacity_count,
       i.enrollment_count * 1.0 / i.capacity_count AS fill
FROM section_interval i
JOIN snapshots sn
  ON sn.semester = i.semester
 AND sn.sequence_no >= i.from_seq
 AND (i.to_seq IS NULL OR sn.sequence_no < i.to_seq)
JOIN section_catalog sc
  ON sc.semester = i.semester AND sc.section_id = i.section_id
LEFT JOIN course_attr ca
  ON ca.semester = i.semester AND ca.course_id = sc.course_id
WHERE i.event_kind <> 'REMOVE';
