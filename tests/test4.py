"""Purpose: Validation tests for query execution plans and index usage.
Validates:
- GET /admin/explain/{endpoint} across all 5 key query endpoints
- executionStats presence in MongoDB query plan output
- Confirms index scan (IXSCAN) usage and absence of collection scans (COLLSCAN)
- Missing parameter handling (422)
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi import HTTPException

from app.main import (
    explain_endpoint,
    init_db,
)


def setup_db():
    init_db()


def has_stage(obj, stage_name: str) -> bool:
    """Recursively checks if a stage_name exists anywhere in the explain plan."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "stage" and v == stage_name:
                return True
            if has_stage(v, stage_name):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if has_stage(item, stage_name):
                return True
    return False


def has_execution_stats(exp: dict) -> bool:
    if "executionStats" in exp:
        return True
    for s in exp.get("stages", []):
        if "executionStats" in s.get("$cursor", {}):
            return True
    return False


def test_explain_attendance_list():
    print("Testing explain attendance_list...")
    res = explain_endpoint("attendance_list", page=1, page_size=20)
    assert res["endpoint"] == "attendance_list"
    assert res["collection"] == "attendance_logs"
    assert "explain" in res
    exp = res["explain"]
    assert "executionStats" in exp

    # Check for IXSCAN and absence of COLLSCAN
    assert has_stage(exp, "IXSCAN"), "attendance_list must use IXSCAN"
    assert not has_stage(exp, "COLLSCAN"), "attendance_list must not use COLLSCAN"
    print("  [PASS] explain attendance_list (IXSCAN verified, no COLLSCAN)")


def test_explain_employee_monthly():
    print("Testing explain employee_monthly...")
    # Missing parameters -> 422
    try:
        explain_endpoint("employee_monthly")
        assert False, "Should raise 422 when emp_code or month is missing"
    except HTTPException as e:
        assert e.status_code == 422

    res = explain_endpoint("employee_monthly", emp_code="EMP0001", month="2026-07")
    assert res["endpoint"] == "employee_monthly"
    assert res["collection"] == "attendance_logs"
    exp = res["explain"]
    assert "executionStats" in exp
    assert has_stage(exp, "IXSCAN"), "employee_monthly aggregation must use IXSCAN on (emp_code, date)"
    print("  [PASS] explain employee_monthly (IXSCAN verified)")


def test_explain_department_summary():
    print("Testing explain department_summary...")
    try:
        explain_endpoint("department_summary")
        assert False, "Should raise 422 when month is missing"
    except HTTPException as e:
        assert e.status_code == 422

    res = explain_endpoint("department_summary", month="2026-07")
    assert res["endpoint"] == "department_summary"
    assert res["collection"] == "employees"
    exp = res["explain"]
    assert has_execution_stats(exp)
    print("  [PASS] explain department_summary")


def test_explain_late_leaderboard():
    print("Testing explain late_leaderboard...")
    try:
        explain_endpoint("late_leaderboard")
        assert False, "Should raise 422 when month is missing"
    except HTTPException as e:
        assert e.status_code == 422

    res = explain_endpoint("late_leaderboard", month="2026-07")
    assert res["endpoint"] == "late_leaderboard"
    assert res["collection"] == "attendance_logs"
    exp = res["explain"]
    assert has_execution_stats(exp)
    assert has_stage(exp, "IXSCAN"), "late_leaderboard must use IXSCAN on (date, late_minutes)"
    print("  [PASS] explain late_leaderboard (IXSCAN verified)")


def test_explain_department_trend():
    print("Testing explain department_trend...")
    try:
        explain_endpoint("department_trend", department="Engineering")
        assert False, "Should raise 422 when from/to are missing"
    except HTTPException as e:
        assert e.status_code == 422

    res = explain_endpoint(
        "department_trend",
        department="Engineering",
        from_date="2026-07-01",
        to_date="2026-07-07",
    )
    assert res["endpoint"] == "department_trend"
    assert res["collection"] == "employees"
    exp = res["explain"]
    assert has_execution_stats(exp)
    print("  [PASS] explain department_trend")


if __name__ == "__main__":
    setup_db()
    test_explain_attendance_list()
    test_explain_employee_monthly()
    test_explain_department_summary()
    test_explain_late_leaderboard()
    test_explain_department_trend()
    print("\nALL TEST 4 SUITE TESTS PASSED SUCCESSFULLY!")
