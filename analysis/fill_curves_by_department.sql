-- Seat fill per department at the end of each day, per semester.
-- Compare shapes across semesters with day_of_semester (days since the first
-- observed snapshot).
WITH snapshot_day AS (
    SELECT semester,
           CAST(observed_at AS DATE) AS day,
           max(sequence_no) AS last_seq
    FROM snapshots
    GROUP BY semester, CAST(observed_at AS DATE)
),
first_day AS (
    SELECT semester, min(day) AS first_day FROM snapshot_day GROUP BY semester
)
SELECT d.semester,
       h.department,
       d.day,
       date_diff('day', f.first_day, d.day) AS day_of_semester,
       sum(h.enrollment_count) AS enrolled,
       sum(h.capacity_count) AS capacity,
       round(sum(h.enrollment_count) * 1.0 / sum(h.capacity_count), 4) AS fill
FROM snapshot_day d
JOIN first_day f ON f.semester = d.semester
JOIN section_history h
  ON h.semester = d.semester AND h.sequence_no = d.last_seq
GROUP BY d.semester, h.department, d.day, f.first_day
ORDER BY d.semester, h.department, d.day;
