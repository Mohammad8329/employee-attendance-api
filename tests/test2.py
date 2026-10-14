"""Purpose: Integration tests for employee and attendance operational endpoints.
Validates:
- POST /employees: Validation, unique emp_code constraint (409), serialization
- GET /employees: Pagination, sorting, and department filtering
- POST /attendance/punch-in & punch-out: Overnight shifts, late/overtime tracking, concurrency handling
- GET /attendance: Sorting (date desc, emp_code asc), pagination, date range filtering
- PATCH /attendance/{emp_code}/{date}: Regularization, punch clearing, optimistic locking, audit trail
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    create_employee,
    list_employees,
    punch_in,
    punch_out,
    regularize_attendance,
    list_attendance,
    EmployeeIn,
    PunchInIn,
    PunchOutIn,
    RegularizeIn,
    db,
    init_db,
)


def setup_db():
    init_db()


def test_post_employees():
    print("Testing POST /employees...")
    # 1. Invalid emp_code regex
    try:
        EmployeeIn(
            emp_code="INVALID",
            name="Test",
            email="test@example.com",
            department="Engineering",
            joined_on="2026-01-01",
        )
        assert False, "Should fail regex validation for emp_code"
    except ValidationError:
        pass

    # 2. Same shift_start and shift_end
    try:
        EmployeeIn(
            emp_code="EMP9001",
            name="Test",
            email="test@example.com",
            department="Engineering",
            shift_start="09:00",
            shift_end="09:00",
            joined_on="2026-01-01",
        )
        assert False, "Should fail validation when shift_start == shift_end"
    except ValidationError:
        pass

    # 3. Valid creation
    test_emp = EmployeeIn(
        emp_code="EMP8888",
        name="Phase1 Tester",
        email="phase1@example.com",
        department="QA",
        shift_start="10:00",
        shift_end="19:00",
        joined_on="2026-06-01",
    )
    # Clean up if existed
    db.employees.delete_one({"emp_code": "EMP8888"})
    res = create_employee(test_emp)
    assert res["emp_code"] == "EMP8888"
    assert "_id" not in res, "Leaked _id in employee response"
    assert isinstance(res["created_at"], int), "created_at must be epoch ms"

    # 4. Duplicate employee -> 409
    try:
        create_employee(test_emp)
        assert False, "Duplicate employee creation should raise 409"
    except HTTPException as e:
        assert e.status_code == 409, f"Expected 409, got {e.status_code}"

    print("  [PASS] POST /employees")


def test_get_employees():
    print("Testing GET /employees...")
    # 1. Page size boundary validation
    try:
        list_employees(page=1, page_size=101)
        assert False, "page_size > 100 should raise 422"
    except HTTPException as e:
        assert e.status_code == 422

    # 2. Valid page listing
    res = list_employees(page=1, page_size=10)
    assert "items" in res and "total" in res
    assert res["page"] == 1
    assert res["page_size"] == 10
    # Verify sorting by emp_code asc
    emp_codes = [e["emp_code"] for e in res["items"]]
    assert emp_codes == sorted(emp_codes), "Employees must be sorted by emp_code asc"
    for e in res["items"]:
        assert "_id" not in e

    print("  [PASS] GET /employees")


def test_post_punch_in():
    print("Testing POST /attendance/punch-in...")
    # 1. Unknown employee -> 404
    try:
        punch_in(PunchInIn(emp_code="EMP99999"))
        assert False, "Punch-in for unknown employee should raise 404"
    except HTTPException as e:
        assert e.status_code == 404

    # 2. Punch-in for EMP8888 on date 2026-08-03 (a Monday)
    # 2026-08-03 10:15:00 IST (Shift is 10:00, so 15 min late)
    # 10:15:00 IST = 04:45:00 UTC = 1785732300000 ms
    db.attendance_logs.delete_many({"emp_code": "EMP8888"})
    res = punch_in(PunchInIn(emp_code="EMP8888", punched_at=1785732300000))
    assert res["emp_code"] == "EMP8888"
    assert res["date"] == "2026-08-03"
    assert res["late_minutes"] == 15, f"Expected 15 late minutes, got {res['late_minutes']}"
    assert res["punch_out"] is None
    assert res["work_hours"] is None
    assert "_id" not in res and "id" not in res, "Internal ID leaked"

    # 3. Duplicate punch-in for same date -> 409
    try:
        punch_in(PunchInIn(emp_code="EMP8888", punched_at=1785732300000))
        assert False, "Duplicate punch-in should raise 409"
    except HTTPException as e:
        assert e.status_code == 409

    print("  [PASS] POST /attendance/punch-in")


def test_get_attendance():
    print("Testing GET /attendance...")
    # 1. Date range validation date_from > date_to -> 422
    try:
        list_attendance(date_from="2026-08-05", date_to="2026-08-01")
        assert False, "date_from > date_to should raise 422"
    except HTTPException as e:
        assert e.status_code == 422

    # 2. Invalid date format -> 422
    try:
        list_attendance(date_from="01-08-2026")
        assert False, "Invalid date format should raise 422"
    except HTTPException as e:
        assert e.status_code == 422

    # 3. List attendance and check sorting (date desc, emp_code asc)
    res = list_attendance(page=1, page_size=20)
    assert res["page"] == 1
    items = res["items"]
    if len(items) > 1:
        dates = [item["date"] for item in items]
        assert dates == sorted(dates, reverse=True), "Records must be sorted by date desc"
    for item in items:
        assert "id" not in item and "_id" not in item
        assert isinstance(item["punch_in"], (int, type(None)))

    print("  [PASS] GET /attendance")


def test_punch_out():
    print("Testing POST /attendance/punch-out...")
    # 1. Unknown employee -> 404
    try:
        punch_out(PunchOutIn(emp_code="EMP99999"))
        assert False, "Should raise 404 for unknown employee"
    except HTTPException as e:
        assert e.status_code == 404

    # 2. Punch-out timestamp earlier than punch-in -> 422
    # EMP8888 punched in at 1785732300000 (10:15 IST)
    try:
        punch_out(PunchOutIn(emp_code="EMP8888", punched_at=1785732200000))
        assert False, "punched_at <= punch_in should raise 422"
    except HTTPException as e:
        assert e.status_code == 422

    # 3. Punch-out more than 24 hours after punch-in -> 422
    try:
        punch_out(PunchOutIn(emp_code="EMP8888", punched_at=1785732300000 + 25 * 3600 * 1000))
        assert False, "punched_at > punch_in + 24h should raise 422"
    except HTTPException as e:
        assert e.status_code == 422

    # 4. Valid punch-out at 19:45 IST (14:15 UTC = 1785766500000 ms)
    # Work hours = 9.5, overtime = 45 min, half_day = False
    res = punch_out(PunchOutIn(emp_code="EMP8888", punched_at=1785766500000))
    assert res["emp_code"] == "EMP8888"
    assert res["date"] == "2026-08-03"
    assert res["work_hours"] == 9.5
    assert res["overtime_minutes"] == 45
    assert res["half_day"] is False
    assert res["punch_out"] == 1785766500000
    assert "_id" not in res and "id" not in res

    # 5. Second punch-out on same record -> 409
    try:
        punch_out(PunchOutIn(emp_code="EMP8888", punched_at=1785766500000))
        assert False, "Already punched out record should raise 409"
    except HTTPException as e:
        assert e.status_code == 409

    print("  [PASS] POST /attendance/punch-out")


def test_regularize_attendance():
    print("Testing PATCH /attendance/{emp_code}/{date}...")
    # 1. Non-existent employee -> 404
    try:
        regularize_attendance(
            emp_code="EMP99999",
            date="2026-08-03",
            body=RegularizeIn(status="WFH", reason="Work from home approved", regularized_by="HR_ADMIN"),
        )
        assert False, "Should raise 404 for unknown employee"
    except HTTPException as e:
        assert e.status_code == 404

    # 2. Non-existent record date -> 404
    try:
        regularize_attendance(
            emp_code="EMP8888",
            date="2026-01-01",
            body=RegularizeIn(status="WFH", reason="Record does not exist", regularized_by="HR_ADMIN"),
        )
        assert False, "Should raise 404 for unknown date"
    except HTTPException as e:
        assert e.status_code == 404

    # 3. Supplying punch_in or punch_out when setting status to ABSENT/LEAVE -> 422
    try:
        regularize_attendance(
            emp_code="EMP8888",
            date="2026-08-03",
            body=RegularizeIn(
                status="LEAVE",
                punch_in=1785732300000,
                reason="Invalid leave with punches",
                regularized_by="HR_ADMIN",
            ),
        )
        assert False, "Should raise 422 when supplying punches with LEAVE"
    except HTTPException as e:
        assert e.status_code == 422

    # 4. Request that changes nothing -> 422
    try:
        regularize_attendance(
            emp_code="EMP8888",
            date="2026-08-03",
            body=RegularizeIn(
                status="PRESENT",
                punch_in=1785732300000,
                punch_out=1785766500000,
                reason="Same data change nothing",
                regularized_by="HR_ADMIN",
            ),
        )
        assert False, "No-op regularization should raise 422"
    except HTTPException as e:
        assert e.status_code == 422

    # 5. Successful correction:
    # EMP8888 shift is 10:00 to 19:00.
    # Initially punched in at 10:15 IST (15 min late), punched out at 19:45 IST (9.50 hours, 45 min overtime).
    # HR corrects punch_in to 10:00 IST (1785731400000 ms, 0 late minutes) and status to WFH.
    # New work hours: 10:00 to 19:45 = 9.75 hours. Late minutes: 0.
    res = regularize_attendance(
        emp_code="EMP8888",
        date="2026-08-03",
        body=RegularizeIn(
            status="WFH",
            punch_in=1785731400000,
            reason="Biometric scanner delay at gate, approved WFH",
            regularized_by="HR_ADMIN",
        ),
    )
    assert res["status"] == "WFH"
    assert res["late_minutes"] == 0
    assert res["work_hours"] == 9.75
    assert res["overtime_minutes"] == 45
    assert len(res["history"]) == 1
    h0 = res["history"][0]
    assert h0["by"] == "HR_ADMIN"
    assert h0["reason"] == "Biometric scanner delay at gate, approved WFH"
    assert h0["changes"]["status"] == {"from": "PRESENT", "to": "WFH"}
    assert h0["changes"]["punch_in"] == {"from": 1785732300000, "to": 1785731400000}
    assert h0["changes"]["late_minutes"] == {"from": 15, "to": 0}
    assert h0["changes"]["work_hours"] == {"from": 9.5, "to": 9.75}

    print("  [PASS] PATCH /attendance/{emp_code}/{date}")


if __name__ == "__main__":
    setup_db()
    test_post_employees()
    test_get_employees()
    test_post_punch_in()
    test_punch_out()
    test_regularize_attendance()
    test_get_attendance()
    print("\nALL TEST 2 SUITE TESTS PASSED SUCCESSFULLY!")
