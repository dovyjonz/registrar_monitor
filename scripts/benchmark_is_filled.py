import json
import timeit
import sys
from pathlib import Path

# Add src to python path so we can import models
sys.path.append(str(Path(__file__).parent.parent / "src"))

from registrarmonitor.models import EnrollmentSnapshot, Course

def load_snapshot(filepath):
    with open(filepath, "r") as f:
        data = json.load(f)
    return EnrollmentSnapshot.from_dict(data)

def benchmark_is_filled(courses, iterations=1000):
    start_time = timeit.default_timer()

    count = 0
    for _ in range(iterations):
        for course in courses.values():
            if course.is_filled:
                count += 1

    end_time = timeit.default_timer()
    return end_time - start_time, count

def main():
    data_file = Path("data/spring_2026_2025-12-18_15-45-00.json")
    if not data_file.exists():
        print(f"Error: Data file {data_file} not found.")
        return

    print(f"Loading data from {data_file}...")
    snapshot = load_snapshot(data_file)
    courses = snapshot.courses
    print(f"Loaded {len(courses)} courses.")

    iterations = 1000
    print(f"Benchmarking is_filled with {iterations} iterations over all courses...")

    duration, count = benchmark_is_filled(courses, iterations)

    total_calls = len(courses) * iterations
    avg_time_per_call = (duration / total_calls) * 1_000_000 # microseconds

    print(f"Total time: {duration:.4f} seconds")
    print(f"Total calls: {total_calls}")
    print(f"Average time per call: {avg_time_per_call:.4f} microseconds")
    print(f"Result checksum (filled count): {count}")

if __name__ == "__main__":
    main()
