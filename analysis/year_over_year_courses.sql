-- Final enrollment and peak fill per course, next to the same term one year
-- earlier (e.g. Fall 2026 vs Fall 2025). Semester names are "<Term> <Year>".
-- "final" is the course's last snapshot while it was offered.
WITH course_snapshot AS (
    SELECT semester, sequence_no, course_code,
           sum(enrollment_count) AS enrolled,
           sum(capacity_count) AS capacity
    FROM section_history
    GROUP BY semester, sequence_no, course_code
),
course_term AS (
    SELECT semester,
           regexp_extract(semester, '^(\S+) (\d{4})$', 1) AS term,
           CAST(regexp_extract(semester, '^(\S+) (\d{4})$', 2) AS INTEGER) AS year,
           course_code,
           max(enrolled * 1.0 / capacity) AS peak_fill,
           arg_max(enrolled, sequence_no) AS final_enrolled,
           arg_max(capacity, sequence_no) AS final_capacity
    FROM course_snapshot
    GROUP BY semester, course_code
)
SELECT cur.term,
       cur.year,
       cur.course_code,
       cur.final_enrolled,
       cur.final_capacity,
       round(cur.peak_fill, 4) AS peak_fill,
       prev.final_enrolled AS prev_final_enrolled,
       prev.final_capacity AS prev_final_capacity,
       round(prev.peak_fill, 4) AS prev_peak_fill,
       cur.final_enrolled - prev.final_enrolled AS enrolled_change,
       cur.final_capacity - prev.final_capacity AS capacity_change
FROM course_term cur
JOIN course_term prev
  ON prev.term = cur.term
 AND prev.year = cur.year - 1
 AND prev.course_code = cur.course_code
WHERE cur.term <> ''
ORDER BY cur.term, cur.year, cur.course_code;
