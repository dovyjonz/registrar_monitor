#!/usr/bin/env python3
"""
Generate a prototype HTML page for visualizing course enrollment data.

This script queries the database for the most recent semester and generates
a self-contained HTML file with:
- Course grid layout (similar to PDF format)
- Expandable sections on course click
- Enrollment history graphs on section click
"""

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

# Project imports
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from registrarmonitor.data.database_manager import DatabaseManager


def get_semester_data(semester: str) -> dict[str, Any]:
    """
    Query the database for all course, section, and enrollment data.

    Returns a dictionary with all data needed for the prototype page.
    """
    db = DatabaseManager(semester=semester)

    data: dict[str, Any] = {
        "semester": semester,
        "lastReportTime": None,  # Will be set to latest snapshot timestamp
        "snapshots": [],
        "courses": {},
    }

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # Get all snapshots for this semester (ordered by timestamp)
        cursor.execute(
            """
            SELECT snapshot_id, timestamp, overall_fill
            FROM snapshots
            WHERE semester = ?
            ORDER BY timestamp ASC
        """,
            (semester,),
        )

        snapshots = cursor.fetchall()
        snapshot_id_to_idx = {}

        for idx, (snapshot_id, timestamp, overall_fill) in enumerate(snapshots):
            data["snapshots"].append(
                {
                    "id": snapshot_id,
                    "timestamp": timestamp,
                    "overallFill": overall_fill,
                }
            )
            snapshot_id_to_idx[snapshot_id] = idx

        # Set last report time to the latest snapshot
        if snapshots:
            data["lastReportTime"] = snapshots[-1][1]  # timestamp of latest snapshot

        if not snapshots:
            print(f"No snapshots found for semester: {semester}")
            return data

        # Get latest snapshot ID
        latest_snapshot_id = snapshots[-1][0]

        # Get all courses
        cursor.execute("""
            SELECT course_id, course_code, course_title, department
            FROM courses
            ORDER BY course_code
        """)
        courses = cursor.fetchall()

        course_id_to_code = {}
        for course_id, course_code, course_title, department in courses:
            course_id_to_code[course_id] = course_code
            data["courses"][course_code] = {
                "department": department or course_code.split()[0]
                if course_code
                else "",
                "title": course_title or "",
                "averageFill": 0.0,
                "sections": {},
            }

        # Get all sections with their latest enrollment data
        cursor.execute(
            """
            SELECT 
                s.section_id,
                s.course_id,
                s.section_code,
                s.section_type,
                s.instructor,
                ed.enrollment_count,
                ed.capacity_count,
                ed.fill_percentage
            FROM sections s
            JOIN enrollment_data ed ON s.section_id = ed.section_id
            WHERE ed.snapshot_id = ?
        """,
            (latest_snapshot_id,),
        )

        section_id_to_info: dict[int, tuple[str, str]] = {}

        for row in cursor.fetchall():
            (
                section_id,
                course_id,
                section_code,
                section_type,
                instructor,
                enrollment,
                capacity,
                fill,
            ) = row
            course_code = course_id_to_code.get(course_id)

            if not course_code or course_code not in data["courses"]:
                continue

            section_id_to_info[section_id] = (course_code, section_code)

            data["courses"][course_code]["sections"][section_code] = {
                "type": section_type or "",
                "instructor": instructor or "",
                "currentEnrollment": enrollment,
                "currentCapacity": capacity,
                "currentFill": fill,
                "sectionId": section_id,  # Store for history lookup
                "history": [],
            }

        # Get enrollment history for all sections (including enrollment/capacity)
        cursor.execute("""
            SELECT 
                ed.section_id,
                ed.snapshot_id,
                ed.fill_percentage,
                ed.enrollment_count,
                ed.capacity_count
            FROM enrollment_data ed
            ORDER BY ed.snapshot_id ASC
        """)

        for (
            section_id,
            snapshot_id,
            fill_percentage,
            enrollment_count,
            capacity_count,
        ) in cursor.fetchall():
            if section_id not in section_id_to_info:
                continue
            if snapshot_id not in snapshot_id_to_idx:
                continue

            course_code, section_code = section_id_to_info[section_id]

            if course_code in data["courses"]:
                if section_code in data["courses"][course_code]["sections"]:
                    data["courses"][course_code]["sections"][section_code][
                        "history"
                    ].append(
                        {
                            "snapshotIdx": snapshot_id_to_idx[snapshot_id],
                            "fill": fill_percentage,
                            "enrollment": enrollment_count,
                            "capacity": capacity_count,
                        }
                    )

        # Calculate average fill and isFilled for each course
        for course_code, course_data in data["courses"].items():
            sections = course_data["sections"]
            if sections:
                total_fill = sum(s["currentFill"] for s in sections.values())
                course_data["averageFill"] = total_fill / len(sections)

                # Compute isFilled using same logic as models.py Course.is_filled:
                # True when all sections of at least one section type are >= 100%
                sections_by_type: dict[str, list[float]] = {}
                for section in sections.values():
                    sec_type = section.get("type", "")
                    if sec_type not in sections_by_type:
                        sections_by_type[sec_type] = []
                    sections_by_type[sec_type].append(section["currentFill"])

                course_data["isFilled"] = any(
                    fills and all(f >= 1.0 for f in fills)
                    for fills in sections_by_type.values()
                )

    # Remove courses with no sections (not in this semester)
    data["courses"] = {
        code: course for code, course in data["courses"].items() if course["sections"]
    }

    return data


def get_combined_data(
    milestones_map: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    """
    Get data for all semesters combined into a single structure.

    Returns a combined data structure with all semesters accessible via toggle.
    """
    semesters = ["Spring 2026", "Fall 2025", "Summer 2025"]

    combined: dict[str, Any] = {
        "semesters": semesters,
        "activeSemester": semesters[0],  # Default to first semester
        "semesterData": {},
        "milestonesData": {},
    }

    for semester in semesters:
        print(f"  Loading {semester}...")
        data = get_semester_data(semester)
        combined["semesterData"][semester] = data
        combined["milestonesData"][semester] = milestones_map.get(semester, [])

    return combined


def generate_html(data: dict[str, Any], milestones: list[dict[str, str]]) -> str:
    """Generate the HTML page with embedded data."""

    json_data = json.dumps(data, indent=None, separators=(",", ":"))
    milestones_json = json.dumps(milestones, indent=None, separators=(",", ":"))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Enrollment Monitor - {data["semester"]}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
    <style>
        :root {{
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --bg-tertiary: #0f3460;
            --text-primary: #eaeaea;
            --text-secondary: #a0a0a0;
            --red-fill: #e61b4b;
            --yellow-fill: #ffd700;
            --green-fill: #2ecc71;
            --border-color: #3a3a5e;
            --hover-bg: #2a2a4e;
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        
        body {{
            font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', monospace;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 20px;
        }}
        
        header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: linear-gradient(135deg, var(--bg-secondary), var(--bg-tertiary));
            border-radius: 12px;
            border: 1px solid var(--border-color);
        }}
        
        header h1 {{
            font-size: 1.8rem;
            margin-bottom: 8px;
            background: linear-gradient(90deg, #e61b4b, #ffd700);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        header p {{
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}
        
        .stats {{
            display: flex;
            justify-content: center;
            gap: 30px;
            margin-top: 15px;
        }}
        
        .stat {{
            text-align: center;
        }}
        
        .stat-value {{
            font-size: 1.4rem;
            font-weight: bold;
        }}
        
        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        
        .course-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
            gap: 8px;
            max-width: 1400px;
            margin: 0 auto;
        }}
        
        .jump-to-nav {{
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 6px;
            padding: 12px 20px;
            background: var(--bg-secondary);
            border-radius: 8px;
            margin: 0 auto 20px;
            max-width: 1400px;
        }}
        
        .jump-to-nav a {{
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.75rem;
            padding: 4px 8px;
            border-radius: 4px;
            transition: all 0.2s ease;
        }}
        
        .jump-to-nav a:hover {{
            background: var(--hover-bg);
            color: var(--text-primary);
        }}
        
        .dept-header {{
            grid-column: 1 / -1;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.9rem;
            font-weight: bold;
            color: var(--text-secondary);
            padding: 15px 0 5px;
            border-bottom: 1px solid var(--border-color);
            margin-top: 10px;
        }}
        
        .dept-header .back-to-top {{
            font-size: 0.7rem;
            font-weight: normal;
            color: var(--text-secondary);
            text-decoration: none;
            opacity: 0.6;
            transition: opacity 0.2s ease;
        }}
        
        .dept-header .back-to-top:hover {{
            opacity: 1;
            color: var(--yellow-fill);
        }}
        
        .course-cell {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 10px;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s ease;
            font-size: 0.8rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
        }}
        
        .course-cell:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        
        .course-cell.full {{
            background: linear-gradient(135deg, var(--red-fill), #c41538);
            border-color: var(--red-fill);
        }}
        
        .course-cell.near {{
            background: linear-gradient(135deg, #b8860b, #8b6914);
            border-color: var(--yellow-fill);
        }}
        
        .course-cell.expanded {{
            outline: 2px solid var(--yellow-fill);
        }}
        
        .course-code {{
            font-weight: 500;
        }}
        
        .course-fill {{
            font-weight: bold;
            font-size: 0.75rem;
        }}
        
        /* Modal for expanded course view */
        .modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.7);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        
        .modal-overlay.active {{
            display: flex;
        }}
        
        .modal {{
            background: var(--bg-secondary);
            border-radius: 12px;
            width: 100%;
            max-width: 900px;
            max-height: 90vh;
            overflow: hidden;
            border: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
        }}
        
        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px;
            border-bottom: 1px solid var(--border-color);
            position: sticky;
            top: 0;
            background: var(--bg-secondary);
            z-index: 10;
        }}
        
        .modal-header h2 {{
            font-size: 1.3rem;
        }}
        
        .modal-header .close-btn {{
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.5rem;
            cursor: pointer;
            padding: 5px 10px;
        }}
        
        .modal-header .close-btn:hover {{
            color: var(--red-fill);
        }}
        
        .modal-body {{
            padding: 20px;
            overflow-y: auto;
            overscroll-behavior: contain;
            flex: 1;
        }}
        
        /* Section type selector (the grouped sections by type) */
        .section-type-selector {{
            margin-bottom: 20px;
        }}
        
        .section-type-group {{
            margin-bottom: 15px;
        }}
        
        .section-type-label {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .modal-header-left {{
            display: flex;
            align-items: center;
        }}
        
        .section-list {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 10px;
            margin-bottom: 20px;
        }}
        
        .section-item {{
            padding: 12px;
            background: var(--bg-tertiary);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            border: 2px solid transparent;
        }}
        
        .section-item:hover {{
            background: var(--hover-bg);
        }}
        
        .section-item.selected {{
            border-color: var(--yellow-fill);
        }}
        
        .section-item.full {{
            border-left: 4px solid var(--red-fill);
        }}
        
        .section-item.near {{
            border-left: 4px solid var(--yellow-fill);
        }}
        
        .section-id {{
            font-weight: bold;
            font-size: 1rem;
        }}
        
        .section-instructor {{
            color: var(--text-secondary);
            font-size: 0.75rem;
        }}
        
        .section-stats {{
            margin-top: 8px;
            font-size: 0.85rem;
        }}
        
        .section-fill {{
            font-weight: bold;
        }}
        
        .chart-container {{
            background: var(--bg-tertiary);
            border-radius: 8px;
            padding: 20px;
            margin-top: 20px;
            min-height: 300px;
        }}
        
        .chart-placeholder {{
            text-align: center;
            color: var(--text-secondary);
            padding: 60px;
        }}
        
        #enrollmentChart {{
            max-height: 350px;
            height: 300px;
        }}
        
        .chart-hidden {{
            visibility: hidden;
            position: absolute;
        }}
        
        .chart-legend {{
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 10px;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        
        .chart-legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        
        .chart-legend-dot {{
            width: 10px;
            height: 10px;
            border-radius: 2px;
            transform: rotate(45deg);
        }}
        
        /* Prevent body scroll when modal is open */
        body.modal-open {{
            overflow: hidden;
            position: fixed;
            width: 100%;
        }}
        
        /* Chart touch handling */
        .chart-container {{
            touch-action: pan-x pinch-zoom;
        }}
        
        #enrollmentChart {{
            touch-action: pan-x pinch-zoom;
        }}
        
        /* Mobile optimizations */
        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}
            
            header {{
                padding: 15px;
                margin-bottom: 20px;
            }}
            
            header h1 {{
                font-size: 1.4rem;
            }}
            
            .stats {{
                gap: 15px;
            }}
            
            .stat-value {{
                font-size: 1.1rem;
            }}
            
            .course-grid {{
                grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
                gap: 6px;
            }}
            
            .course-cell {{
                padding: 6px 8px;
                font-size: 0.7rem;
            }}
            
            .modal-overlay {{
                padding: 0;
                align-items: flex-end;
            }}
            
            .modal {{
                max-height: 90vh;
                border-radius: 12px 12px 0 0;
                will-change: transform;
            }}
            
            /* Prevent background scroll in modal on mobile */
            .modal-body {{
                -webkit-overflow-scrolling: touch;
            }}
            
            .modal-header {{
                padding: 15px;
            }}
            
            .modal-header h2 {{
                font-size: 1.1rem;
            }}
            
            .modal-body {{
                padding: 15px;
            }}
            
            .section-list {{
                grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
                gap: 8px;
            }}
            
            .section-item {{
                padding: 10px;
            }}
            
            .chart-container {{
                padding: 10px;
                min-height: 250px;
            }}
            
            #enrollmentChart {{
                max-height: 280px;
            }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>📊 Enrollment Monitor</h1>
        <p>{data["semester"]} • Last updated {datetime.fromisoformat(data["lastReportTime"]).strftime("%Y-%m-%d %H:%M") if data["lastReportTime"] else "N/A"}</p>
        <div class="stats">
            <div class="stat">
                <div class="stat-value" id="totalCourses">0</div>
                <div class="stat-label">Courses</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="totalSections">0</div>
                <div class="stat-label">Sections</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="fullSections">0</div>
                <div class="stat-label">Full</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="snapshotCount">0</div>
                <div class="stat-label">Snapshots</div>
            </div>
        </div>
    </header>
    
    <nav class="jump-to-nav" id="jumpToNav"></nav>
    
    <div class="course-grid" id="courseGrid"></div>
    
    <div class="modal-overlay" id="modalOverlay">
        <div class="modal">
            <div class="modal-header">
                <div class="modal-header-left">
                    <h2 id="modalTitle">Course Details</h2>
                </div>
                <button class="close-btn" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="section-type-selector" id="sectionTypeSelector"></div>
                <div class="section-list" id="sectionList"></div>
                <div class="chart-container" id="chartContainer">
                    <div class="chart-placeholder" id="chartPlaceholder">
                        Click a section to view enrollment history
                    </div>
                    <canvas id="enrollmentChart" class="chart-hidden"></canvas>
                    <div class="chart-legend" id="chartLegend" style="display: none;">
                        <div class="chart-legend-item">
                            <div class="chart-legend-dot" style="background: #4ecdc4;"></div>
                            <span>Capacity changed</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        const DATA = {json_data};
        
        // Registration milestones (dynamically set based on semester)
        const MILESTONES = {milestones_json};
        
        let chart = null;
        let selectedCourse = null;
        let selectedSection = null;
        let viewingGraph = false;
        let scrollPositionBeforeModal = 0;
        let currentEnrollmentData = []; // Store enrollment/capacity for tooltip
        
        // Helper to get contrasting text color (black or white) based on background
        function getContrastColor(hexColor) {{
            const hex = hexColor.replace('#', '');
            const r = parseInt(hex.substring(0, 2), 16);
            const g = parseInt(hex.substring(2, 4), 16);
            const b = parseInt(hex.substring(4, 6), 16);
            // Calculate relative luminance
            const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
            return luminance > 0.5 ? '#1a1a2e' : '#ffffff';
        }}
        
        function formatCourseCode(code) {{
            const parts = code.split(' ');
            if (parts.length !== 2) return code;
            const [dept, num] = parts;
            return `${{dept}} ${{num}}`;
        }}
        
        function getStatusClass(fill, isFilled = false) {{
            if (isFilled || fill >= 1.0) return 'full';
            if (fill >= 0.75) return 'near';
            return '';
        }}
        
        function getSectionTypeName(type) {{
            const names = {{
                'L': 'Lecture',
                'S': 'Seminar',
                'R': 'Recitation',
                'D': 'Discussion',
                'B': 'Lab',
                'Lb': 'Lab',
                'Int': 'Internship',
                'P': 'Project',
                'IS': 'Independent Study',
                'T': 'Tutorial',
            }};
            return names[type] || type || 'Section';
        }}
        
        function renderCourseGrid() {{
            const grid = document.getElementById('courseGrid');
            grid.innerHTML = '';
            
            // Group courses by department
            const deptCourses = {{}};
            for (const [code, course] of Object.entries(DATA.courses)) {{
                const dept = course.department;
                if (!deptCourses[dept]) deptCourses[dept] = [];
                deptCourses[dept].push({{ code, ...course }});
            }}
            
            // Sort departments alphabetically
            const sortedDepts = Object.keys(deptCourses).sort();
            
            let totalCourses = 0;
            let totalSections = 0;
            let fullSections = 0;
            
            for (const dept of sortedDepts) {{
                const courses = deptCourses[dept];
                
                // Department header with ID for jump-to and back-to-top link
                const header = document.createElement('div');
                header.className = 'dept-header';
                header.id = `dept-${{dept}}`;
                header.innerHTML = `
                    <span>${{dept}}</span>
                    <a href="#" class="back-to-top" onclick="event.preventDefault(); window.scrollTo({{top: 0, behavior: 'smooth'}});">↑ Top</a>
                `;
                grid.appendChild(header);
                
                // Sort courses by code
                courses.sort((a, b) => a.code.localeCompare(b.code));
                
                for (const course of courses) {{
                    totalCourses++;
                    const sectionCount = Object.keys(course.sections).length;
                    totalSections += sectionCount;
                    
                    for (const section of Object.values(course.sections)) {{
                        if (section.currentFill >= 1.0) fullSections++;
                    }}
                    
                    const cell = document.createElement('div');
                    cell.className = `course-cell ${{getStatusClass(course.averageFill, course.isFilled)}}`;
                    cell.setAttribute('data-course', course.code);
                    cell.innerHTML = `
                        <span class="course-code">${{formatCourseCode(course.code)}}</span>
                        <span class="course-fill">${{Math.round(course.averageFill * 100)}}%</span>
                    `;
                    cell.onclick = () => openCourse(course.code);
                    grid.appendChild(cell);
                }}
            }}
            
            // Update stats
            document.getElementById('totalCourses').textContent = totalCourses;
            document.getElementById('totalSections').textContent = totalSections;
            document.getElementById('fullSections').textContent = fullSections;
            document.getElementById('snapshotCount').textContent = DATA.snapshots.length;
            
            // Render jump-to navigation
            const jumpNav = document.getElementById('jumpToNav');
            jumpNav.innerHTML = sortedDepts.map(dept => 
                `<a href="#dept-${{dept}}">${{dept}}</a>`
            ).join('');
        }}
        
        function openCourse(courseCode) {{
            selectedCourse = courseCode;
            selectedSection = null;
            viewingGraph = false;
            
            // Store scroll position and lock body
            scrollPositionBeforeModal = window.scrollY;
            
            const course = DATA.courses[courseCode];
            document.getElementById('modalTitle').textContent = `${{courseCode}}${{course.title ? ' - ' + course.title : ''}}`;
            
            const sectionList = document.getElementById('sectionList');
            sectionList.innerHTML = '';
            
            // Sort sections by type then by ID
            const sections = Object.entries(course.sections).sort((a, b) => {{
                const typePriority = {{ L: 0, S: 1, R: 1, D: 1, B: 2, Lb: 2 }};
                const pa = typePriority[a[1].type] ?? 3;
                const pb = typePriority[b[1].type] ?? 3;
                if (pa !== pb) return pa - pb;
                return a[0].localeCompare(b[0], undefined, {{ numeric: true }});
            }});
            
            // Group sections by type for the selector
            const sectionsByType = {{}};
            for (const [sectionCode, section] of sections) {{
                const type = section.type || 'Other';
                if (!sectionsByType[type]) sectionsByType[type] = [];
                sectionsByType[type].push({{ code: sectionCode, ...section }});
            }}
            
            // Render section type selector
            const sectionTypeSelector = document.getElementById('sectionTypeSelector');
            sectionTypeSelector.innerHTML = '';
            
            for (const [type, typeSections] of Object.entries(sectionsByType)) {{
                const typeGroup = document.createElement('div');
                typeGroup.className = 'section-type-group';
                
                const typeLabel = document.createElement('div');
                typeLabel.className = 'section-type-label';
                typeLabel.textContent = getSectionTypeName(type);
                typeGroup.appendChild(typeLabel);
                
                const groupList = document.createElement('div');
                groupList.className = 'section-list';
                groupList.style.marginBottom = '0';
                
                for (const section of typeSections) {{
                    const item = document.createElement('div');
                    item.className = `section-item ${{getStatusClass(section.currentFill)}}`;
                    item.id = `section-${{section.code}}`;
                    item.innerHTML = `
                        <div class="section-id">${{section.code}}</div>
                        ${{section.instructor ? `<div class="section-instructor">${{section.instructor}}</div>` : ''}}
                        <div class="section-stats">
                            <span class="section-fill">${{Math.round(section.currentFill * 100)}}%</span>
                            <span>(${{section.currentEnrollment}}/${{section.currentCapacity}})</span>
                        </div>
                    `;
                    item.onclick = () => selectSection(section.code);
                    groupList.appendChild(item);
                }}
                
                typeGroup.appendChild(groupList);
                sectionTypeSelector.appendChild(typeGroup);
            }}
            
            // Show average fill chart by default
            document.getElementById('modalOverlay').classList.add('active');
            
            // Use setTimeout to ensure modal is visible before rendering chart
            setTimeout(() => {{
                showAverageFillChart(courseCode);
            }}, 50);
            document.body.classList.add('modal-open');
            document.body.style.top = `-${{scrollPositionBeforeModal}}px`;
        }}
        
        function showAverageFillChart(courseCode) {{
            const course = DATA.courses[courseCode];
            const sectionsArr = Object.values(course.sections);
            
            // Build average fill data across all snapshots
            const snapshotFills = {{}}; // snapshotIdx -> [fills]
            for (const section of sectionsArr) {{
                for (const point of section.history) {{
                    if (!snapshotFills[point.snapshotIdx]) {{
                        snapshotFills[point.snapshotIdx] = [];
                    }}
                    snapshotFills[point.snapshotIdx].push(point.fill);
                }}
            }}
            
            // Sort by snapshot index and compute averages
            const sortedIndices = Object.keys(snapshotFills).map(Number).sort((a, b) => a - b);
            const labels = [];
            const fillData = [];
            const timestamps = [];
            
            for (const idx of sortedIndices) {{
                const snapshot = DATA.snapshots[idx];
                if (snapshot) {{
                    const fills = snapshotFills[idx];
                    const avgFill = fills.reduce((a, b) => a + b, 0) / fills.length;
                    const date = new Date(snapshot.timestamp);
                    timestamps.push(date.getTime());
                    labels.push(date.toLocaleDateString('en-US', {{ 
                        month: 'short', 
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit'
                    }}));
                    fillData.push(Math.round(avgFill * 100));
                }}
            }}
            
            document.getElementById('chartLegend').style.display = 'none';
            renderChart('Average Fill', labels, fillData, timestamps, false);
        }}
        
        function selectSection(sectionCode) {{
            // If clicking the same section, deselect and show average fill
            if (selectedSection === sectionCode) {{
                document.getElementById(`section-${{sectionCode}}`)?.classList.remove('selected');
                selectedSection = null;
                viewingGraph = false;
                currentEnrollmentData = [];
                showAverageFillChart(selectedCourse);
                return;
            }}
            
            // Update selection styling
            if (selectedSection) {{
                document.getElementById(`section-${{selectedSection}}`)?.classList.remove('selected');
            }}
            selectedSection = sectionCode;
            viewingGraph = true;
            document.getElementById(`section-${{sectionCode}}`)?.classList.add('selected');
            
            const section = DATA.courses[selectedCourse].sections[sectionCode];
            
            // Prepare chart data with enrollment info and capacity changes
            const labels = [];
            const fillData = [];
            const timestamps = [];
            currentEnrollmentData = [];
            let prevCapacity = null;
            
            for (const point of section.history) {{
                const snapshot = DATA.snapshots[point.snapshotIdx];
                if (snapshot) {{
                    const date = new Date(snapshot.timestamp);
                    timestamps.push(date.getTime());
                    labels.push(date.toLocaleDateString('en-US', {{ 
                        month: 'short', 
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit'
                    }}));
                    fillData.push(Math.round(point.fill * 100));
                    
                    const capacityChanged = prevCapacity !== null && point.capacity !== prevCapacity;
                    currentEnrollmentData.push({{
                        enrollment: point.enrollment,
                        capacity: point.capacity,
                        prevCapacity: prevCapacity,
                        capacityChanged: capacityChanged
                    }});
                    prevCapacity = point.capacity;
                }}
            }}
            
            // Show legend if there are capacity changes
            const hasCapacityChanges = currentEnrollmentData.some(d => d.capacityChanged);
            document.getElementById('chartLegend').style.display = hasCapacityChanges ? 'flex' : 'none';
            
            renderChart(`${{sectionCode}} Enrollment %`, labels, fillData, timestamps, true);
        }}
        
        function renderChart(chartLabel, labels, fillData, timestamps, showCapacityMarkers) {{
            // Build annotation lines for milestones within our data range
            const annotations = {{}};
            if (timestamps.length > 0) {{
                const minTime = Math.min(...timestamps);
                const maxTime = Math.max(...timestamps);
                
                MILESTONES.forEach((m, idx) => {{
                    const mTime = new Date(m.time).getTime();
                    if (mTime >= minTime && mTime <= maxTime) {{
                        // Find the closest label index
                        let closestIdx = 0;
                        let minDiff = Infinity;
                        timestamps.forEach((t, i) => {{
                            const diff = Math.abs(t - mTime);
                            if (diff < minDiff) {{
                                minDiff = diff;
                                closestIdx = i;
                            }}
                        }});
                        
                        // Get fill value at this point to determine label position
                        const fillAtPoint = fillData[closestIdx] || 0;
                        const labelPos = fillAtPoint > 50 ? 'start' : 'end';
                        
                        // Line annotation with label (z controls layering)
                        annotations[`line${{idx}}`] = {{
                            type: 'line',
                            xMin: closestIdx,
                            xMax: closestIdx,
                            borderColor: m.color,
                            borderWidth: 2,
                            borderDash: [5, 3],
                            drawTime: 'beforeDatasetsDraw',
                            label: {{
                                display: true,
                                content: m.label,
                                position: labelPos,
                                backgroundColor: m.color,
                                color: getContrastColor(m.color),
                                font: {{ size: 9, weight: 'bold' }},
                                padding: 3,
                                borderRadius: 3,
                                z: 10,
                                drawTime: 'afterDatasetsDraw',
                            }}
                        }};
                    }}
                }});
            }}
            
            // Create or update chart
            document.getElementById('chartPlaceholder').style.display = 'none';
            const canvas = document.getElementById('enrollmentChart');
            
            // Remove hidden class first, then force reflow to ensure dimensions are set
            canvas.classList.remove('chart-hidden');
            canvas.offsetHeight; // Force reflow
            
            if (chart) {{
                chart.destroy();
                chart = null;
            }}
            
            // Build point styling arrays for capacity change markers
            const pointStyles = currentEnrollmentData.map(d => 
                showCapacityMarkers && d.capacityChanged ? 'rectRot' : 'circle'
            );
            const pointColors = currentEnrollmentData.map(d => 
                showCapacityMarkers && d.capacityChanged ? '#4ecdc4' : '#ffd700'
            );
            const pointRadii = currentEnrollmentData.map(d => 
                showCapacityMarkers && d.capacityChanged ? 7 : (labels.length > 50 ? 0 : 3)
            );
            const pointBorderColors = currentEnrollmentData.map(d =>
                showCapacityMarkers && d.capacityChanged ? '#ffffff' : '#ffd700'
            );
            const pointBorderWidths = currentEnrollmentData.map(d =>
                showCapacityMarkers && d.capacityChanged ? 2 : 1
            );
            
            chart = new Chart(canvas, {{
                type: 'line',
                data: {{
                    labels: labels,
                    datasets: [{{
                        label: chartLabel,
                        data: fillData,
                        borderColor: '#ffd700',
                        backgroundColor: 'rgba(255, 215, 0, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointStyle: pointStyles,
                        pointRadius: pointRadii,
                        pointHoverRadius: 6,
                        pointBackgroundColor: pointColors,
                        pointBorderColor: pointBorderColors,
                        pointBorderWidth: pointBorderWidths,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {{
                        annotation: {{
                            annotations: annotations
                        }},
                        legend: {{
                            labels: {{
                                color: '#eaeaea',
                                font: {{ family: 'monospace' }}
                            }}
                        }},
                        tooltip: {{
                            backgroundColor: '#1a1a2e',
                            titleColor: '#ffd700',
                            bodyColor: '#eaeaea',
                            borderColor: '#3a3a5e',
                            borderWidth: 1,
                            callbacks: {{
                                label: (ctx) => {{
                                    const idx = ctx.dataIndex;
                                    const enrollInfo = currentEnrollmentData[idx];
                                    if (enrollInfo && enrollInfo.enrollment !== undefined) {{
                                        let label = `${{ctx.parsed.y}}% (${{enrollInfo.enrollment}}/${{enrollInfo.capacity}})`;
                                        if (enrollInfo.capacityChanged) {{
                                            label += ` • Cap: ${{enrollInfo.prevCapacity}} → ${{enrollInfo.capacity}}`;
                                        }}
                                        return label;
                                    }}
                                    return `${{ctx.dataset.label}}: ${{ctx.parsed.y}}%`;
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{
                            ticks: {{
                                display: false
                            }},
                            grid: {{
                                color: 'rgba(255,255,255,0.05)'
                            }}
                        }},
                        y: {{
                            min: 0,
                            suggestedMax: 100,
                            ticks: {{
                                display: false
                            }},
                            grid: {{
                                color: 'rgba(255,255,255,0.05)'
                            }}
                        }}
                    }},
                    interaction: {{
                        intersect: false,
                        mode: 'index'
                    }}
                }}
            }});
        }}
        
        function closeModal() {{
            document.getElementById('modalOverlay').classList.remove('active');
            document.body.classList.remove('modal-open');
            document.body.style.top = '';
            window.scrollTo(0, scrollPositionBeforeModal);
            selectedCourse = null;
            selectedSection = null;
            viewingGraph = false;
            currentEnrollmentData = [];
            document.getElementById('chartLegend').style.display = 'none';
            if (chart) {{
                chart.destroy();
                chart = null;
            }}
        }}
        
        // Clear chart active elements on touch end (fix persistent dot)
        function clearChartActiveElements() {{
            if (chart) {{
                chart.setActiveElements([]);
                chart.tooltip.setActiveElements([]);
                chart.update('none');
            }}
        }}
        
        // Close modal on overlay click
        document.getElementById('modalOverlay').addEventListener('click', (e) => {{
            if (e.target.id === 'modalOverlay') closeModal();
        }});
        
        // Close modal on escape key
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') closeModal();
        }});
        
        // Clear chart hover on touch end
        document.getElementById('chartContainer').addEventListener('touchend', () => {{
            setTimeout(clearChartActiveElements, 100);
        }});
        
        // Clear chart hover when clicking elsewhere in modal body
        document.querySelector('.modal-body').addEventListener('click', (e) => {{
            if (!e.target.closest('#chartContainer')) {{
                clearChartActiveElements();
            }}
        }});
        
        // Initialize
        renderCourseGrid();
    </script>
</body>
</html>"""

    return html


def generate_combined_html(combined_data: dict[str, Any]) -> str:
    """Generate the HTML page with all semesters and a toggle selector."""

    json_data = json.dumps(combined_data, indent=None, separators=(",", ":"))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Enrollment Monitor - All Semesters</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
    <style>
        :root {{
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --bg-tertiary: #0f3460;
            --text-primary: #eaeaea;
            --text-secondary: #a0a0a0;
            --red-fill: #e61b4b;
            --yellow-fill: #ffd700;
            --green-fill: #2ecc71;
            --teal-accent: #4ecdc4;
            --border-color: #3a3a5e;
            --hover-bg: #2a2a4e;
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        
        body {{
            font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', monospace;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 20px;
        }}
        
        header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: linear-gradient(135deg, var(--bg-secondary), var(--bg-tertiary));
            border-radius: 12px;
            border: 1px solid var(--border-color);
        }}
        
        header h1 {{
            font-size: 1.8rem;
            margin-bottom: 8px;
            background: linear-gradient(90deg, #e61b4b, #ffd700);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        header p {{
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}
        
        .semester-toggle {{
            display: flex;
            justify-content: center;
            gap: 8px;
            margin: 15px 0;
        }}
        
        .semester-btn {{
            padding: 8px 16px;
            border: 1px solid var(--border-color);
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            border-radius: 6px;
            cursor: pointer;
            font-family: inherit;
            font-size: 0.85rem;
            transition: all 0.2s ease;
        }}
        
        .semester-btn:hover {{
            background: var(--hover-bg);
            color: var(--text-primary);
        }}
        
        .semester-btn.active {{
            background: linear-gradient(135deg, var(--yellow-fill), #ff9100);
            color: #1a1a2e;
            border-color: var(--yellow-fill);
            font-weight: bold;
        }}
        
        .stats {{
            display: flex;
            justify-content: center;
            gap: 30px;
            margin-top: 15px;
        }}
        
        .stat {{
            text-align: center;
        }}
        
        .stat-value {{
            font-size: 1.4rem;
            font-weight: bold;
        }}
        
        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        
        .course-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
            gap: 8px;
            max-width: 1400px;
            margin: 0 auto;
        }}
        
        .jump-to-nav {{
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 6px;
            padding: 12px 20px;
            background: var(--bg-secondary);
            border-radius: 8px;
            margin: 0 auto 20px;
            max-width: 1400px;
        }}
        
        .jump-to-nav a {{
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.75rem;
            padding: 4px 8px;
            border-radius: 4px;
            transition: all 0.2s ease;
        }}
        
        .jump-to-nav a:hover {{
            background: var(--hover-bg);
            color: var(--text-primary);
        }}
        
        .dept-header {{
            grid-column: 1 / -1;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.9rem;
            font-weight: bold;
            color: var(--text-secondary);
            padding: 15px 0 5px;
            border-bottom: 1px solid var(--border-color);
            margin-top: 10px;
        }}
        
        .dept-header .back-to-top {{
            font-size: 0.7rem;
            font-weight: normal;
            color: var(--text-secondary);
            text-decoration: none;
            opacity: 0.6;
            transition: opacity 0.2s ease;
        }}
        
        .dept-header .back-to-top:hover {{
            opacity: 1;
            color: var(--yellow-fill);
        }}
        
        .course-cell {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 10px;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s ease;
            font-size: 0.8rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
        }}
        
        .course-cell:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        
        .course-cell.full {{
            background: linear-gradient(135deg, var(--red-fill), #c41538);
            border-color: var(--red-fill);
        }}
        
        .course-cell.near {{
            background: linear-gradient(135deg, #b8860b, #8b6914);
            border-color: var(--yellow-fill);
        }}
        
        .course-cell.expanded {{
            outline: 2px solid var(--yellow-fill);
        }}
        
        .course-code {{
            font-weight: 500;
        }}
        
        .course-fill {{
            font-weight: bold;
            font-size: 0.75rem;
        }}
        
        /* Modal for expanded course view */
        .modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.7);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        
        .modal-overlay.active {{
            display: flex;
        }}
        
        .modal {{
            background: var(--bg-secondary);
            border-radius: 12px;
            width: 100%;
            max-width: 900px;
            max-height: 90vh;
            overflow: hidden;
            border: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
        }}
        
        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px;
            border-bottom: 1px solid var(--border-color);
            position: sticky;
            top: 0;
            background: var(--bg-secondary);
            z-index: 10;
        }}
        
        .modal-header h2 {{
            font-size: 1.3rem;
        }}
        
        .modal-header .close-btn {{
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.5rem;
            cursor: pointer;
            padding: 5px 10px;
        }}
        
        .modal-header .close-btn:hover {{
            color: var(--red-fill);
        }}
        
        .modal-body {{
            padding: 20px;
            overflow-y: auto;
            overscroll-behavior: contain;
            flex: 1;
        }}
        
        /* Section type selector (the grouped sections by type) */
        .section-type-selector {{
            margin-bottom: 20px;
        }}
        
        .section-type-group {{
            margin-bottom: 15px;
        }}
        
        .section-type-label {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .modal-header-left {{
            display: flex;
            align-items: center;
        }}
        
        .section-list {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 10px;
            margin-bottom: 20px;
        }}
        
        .section-item {{
            padding: 12px;
            background: var(--bg-tertiary);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            border: 2px solid transparent;
        }}
        
        .section-item:hover {{
            background: var(--hover-bg);
        }}
        
        .section-item.selected {{
            border-color: var(--yellow-fill);
        }}
        
        .section-item.full {{
            border-left: 4px solid var(--red-fill);
        }}
        
        .section-item.near {{
            border-left: 4px solid var(--yellow-fill);
        }}
        
        .section-id {{
            font-weight: bold;
            font-size: 1rem;
        }}
        
        .section-instructor {{
            color: var(--text-secondary);
            font-size: 0.75rem;
        }}
        
        .section-stats {{
            margin-top: 8px;
            font-size: 0.85rem;
        }}
        
        .section-fill {{
            font-weight: bold;
        }}
        
        .chart-container {{
            background: var(--bg-tertiary);
            border-radius: 8px;
            padding: 20px;
            margin-top: 20px;
            min-height: 300px;
        }}
        
        .chart-placeholder {{
            text-align: center;
            color: var(--text-secondary);
            padding: 60px;
        }}
        
        #enrollmentChart {{
            max-height: 350px;
            height: 300px;
        }}
        
        .chart-hidden {{
            visibility: hidden;
            position: absolute;
        }}
        
        .chart-legend {{
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 10px;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        
        .chart-legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        
        .chart-legend-dot {{
            width: 10px;
            height: 10px;
            border-radius: 2px;
            transform: rotate(45deg);
        }}
        
        /* Prevent body scroll when modal is open */
        body.modal-open {{
            overflow: hidden;
            position: fixed;
            width: 100%;
        }}
        
        /* Chart touch handling */
        .chart-container {{
            touch-action: pan-x pinch-zoom;
        }}
        
        #enrollmentChart {{
            touch-action: pan-x pinch-zoom;
        }}
        
        /* Mobile optimizations */
        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}
            
            header {{
                padding: 15px;
                margin-bottom: 20px;
            }}
            
            header h1 {{
                font-size: 1.4rem;
            }}
            
            .semester-toggle {{
                flex-wrap: wrap;
            }}
            
            .semester-btn {{
                padding: 6px 12px;
                font-size: 0.8rem;
            }}
            
            .stats {{
                gap: 15px;
            }}
            
            .stat-value {{
                font-size: 1.1rem;
            }}
            
            .course-grid {{
                grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
                gap: 6px;
            }}
            
            .course-cell {{
                padding: 6px 8px;
                font-size: 0.7rem;
            }}
            
            .modal-overlay {{
                padding: 0;
                align-items: flex-end;
            }}
            
            .modal {{
                max-height: 90vh;
                border-radius: 12px 12px 0 0;
                will-change: transform;
            }}
            
            /* Prevent background scroll in modal on mobile */
            .modal-body {{
                -webkit-overflow-scrolling: touch;
            }}
            
            .modal-header {{
                padding: 15px;
            }}
            
            .modal-header h2 {{
                font-size: 1.1rem;
            }}
            
            .modal-body {{
                padding: 15px;
            }}
            
            .section-list {{
                grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
                gap: 8px;
            }}
            
            .section-item {{
                padding: 10px;
            }}
            
            .chart-container {{
                padding: 10px;
                min-height: 250px;
            }}
            
            #enrollmentChart {{
                max-height: 280px;
            }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>📊 Enrollment Monitor</h1>
        <div class="semester-toggle" id="semesterToggle"></div>
        <p id="lastUpdated">Last updated N/A</p>
        <div class="stats">
            <div class="stat">
                <div class="stat-value" id="totalCourses">0</div>
                <div class="stat-label">Courses</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="totalSections">0</div>
                <div class="stat-label">Sections</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="fullSections">0</div>
                <div class="stat-label">Full</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="snapshotCount">0</div>
                <div class="stat-label">Snapshots</div>
            </div>
        </div>
    </header>
    
    <nav class="jump-to-nav" id="jumpToNav"></nav>
    
    <div class="course-grid" id="courseGrid"></div>
    
    <div class="modal-overlay" id="modalOverlay">
        <div class="modal">
            <div class="modal-header">
                <div class="modal-header-left">
                    <h2 id="modalTitle">Course Details</h2>
                </div>
                <button class="close-btn" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="section-type-selector" id="sectionTypeSelector"></div>
                <div class="section-list" id="sectionList"></div>
                <div class="chart-container" id="chartContainer">
                    <div class="chart-placeholder" id="chartPlaceholder">
                        Click a section to view enrollment history
                    </div>
                    <canvas id="enrollmentChart" class="chart-hidden"></canvas>
                    <div class="chart-legend" id="chartLegend" style="display: none;">
                        <div class="chart-legend-item">
                            <div class="chart-legend-dot" style="background: #4ecdc4;"></div>
                            <span>Capacity changed</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        const COMBINED_DATA = {json_data};
        
        let activeSemester = localStorage.getItem('activeSemester') || COMBINED_DATA.activeSemester;
        // Validate stored semester exists
        if (!COMBINED_DATA.semesters.includes(activeSemester)) {{
            activeSemester = COMBINED_DATA.activeSemester;
        }}
        
        let chart = null;
        let selectedCourse = null;
        let selectedSection = null;
        let viewingGraph = false;
        let scrollPositionBeforeModal = 0;
        let currentEnrollmentData = [];
        
        // Get current semester data and milestones
        function getData() {{
            return COMBINED_DATA.semesterData[activeSemester];
        }}
        
        function getMilestones() {{
            return COMBINED_DATA.milestonesData[activeSemester] || [];
        }}
        
        // Helper to get contrasting text color (black or white) based on background
        function getContrastColor(hexColor) {{
            const hex = hexColor.replace('#', '');
            const r = parseInt(hex.substring(0, 2), 16);
            const g = parseInt(hex.substring(2, 4), 16);
            const b = parseInt(hex.substring(4, 6), 16);
            const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
            return luminance > 0.5 ? '#1a1a2e' : '#ffffff';
        }}
        
        function formatCourseCode(code) {{
            const parts = code.split(' ');
            if (parts.length !== 2) return code;
            const [dept, num] = parts;
            return `${{dept}} ${{num}}`;
        }}
        
        function getStatusClass(fill, isFilled = false) {{
            if (isFilled || fill >= 1.0) return 'full';
            if (fill >= 0.75) return 'near';
            return '';
        }}
        
        function getSectionTypeName(type) {{
            const names = {{
                'L': 'Lecture',
                'S': 'Seminar',
                'R': 'Recitation',
                'D': 'Discussion',
                'B': 'Lab',
                'Lb': 'Lab',
                'Int': 'Internship',
                'P': 'Project',
                'IS': 'Independent Study',
                'T': 'Tutorial',
            }};
            return names[type] || type || 'Section';
        }}
        
        function formatDate(isoString) {{
            if (!isoString) return 'N/A';
            const date = new Date(isoString);
            return date.toLocaleDateString('en-US', {{
                year: 'numeric',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            }});
        }}
        
        function renderSemesterToggle() {{
            const toggle = document.getElementById('semesterToggle');
            toggle.innerHTML = COMBINED_DATA.semesters.map(sem => `
                <button class="semester-btn ${{sem === activeSemester ? 'active' : ''}}" 
                        onclick="switchSemester('${{sem}}')">${{sem}}</button>
            `).join('');
        }}
        
        function switchSemester(semester) {{
            activeSemester = semester;
            localStorage.setItem('activeSemester', semester);
            closeModal();
            renderSemesterToggle();
            renderCourseGrid();
        }}
        
        function renderCourseGrid() {{
            const DATA = getData();
            const grid = document.getElementById('courseGrid');
            grid.innerHTML = '';
            
            // Update last updated text
            document.getElementById('lastUpdated').textContent = 
                `${{activeSemester}} • Last updated ${{formatDate(DATA.lastReportTime)}}`;
            
            // Group courses by department
            const deptCourses = {{}};
            for (const [code, course] of Object.entries(DATA.courses)) {{
                const dept = course.department;
                if (!deptCourses[dept]) deptCourses[dept] = [];
                deptCourses[dept].push({{ code, ...course }});
            }}
            
            // Sort departments alphabetically
            const sortedDepts = Object.keys(deptCourses).sort();
            
            let totalCourses = 0;
            let totalSections = 0;
            let fullSections = 0;
            
            for (const dept of sortedDepts) {{
                const courses = deptCourses[dept];
                
                // Department header with ID for jump-to and back-to-top link
                const header = document.createElement('div');
                header.className = 'dept-header';
                header.id = `dept-${{dept}}`;
                header.innerHTML = `
                    <span>${{dept}}</span>
                    <a href="#" class="back-to-top" onclick="event.preventDefault(); window.scrollTo({{top: 0, behavior: 'smooth'}});">↑ Top</a>
                `;
                grid.appendChild(header);
                
                // Sort courses by code
                courses.sort((a, b) => a.code.localeCompare(b.code));
                
                for (const course of courses) {{
                    totalCourses++;
                    const sectionCount = Object.keys(course.sections).length;
                    totalSections += sectionCount;
                    
                    for (const section of Object.values(course.sections)) {{
                        if (section.currentFill >= 1.0) fullSections++;
                    }}
                    
                    const cell = document.createElement('div');
                    cell.className = `course-cell ${{getStatusClass(course.averageFill, course.isFilled)}}`;
                    cell.setAttribute('data-course', course.code);
                    cell.innerHTML = `
                        <span class="course-code">${{formatCourseCode(course.code)}}</span>
                        <span class="course-fill">${{Math.round(course.averageFill * 100)}}%</span>
                    `;
                    cell.onclick = () => openCourse(course.code);
                    grid.appendChild(cell);
                }}
            }}
            
            // Update stats
            document.getElementById('totalCourses').textContent = totalCourses;
            document.getElementById('totalSections').textContent = totalSections;
            document.getElementById('fullSections').textContent = fullSections;
            document.getElementById('snapshotCount').textContent = DATA.snapshots.length;
            
            // Render jump-to navigation
            const jumpNav = document.getElementById('jumpToNav');
            jumpNav.innerHTML = sortedDepts.map(dept => 
                `<a href="#dept-${{dept}}">${{dept}}</a>`
            ).join('');
        }}
        
        function openCourse(courseCode) {{
            const DATA = getData();
            selectedCourse = courseCode;
            selectedSection = null;
            viewingGraph = false;
            
            // Store scroll position and lock body
            scrollPositionBeforeModal = window.scrollY;
            
            const course = DATA.courses[courseCode];
            document.getElementById('modalTitle').textContent = `${{courseCode}}${{course.title ? ' - ' + course.title : ''}}`;
            
            const sectionList = document.getElementById('sectionList');
            sectionList.innerHTML = '';
            
            // Sort sections by type then by ID
            const sections = Object.entries(course.sections).sort((a, b) => {{
                const typePriority = {{ L: 0, S: 1, R: 1, D: 1, B: 2, Lb: 2 }};
                const pa = typePriority[a[1].type] ?? 3;
                const pb = typePriority[b[1].type] ?? 3;
                if (pa !== pb) return pa - pb;
                return a[0].localeCompare(b[0], undefined, {{ numeric: true }});
            }});
            
            // Group sections by type for the selector
            const sectionsByType = {{}};
            for (const [sectionCode, section] of sections) {{
                const type = section.type || 'Other';
                if (!sectionsByType[type]) sectionsByType[type] = [];
                sectionsByType[type].push({{ code: sectionCode, ...section }});
            }}
            
            // Render section type selector
            const sectionTypeSelector = document.getElementById('sectionTypeSelector');
            sectionTypeSelector.innerHTML = '';
            
            for (const [type, typeSections] of Object.entries(sectionsByType)) {{
                const typeGroup = document.createElement('div');
                typeGroup.className = 'section-type-group';
                
                const typeLabel = document.createElement('div');
                typeLabel.className = 'section-type-label';
                typeLabel.textContent = getSectionTypeName(type);
                typeGroup.appendChild(typeLabel);
                
                const groupList = document.createElement('div');
                groupList.className = 'section-list';
                groupList.style.marginBottom = '0';
                
                for (const section of typeSections) {{
                    const item = document.createElement('div');
                    item.className = `section-item ${{getStatusClass(section.currentFill)}}`;
                    item.id = `section-${{section.code}}`;
                    item.innerHTML = `
                        <div class="section-id">${{section.code}}</div>
                        ${{section.instructor ? `<div class="section-instructor">${{section.instructor}}</div>` : ''}}
                        <div class="section-stats">
                            <span class="section-fill">${{Math.round(section.currentFill * 100)}}%</span>
                            <span>(${{section.currentEnrollment}}/${{section.currentCapacity}})</span>
                        </div>
                    `;
                    item.onclick = () => selectSection(section.code);
                    groupList.appendChild(item);
                }}
                
                typeGroup.appendChild(groupList);
                sectionTypeSelector.appendChild(typeGroup);
            }}
            
            // Show average fill chart by default
            document.getElementById('modalOverlay').classList.add('active');
            
            // Use setTimeout to ensure modal is visible before rendering chart
            setTimeout(() => {{
                showAverageFillChart(courseCode);
            }}, 50);
            document.body.classList.add('modal-open');
            document.body.style.top = `-${{scrollPositionBeforeModal}}px`;
        }}
        
        function showAverageFillChart(courseCode) {{
            const DATA = getData();
            const course = DATA.courses[courseCode];
            const sectionsArr = Object.values(course.sections);
            
            // Build average fill data across all snapshots
            const snapshotFills = {{}}; // snapshotIdx -> [fills]
            for (const section of sectionsArr) {{
                for (const point of section.history) {{
                    if (!snapshotFills[point.snapshotIdx]) {{
                        snapshotFills[point.snapshotIdx] = [];
                    }}
                    snapshotFills[point.snapshotIdx].push(point.fill);
                }}
            }}
            
            // Sort by snapshot index and compute averages
            const sortedIndices = Object.keys(snapshotFills).map(Number).sort((a, b) => a - b);
            const labels = [];
            const fillData = [];
            const timestamps = [];
            currentEnrollmentData = [];
            
            for (const idx of sortedIndices) {{
                const snapshot = DATA.snapshots[idx];
                if (snapshot) {{
                    const fills = snapshotFills[idx];
                    const avgFill = fills.reduce((a, b) => a + b, 0) / fills.length;
                    const date = new Date(snapshot.timestamp);
                    timestamps.push(date.getTime());
                    labels.push(date.toLocaleDateString('en-US', {{ 
                        month: 'short', 
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit'
                    }}));
                    fillData.push(Math.round(avgFill * 100));
                    currentEnrollmentData.push({{
                        enrollment: null,
                        capacity: null,
                        prevCapacity: null,
                        capacityChanged: false
                    }});
                }}
            }}
            
            document.getElementById('chartLegend').style.display = 'none';
            renderChart('Average Fill', labels, fillData, timestamps, false);
        }}
        
        function selectSection(sectionCode) {{
            const DATA = getData();
            
            // If clicking the same section, deselect and show average fill
            if (selectedSection === sectionCode) {{
                document.getElementById(`section-${{sectionCode}}`)?.classList.remove('selected');
                selectedSection = null;
                viewingGraph = false;
                currentEnrollmentData = [];
                showAverageFillChart(selectedCourse);
                return;
            }}
            
            // Update selection styling
            if (selectedSection) {{
                document.getElementById(`section-${{selectedSection}}`)?.classList.remove('selected');
            }}
            selectedSection = sectionCode;
            viewingGraph = true;
            document.getElementById(`section-${{sectionCode}}`)?.classList.add('selected');
            
            const section = DATA.courses[selectedCourse].sections[sectionCode];
            
            // Prepare chart data with enrollment info and capacity changes
            const labels = [];
            const fillData = [];
            const timestamps = [];
            currentEnrollmentData = [];
            let prevCapacity = null;
            
            for (const point of section.history) {{
                const snapshot = DATA.snapshots[point.snapshotIdx];
                if (snapshot) {{
                    const date = new Date(snapshot.timestamp);
                    timestamps.push(date.getTime());
                    labels.push(date.toLocaleDateString('en-US', {{ 
                        month: 'short', 
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit'
                    }}));
                    fillData.push(Math.round(point.fill * 100));
                    
                    const capacityChanged = prevCapacity !== null && point.capacity !== prevCapacity;
                    currentEnrollmentData.push({{
                        enrollment: point.enrollment,
                        capacity: point.capacity,
                        prevCapacity: prevCapacity,
                        capacityChanged: capacityChanged
                    }});
                    prevCapacity = point.capacity;
                }}
            }}
            
            // Show legend if there are capacity changes
            const hasCapacityChanges = currentEnrollmentData.some(d => d.capacityChanged);
            document.getElementById('chartLegend').style.display = hasCapacityChanges ? 'flex' : 'none';
            
            renderChart(`${{sectionCode}} Enrollment %`, labels, fillData, timestamps, true);
        }}
        
        function renderChart(chartLabel, labels, fillData, timestamps, showCapacityMarkers) {{
            const MILESTONES = getMilestones();
            
            // Build annotation lines for milestones within our data range
            const annotations = {{}};
            if (timestamps.length > 0) {{
                const minTime = Math.min(...timestamps);
                const maxTime = Math.max(...timestamps);
                
                MILESTONES.forEach((m, idx) => {{
                    const mTime = new Date(m.time).getTime();
                    if (mTime >= minTime && mTime <= maxTime) {{
                        // Find the closest label index
                        let closestIdx = 0;
                        let minDiff = Infinity;
                        timestamps.forEach((t, i) => {{
                            const diff = Math.abs(t - mTime);
                            if (diff < minDiff) {{
                                minDiff = diff;
                                closestIdx = i;
                            }}
                        }});
                        
                        // Get fill value at this point to determine label position
                        const fillAtPoint = fillData[closestIdx] || 0;
                        const labelPos = fillAtPoint > 50 ? 'start' : 'end';
                        
                        // Line annotation with label (z controls layering)
                        annotations[`line${{idx}}`] = {{
                            type: 'line',
                            xMin: closestIdx,
                            xMax: closestIdx,
                            borderColor: m.color,
                            borderWidth: 2,
                            borderDash: [5, 3],
                            drawTime: 'beforeDatasetsDraw',
                            label: {{
                                display: true,
                                content: m.label,
                                position: labelPos,
                                backgroundColor: m.color,
                                color: getContrastColor(m.color),
                                font: {{ size: 9, weight: 'bold' }},
                                padding: 3,
                                borderRadius: 3,
                                z: 10,
                                drawTime: 'afterDatasetsDraw',
                            }}
                        }};
                    }}
                }});
            }}
            
            // Create or update chart
            document.getElementById('chartPlaceholder').style.display = 'none';
            const canvas = document.getElementById('enrollmentChart');
            
            // Remove hidden class first, then force reflow to ensure dimensions are set
            canvas.classList.remove('chart-hidden');
            canvas.offsetHeight; // Force reflow
            
            if (chart) {{
                chart.destroy();
                chart = null;
            }}
            
            // Build point styling arrays for capacity change markers
            const pointStyles = currentEnrollmentData.map(d => 
                showCapacityMarkers && d.capacityChanged ? 'rectRot' : 'circle'
            );
            const pointColors = currentEnrollmentData.map(d => 
                showCapacityMarkers && d.capacityChanged ? '#4ecdc4' : '#ffd700'
            );
            const pointRadii = currentEnrollmentData.map(d => 
                showCapacityMarkers && d.capacityChanged ? 7 : (labels.length > 50 ? 0 : 3)
            );
            const pointBorderColors = currentEnrollmentData.map(d =>
                showCapacityMarkers && d.capacityChanged ? '#ffffff' : '#ffd700'
            );
            const pointBorderWidths = currentEnrollmentData.map(d =>
                showCapacityMarkers && d.capacityChanged ? 2 : 1
            );
            
            chart = new Chart(canvas, {{
                type: 'line',
                data: {{
                    labels: labels,
                    datasets: [{{
                        label: chartLabel,
                        data: fillData,
                        borderColor: '#ffd700',
                        backgroundColor: 'rgba(255, 215, 0, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointStyle: pointStyles,
                        pointRadius: pointRadii,
                        pointHoverRadius: 6,
                        pointBackgroundColor: pointColors,
                        pointBorderColor: pointBorderColors,
                        pointBorderWidth: pointBorderWidths,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {{
                        annotation: {{
                            annotations: annotations
                        }},
                        legend: {{
                            labels: {{
                                color: '#eaeaea',
                                font: {{ family: 'monospace' }}
                            }}
                        }},
                        tooltip: {{
                            backgroundColor: '#1a1a2e',
                            titleColor: '#ffd700',
                            bodyColor: '#eaeaea',
                            borderColor: '#3a3a5e',
                            borderWidth: 1,
                            callbacks: {{
                                label: (ctx) => {{
                                    const idx = ctx.dataIndex;
                                    const enrollInfo = currentEnrollmentData[idx];
                                    if (enrollInfo && enrollInfo.enrollment !== null) {{
                                        let label = `${{ctx.parsed.y}}% (${{enrollInfo.enrollment}}/${{enrollInfo.capacity}})`;
                                        if (enrollInfo.capacityChanged) {{
                                            label += ` • Cap: ${{enrollInfo.prevCapacity}} → ${{enrollInfo.capacity}}`;
                                        }}
                                        return label;
                                    }}
                                    return `${{ctx.dataset.label}}: ${{ctx.parsed.y}}%`;
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{
                            ticks: {{
                                display: false
                            }},
                            grid: {{
                                color: 'rgba(255,255,255,0.05)'
                            }}
                        }},
                        y: {{
                            min: 0,
                            suggestedMax: 100,
                            ticks: {{
                                display: false
                            }},
                            grid: {{
                                color: 'rgba(255,255,255,0.05)'
                            }}
                        }}
                    }},
                    interaction: {{
                        intersect: false,
                        mode: 'index'
                    }}
                }}
            }});
        }}
        
        function closeModal() {{
            document.getElementById('modalOverlay').classList.remove('active');
            document.body.classList.remove('modal-open');
            document.body.style.top = '';
            window.scrollTo(0, scrollPositionBeforeModal);
            selectedCourse = null;
            selectedSection = null;
            viewingGraph = false;
            currentEnrollmentData = [];
            document.getElementById('chartLegend').style.display = 'none';
            if (chart) {{
                chart.destroy();
                chart = null;
            }}
        }}
        
        // Clear chart active elements on touch end (fix persistent dot)
        function clearChartActiveElements() {{
            if (chart) {{
                chart.setActiveElements([]);
                chart.tooltip.setActiveElements([]);
                chart.update('none');
            }}
        }}
        
        // Close modal on overlay click
        document.getElementById('modalOverlay').addEventListener('click', (e) => {{
            if (e.target.id === 'modalOverlay') closeModal();
        }});
        
        // Close modal on escape key
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') closeModal();
        }});
        
        // Clear chart hover on touch end
        document.getElementById('chartContainer').addEventListener('touchend', () => {{
            setTimeout(clearChartActiveElements, 100);
        }});
        
        // Clear chart hover when clicking elsewhere in modal body
        document.querySelector('.modal-body').addEventListener('click', (e) => {{
            if (!e.target.closest('#chartContainer')) {{
                clearChartActiveElements();
            }}
        }});
        
        // Initialize
        renderSemesterToggle();
        renderCourseGrid();
    </script>
</body>
</html>"""

    return html


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate prototype HTML page for enrollment data visualization"
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy to Cloudflare Workers after generating HTML",
    )
    parser.add_argument(
        "--semester",
        choices=["spring2026", "fall2025", "summer2025"],
        default="spring2026",
        help="Semester to generate website for (default: spring2026)",
    )
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Generate a single HTML file with all semesters and a toggle selector",
    )
    args = parser.parse_args()

    # Map CLI argument to semester name
    semester_map = {
        "spring2026": "Spring 2026",
        "fall2025": "Fall 2025",
        "summer2025": "Summer 2025",
    }
    semester = semester_map[args.semester]

    # Define milestones for each semester
    milestones_map = {
        "Spring 2026": [
            # First Priority - December 17 (warm: red-orange gradient)
            {"time": "2025-12-17T09:00:00", "label": "Y4+", "color": "#FF1744"},
            {"time": "2025-12-17T11:00:00", "label": "Y3", "color": "#FF5722"},
            {"time": "2025-12-17T13:00:00", "label": "Y2", "color": "#FF9100"},
            {"time": "2025-12-17T15:00:00", "label": "Y1", "color": "#FFC400"},
            # Second Priority - December 18 (cool: cyan-blue gradient)
            {"time": "2025-12-18T09:00:00", "label": "Y4+", "color": "#00E5FF"},
            {"time": "2025-12-18T11:00:00", "label": "Y3", "color": "#00B0FF"},
            {"time": "2025-12-18T13:00:00", "label": "Y2", "color": "#2979FF"},
            {"time": "2025-12-18T15:00:00", "label": "Y1", "color": "#651FFF"},
            # Third Priority - December 19 (distinct: magenta)
            {"time": "2025-12-19T09:00:00", "label": "ALL", "color": "#D500F9"},
        ],
        "Fall 2025": [
            # First Priority - August 6 (warm: red-orange gradient)
            {"time": "2025-08-06T09:00:00", "label": "Y4+", "color": "#FF1744"},
            {"time": "2025-08-06T11:00:00", "label": "Y3", "color": "#FF5722"},
            {"time": "2025-08-06T13:00:00", "label": "Y2", "color": "#FF9100"},
            # First Priority continues - August 13
            {"time": "2025-08-13T09:00:00", "label": "Y1", "color": "#FFC400"},
            # Second Priority - August 14 (cool: cyan-blue gradient)
            {"time": "2025-08-14T09:00:00", "label": "Y4+", "color": "#00E5FF"},
            {"time": "2025-08-14T11:00:00", "label": "Y3", "color": "#00B0FF"},
            {"time": "2025-08-14T13:00:00", "label": "Y2", "color": "#2979FF"},
            {"time": "2025-08-14T15:00:00", "label": "Y1", "color": "#651FFF"},
            # Third Priority - August 15 (distinct: magenta)
            {"time": "2025-08-15T09:00:00", "label": "ALL", "color": "#D500F9"},
        ],
        "Summer 2025": [
            # First Priority - May 12 (warm: red-orange gradient)
            {"time": "2025-05-12T10:00:00", "label": "Y4+", "color": "#FF1744"},
            {"time": "2025-05-12T11:00:00", "label": "Y3", "color": "#FF5722"},
            {"time": "2025-05-12T12:00:00", "label": "Y2", "color": "#FF9100"},
            {"time": "2025-05-12T13:00:00", "label": "Y1", "color": "#FFC400"},
            # Second Priority - May 13 (cool: cyan-blue gradient)
            {"time": "2025-05-13T10:00:00", "label": "Y4+", "color": "#00E5FF"},
            {"time": "2025-05-13T11:00:00", "label": "Y2/Y1", "color": "#2979FF"},
            # Third Priority - May 14 (distinct: magenta)
            {"time": "2025-05-14T10:00:00", "label": "ALL", "color": "#D500F9"},
        ],
    }
    milestones = milestones_map.get(semester, [])

    # Save to assets directory
    output_dir = Path(__file__).parent.parent / "assets" / "website" / "public"
    output_dir.mkdir(exist_ok=True, parents=True)

    if args.combined:
        # Generate combined HTML with all semesters
        print("Generating combined prototype for all semesters...")
        combined_data = get_combined_data(milestones_map)

        # Check if we have any data
        total_courses = sum(
            len(d["courses"]) for d in combined_data["semesterData"].values()
        )
        if total_courses == 0:
            print("No courses found in any semester!")
            return

        for sem, data in combined_data["semesterData"].items():
            print(
                f"  {sem}: {len(data['courses'])} courses, {len(data['snapshots'])} snapshots"
            )

        html = generate_combined_html(combined_data)
        output_path = output_dir / "index.html"
    else:
        # Generate single semester HTML
        print(f"Generating prototype for {semester}...")

        # Get data from database
        data = get_semester_data(semester)

        if not data["courses"]:
            print("No courses found!")
            return

        print(
            f"Found {len(data['courses'])} courses with {len(data['snapshots'])} snapshots"
        )

        # Generate HTML
        html = generate_html(data, milestones)
        output_path = output_dir / "index.html"

    output_path.write_text(html)
    print(f"Prototype saved to: {output_path}")

    # Deploy to Cloudflare Workers if --deploy flag is set
    if args.deploy:
        print("\nDeploying to Cloudflare Workers...")
        deploy_cmd = [
            "npx",
            "wrangler",
            "deploy",
            f"--assets={output_dir}",
            "--name=monitor",
            "--compatibility-date=2025-12-18",
        ]
        result = subprocess.run(deploy_cmd, cwd=Path(__file__).parent.parent)
        if result.returncode == 0:
            print("Deployment successful!")
        else:
            print(f"Deployment failed with exit code: {result.returncode}")


if __name__ == "__main__":
    main()
