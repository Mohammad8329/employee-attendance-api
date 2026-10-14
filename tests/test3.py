"""Purpose: Integration tests for MongoDB aggregation analytics endpoints.
Validates:
- GET /analytics/employees/{emp_code}/monthly: Mon-Fri working days (R7), mid-month joiners, weekend logs, attendance % (R8)
- GET /analytics/departments/summary: Cross-collection $lookup, headcount including 0-log staff (R9), department averages
- GET /analytics/leaderboard/late: Window function ranking ($rank) with ties (1, 2, 2, 4), post-ranking limit
- GET /analytics/departments/{department}/trend: Daily densified calendar ($densify), 7-day moving window, weekend nulls
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi import HTTPException

from app.main import (
    employee_monthly_summary,
    department_summary,
    late_leaderboard,
    department_trend,
    db,
    init_db,
)


def setup_db():
    init_db()


def test_employee_monthly_summary():
    print("Testing GET /analytics/employees/{emp_code}/monthly...")
    # 1. 422 for invalid month format
    try:
        employee_monthly_summary("EMP0001", "2026-7")
        assert False, "Should raise 422 for invalid month format"
    except HTTPException as e:
        assert e.status_code == 422

    try:
        employee_monthly_summary("EMP0001", "invalid-month")
        assert False, "Should raise 422 for invalid month"
    except HTTPException as e:
        assert e.status_code == 422

    # 2. 404 for unknown employee
    try:
        employee_monthly_summary("EMP99999", "2026-07")
        assert False, "Should raise 404 for unknown employee"
    except HTTPException as e:
        assert e.status_code == 404

    # 3. Existing sample employee EMP0001 for 2026-07
    # Joined on 2026-01-15 -> 23 working days in July 2026
    # 2 present days (July 6, July 7) -> 2 / 23 * 100 = 8.6956... -> 8.70%
    res = employee_monthly_summary("EMP0001", "2026-07")
    assert res["emp_code"] == "EMP0001"
    assert res["month"] == "2026-07"
    assert res["working_days"] == 23
    assert res["present_days"] == 2.0
    assert res["leave_days"] == 0
    assert res["late_count"] == 0
    assert res["total_late_minutes"] == 0
    assert res["total_overtime_minutes"] == 0
    assert res["attendance_pct"] == 8.7, f"Expected 8.7, got {res['attendance_pct']}"

    # 4. Mid-month joiner (Rule R7)
    # Employee joins on 2026-07-27 (Monday). Only 5 working days remaining in July!
    db.employees.delete_one({"emp_code": "EMP7777"})
    db.attendance_logs.delete_many({"emp_code": "EMP7777"})
    db.employees.insert_one({
        "emp_code": "EMP7777",
        "name": "Mid Month Joiner",
        "email": "mid@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-07-27",
    })
    res_mid = employee_monthly_summary("EMP7777", "2026-07")
    assert res_mid["working_days"] == 5, f"Expected 5 working days, got {res_mid['working_days']}"
    assert res_mid["present_days"] == 0.0
    assert res_mid["attendance_pct"] == 0.0

    # 5. Future joiner (joins next month) -> working_days 0, attendance_pct None
    db.employees.delete_one({"emp_code": "EMP7778"})
    db.employees.insert_one({
        "emp_code": "EMP7778",
        "name": "Future Joiner",
        "email": "future@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-08-01",
    })
    res_future = employee_monthly_summary("EMP7778", "2026-07")
    assert res_future["working_days"] == 0
    assert res_future["attendance_pct"] is None

    # 6. Half-day and weekend rule (Rule R5 & R7)
    # Add a weekday half-day log (July 28, Tue)
    db.attendance_logs.insert_one({
        "emp_code": "EMP7777",
        "date": "2026-07-28",
        "status": "PRESENT",
        "work_hours": 4.0,
        "half_day": True,
        "late_minutes": 15,
        "overtime_minutes": 0,
        "history": [],
    })
    # Add a weekend log on Saturday July 25 (before join date or weekend)
    # Rule R7: weekend records still add to late and overtime totals, but NOT present_days
    db.attendance_logs.insert_one({
        "emp_code": "EMP7777",
        "date": "2026-07-25",
        "status": "PRESENT",
        "work_hours": 8.0,
        "half_day": False,
        "late_minutes": 30,
        "overtime_minutes": 60,
        "history": [],
    })
    # Add a LEAVE record on July 29
    db.attendance_logs.insert_one({
        "emp_code": "EMP7777",
        "date": "2026-07-29",
        "status": "LEAVE",
        "work_hours": None,
        "half_day": False,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "history": [],
    })

    res_complex = employee_monthly_summary("EMP7777", "2026-07")
    # Present days should only include the Tuesday half-day (0.5), weekend excluded!
    assert res_complex["present_days"] == 0.5, f"Expected 0.5, got {res_complex['present_days']}"
    assert res_complex["leave_days"] == 1
    # Late count: 1 on weekday (15m) + 1 on weekend (30m) = 2
    assert res_complex["late_count"] == 2
    assert res_complex["total_late_minutes"] == 45
    assert res_complex["total_overtime_minutes"] == 60
    # attendance_pct: 0.5 / 5 * 100 = 10.0%
    assert res_complex["attendance_pct"] == 10.0

    # Cleanup temporary test employees
    db.employees.delete_many({"emp_code": {"$in": ["EMP7777", "EMP7778"]}})
    db.attendance_logs.delete_many({"emp_code": {"$in": ["EMP7777", "EMP7778"]}})

    print("  [PASS] GET /analytics/employees/{emp_code}/monthly")


def test_department_summary():
    print("Testing GET /analytics/departments/summary...")
    # 1. 422 for invalid month format
    try:
        department_summary("2026-7")
        assert False, "Should raise 422 for invalid month"
    except HTTPException as e:
        assert e.status_code == 422

    # 2. Complete department summary for 2026-07 on sample seed
    res = department_summary("2026-07")
    assert res["month"] == "2026-07"
    items = res["items"]
    dept_names = [it["department"] for it in items]
    assert dept_names == sorted(dept_names), "Departments must be sorted ascending"
    assert "Engineering" in dept_names
    assert "Sales" in dept_names
    assert "Support" in dept_names

    eng = next(it for it in items if it["department"] == "Engineering")
    assert eng["headcount"] == 2
    assert eng["present_days"] == 3.0
    assert eng["avg_work_hours"] == 9.04
    assert eng["late_count"] == 0
    assert eng["total_late_minutes"] == 0

    sales = next(it for it in items if it["department"] == "Sales")
    assert sales["headcount"] == 2
    assert sales["present_days"] == 1.5
    assert sales["late_count"] == 1
    assert sales["total_late_minutes"] == 35
    assert sales["leave_count"] == 1
    assert sales["avg_work_hours"] == 6.29

    # 3. Single department filter
    res_filtered = department_summary("2026-07", department="Sales")
    assert len(res_filtered["items"]) == 1
    assert res_filtered["items"][0]["department"] == "Sales"

    # 4. Unknown department filter returns empty items list
    res_none = department_summary("2026-07", department="NonExistent")
    assert res_none["items"] == []

    # 5. Rule R9 Headcount test:
    # Add an employee with 0 logs who joined before end of month -> must count in headcount
    # Add an employee who joined after end of month -> must NOT count
    db.employees.delete_many({"emp_code": {"$in": ["EMP9901", "EMP9902"]}})
    db.employees.insert_one({
        "emp_code": "EMP9901",
        "name": "Zero Logs Guy",
        "email": "zero@example.com",
        "department": "Security",
        "shift_start": "09:00",
        "shift_end": "18:00",
        "joined_on": "2026-07-01",
    })
    db.employees.insert_one({
        "emp_code": "EMP9902",
        "name": "Joined August Guy",
        "email": "aug@example.com",
        "department": "Security",
        "shift_start": "09:00",
        "shift_end": "18:00",
        "joined_on": "2026-08-01",
    })
    res_sec = department_summary("2026-07", department="Security")
    assert len(res_sec["items"]) == 1
    sec_item = res_sec["items"][0]
    # EMP9901 counts, EMP9902 does not!
    assert sec_item["headcount"] == 1
    assert sec_item["present_days"] == 0.0
    assert sec_item["avg_work_hours"] is None

    # Clean up test employees
    db.employees.delete_many({"emp_code": {"$in": ["EMP9901", "EMP9902"]}})

    print("  [PASS] GET /analytics/departments/summary")


def test_late_leaderboard():
    print("Testing GET /analytics/leaderboard/late...")
    # 1. 422 for invalid month
    try:
        late_leaderboard("2026-7")
        assert False, "Should raise 422 for invalid month"
    except HTTPException as e:
        assert e.status_code == 422

    # 2. Existing sample data for 2026-07 (EMP0003 has 35 min late)
    res = late_leaderboard("2026-07")
    assert res["month"] == "2026-07"
    assert len(res["items"]) >= 1
    top = res["items"][0]
    assert top["rank"] == 1
    assert top["emp_code"] == "EMP0003"
    assert top["total_late_minutes"] == 35

    # 3. Test standard competition ranking ties (1, 2, 2, 4):
    # Add two employees who tie with 60 late minutes, and one with 20 late minutes
    db.employees.delete_many({"emp_code": {"$in": ["EMP9911", "EMP9912", "EMP9913"]}})
    db.attendance_logs.delete_many({"emp_code": {"$in": ["EMP9911", "EMP9912", "EMP9913"]}})

    for c, n in [("EMP9911", "Tie Guy A"), ("EMP9912", "Tie Guy B"), ("EMP9913", "Fourth Guy")]:
        db.employees.insert_one({
            "emp_code": c,
            "name": n,
            "email": f"{c}@example.com",
            "department": "TieDept",
            "shift_start": "09:00",
            "shift_end": "18:00",
            "joined_on": "2026-07-01",
        })

    # EMP9911 has 60 min late
    db.attendance_logs.insert_one({"emp_code": "EMP9911", "date": "2026-07-06", "late_minutes": 60})
    # EMP9912 has 60 min late (tie for rank 1!)
    db.attendance_logs.insert_one({"emp_code": "EMP9912", "date": "2026-07-06", "late_minutes": 60})
    # EMP9913 has 20 min late (should be rank 3!)
    db.attendance_logs.insert_one({"emp_code": "EMP9913", "date": "2026-07-06", "late_minutes": 20})

    res_dept = late_leaderboard("2026-07", department="TieDept")
    items = res_dept["items"]
    assert len(items) == 3
    assert items[0]["rank"] == 1 and items[0]["total_late_minutes"] == 60
    assert items[1]["rank"] == 1 and items[1]["total_late_minutes"] == 60
    # Standard competition ranking: next rank after tied 1, 1 is 3!
    assert items[2]["rank"] == 3 and items[2]["total_late_minutes"] == 20

    # Test limit applies AFTER ranking: limit=1 returns both rank 1 tied items!
    res_lim1 = late_leaderboard("2026-07", limit=1, department="TieDept")
    assert len(res_lim1["items"]) == 2
    assert all(it["rank"] == 1 for it in res_lim1["items"])

    # Clean up test data
    db.employees.delete_many({"emp_code": {"$in": ["EMP9911", "EMP9912", "EMP9913"]}})
    db.attendance_logs.delete_many({"emp_code": {"$in": ["EMP9911", "EMP9912", "EMP9913"]}})

    print("  [PASS] GET /analytics/leaderboard/late")


def test_department_trend():
    print("Testing GET /analytics/departments/{department}/trend...")
    # 1. 404 for unknown department
    try:
        department_trend("NonExistent", from_date="2026-07-01", to_date="2026-07-07")
        assert False, "Should raise 404 for unknown department"
    except HTTPException as e:
        assert e.status_code == 404

    # 2. 422 if to < from
    try:
        department_trend("Engineering", from_date="2026-07-10", to_date="2026-07-01")
        assert False, "Should raise 422 if to < from"
    except HTTPException as e:
        assert e.status_code == 422

    # 3. 422 if range > 92 days
    try:
        department_trend("Engineering", from_date="2026-01-01", to_date="2026-05-01")
        assert False, "Should raise 422 if range > 92 days"
    except HTTPException as e:
        assert e.status_code == 422

    # 4. Valid trend query from 2026-07-01 to 2026-07-07 (7 calendar days)
    res = department_trend("Engineering", from_date="2026-07-01", to_date="2026-07-07")
    assert res["department"] == "Engineering"
    items = res["items"]
    assert len(items) == 7, f"Expected exactly 7 daily rows, got {len(items)}"

    # July 1, 2026 is Wednesday -> working day
    assert items[0]["date"] == "2026-07-01"
    assert items[0]["is_working_day"] is True

    # July 4, 2026 is Saturday -> weekend
    sat = next(it for it in items if it["date"] == "2026-07-04")
    assert sat["is_working_day"] is False
    assert sat["attendance_rate"] is None, "Weekend attendance_rate must be null"

    # July 6, 2026 (Mon) in sample seed has 1 log for Engineering
    mon = next(it for it in items if it["date"] == "2026-07-06")
    assert mon["is_working_day"] is True
    assert mon["present_count"] == 1.0
    assert mon["attendance_rate"] == 1.0

    print("  [PASS] GET /analytics/departments/{department}/trend")


if __name__ == "__main__":
    setup_db()
    test_employee_monthly_summary()
    test_department_summary()
    test_late_leaderboard()
    test_department_trend()
    print("\nALL TEST 3 SUITE TESTS PASSED SUCCESSFULLY!")
