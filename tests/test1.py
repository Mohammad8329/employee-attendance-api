"""Purpose: Unit tests for foundational helpers, business rules R1-R5, and health check.
Validates:
- Mathematical half-up rounding (R4, R8)
- Timestamp conversions and whole-second truncation
- Work hours, late minutes (R2 grace period), and overtime (R3) calculations
- Overnight shift date determination (R1) and half-day checks (R5)
- MongoDB connectivity (/health) and index creation
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.main import (
    round_half_up,
    ms_to_datetime,
    datetime_to_ms,
    compute_work_hours,
    compute_late_minutes,
    compute_overtime,
    determine_attendance_date,
    compute_half_day,
    health,
    init_db,
    db,
    IST,
)


def test_round_half_up():
    print("Testing round_half_up...")
    # Banker's rounding round(2.675, 2) in python gives 2.67. Half-up must give 2.68.
    assert round_half_up(2.675, 2) == 2.68, f"Expected 2.68, got {round_half_up(2.675, 2)}"
    assert round_half_up(2.674, 2) == 2.67
    assert round_half_up(2.5, 0) == 3.0
    print("  [PASS] round_half_up")


def test_timestamp_conversions():
    print("Testing timestamp converters...")
    # ms_to_datetime must truncate sub-seconds (milliseconds mod 1000)
    # 1772422200555 -> 1772422200000
    dt = ms_to_datetime(1772422200555)
    assert dt.microsecond == 0, "Sub-seconds must be truncated to 0"
    ms = datetime_to_ms(dt)
    assert ms == 1772422200000, f"Expected 1772422200000, got {ms}"
    assert datetime_to_ms(None) is None
    print("  [PASS] timestamp converters")


def test_compute_work_hours():
    print("Testing compute_work_hours...")
    # 8 hours, 58 minutes, 30 seconds = 8 + 58/60 + 30/3600 = 8.975 -> half-up rounds to 8.98
    t_in = datetime(2026, 7, 6, 9, 0, 0, tzinfo=timezone.utc)
    t_out = datetime(2026, 7, 6, 17, 58, 30, tzinfo=timezone.utc)
    wh = compute_work_hours(t_in, t_out)
    assert wh == 8.98, f"Expected 8.98, got {wh}"
    print("  [PASS] compute_work_hours")


def test_compute_late_minutes():
    print("Testing compute_late_minutes (Rule R2)...")
    # Shift: 09:30 IST on date 2026-07-06.
    # 09:30 IST is 04:00 UTC.
    # Punch-in at 09:40:00 IST (04:10:00 UTC) -> exactly 10 min -> late_minutes = 0 (grace period)
    t_exact_grace = datetime(2026, 7, 6, 4, 10, 0, tzinfo=timezone.utc)
    assert compute_late_minutes(t_exact_grace, "09:30", "2026-07-06") == 0

    # Punch-in at 09:40:01 IST (04:10:01 UTC) -> strictly > 10 min -> late_minutes = 10
    t_late = datetime(2026, 7, 6, 4, 10, 1, tzinfo=timezone.utc)
    assert compute_late_minutes(t_late, "09:30", "2026-07-06") == 10

    # Punch-in at 10:15:00 IST (04:45:00 UTC) -> 45 minutes late
    t_very_late = datetime(2026, 7, 6, 4, 45, 0, tzinfo=timezone.utc)
    assert compute_late_minutes(t_very_late, "09:30", "2026-07-06") == 45
    print("  [PASS] compute_late_minutes")


def test_compute_overtime():
    print("Testing compute_overtime (Rule R3)...")
    # Standard shift: 09:30 to 18:30 IST.
    # 18:30 IST is 13:00 UTC.
    # Punch-out at 18:55 IST (13:25 UTC) -> 25 min late -> overtime = 0 (< 30 min)
    t_out_25 = datetime(2026, 7, 6, 13, 25, 0, tzinfo=timezone.utc)
    assert compute_overtime(t_out_25, "09:30", "18:30", "2026-07-06") == 0

    # Punch-out at 19:10 IST (13:40 UTC) -> 40 min late -> overtime = 40
    t_out_40 = datetime(2026, 7, 6, 13, 40, 0, tzinfo=timezone.utc)
    assert compute_overtime(t_out_40, "09:30", "18:30", "2026-07-06") == 40

    # Overnight shift: 22:00 to 06:00 IST on date 2026-07-06.
    # Shift end is 2026-07-07 06:00 IST (00:30 UTC on July 7).
    # Punch out at 06:45 IST on July 7 (01:15 UTC) -> 45 minutes overtime
    t_out_overnight = datetime(2026, 7, 7, 1, 15, 0, tzinfo=timezone.utc)
    ot = compute_overtime(t_out_overnight, "22:00", "06:00", "2026-07-06")
    assert ot == 45, f"Expected 45, got {ot}"
    print("  [PASS] compute_overtime")


def test_determine_attendance_date():
    print("Testing determine_attendance_date (Rule R1)...")
    # Overnight shift: 22:00 to 06:00.
    # Punch-in at 2026-07-07 01:30 AM IST (belongs to 2026-07-06 shift)
    # 01:30 AM IST on July 7 = July 6 20:00 UTC
    punch_night = datetime(2026, 7, 6, 20, 0, 0, tzinfo=timezone.utc)
    d = determine_attendance_date(punch_night, "22:00", "06:00")
    assert d == "2026-07-06", f"Expected 2026-07-06, got {d}"

    # Daytime shift: 09:30 to 18:30 on 2026-07-06
    punch_day = datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc)
    d_day = determine_attendance_date(punch_day, "09:30", "18:30")
    assert d_day == "2026-07-06", f"Expected 2026-07-06, got {d_day}"
    print("  [PASS] determine_attendance_date")


def test_compute_half_day():
    print("Testing compute_half_day (Rule R5)...")
    assert compute_half_day(4.49) is True
    assert compute_half_day(4.50) is False
    assert compute_half_day(8.00) is False
    assert compute_half_day(None) is False
    print("  [PASS] compute_half_day")


def test_health_and_indexes():
    print("Testing GET /health and database indexes...")
    init_db()
    h = health()
    assert h == {"status": "ok"}, f"Expected {{'status': 'ok'}}, got {h}"

    emp_indexes = db.employees.index_information()
    assert "emp_code_1" in emp_indexes and emp_indexes["emp_code_1"]["unique"] is True
    assert "department_1_joined_on_1" in emp_indexes

    att_indexes = db.attendance_logs.index_information()
    assert "emp_code_1_date_1" in att_indexes and att_indexes["emp_code_1_date_1"]["unique"] is True
    assert "date_-1_emp_code_1" in att_indexes
    assert "status_1_date_1" in att_indexes
    print("  [PASS] health & indexes")


if __name__ == "__main__":
    test_round_half_up()
    test_timestamp_conversions()
    test_compute_work_hours()
    test_compute_late_minutes()
    test_compute_overtime()
    test_determine_attendance_date()
    test_compute_half_day()
    test_health_and_indexes()
    print("\nALL TEST 1 SUITE TESTS PASSED SUCCESSFULLY!")
