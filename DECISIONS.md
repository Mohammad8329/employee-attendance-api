# DECISIONS.md

### 1. Indexes
* `employees`: Unique index on `emp_code` prevents duplicate employee IDs and gives O(1) lookups. Compound index on `(department, joined_on)` speeds up department-filtered lookups and headcount checks.
* `attendance_logs`: Unique compound index on `(emp_code, date)` eliminates race-condition double punch-ins. Compound index `(date: -1, emp_code: 1)` supports sorted, paginated attendance listing without in-memory sorting. Compound `(date, late_minutes)` accelerates leaderboard aggregations.
* *Rejected:* Standalone index on `attendance_logs.punch_in`. A compound `(emp_code, punch_in: -1)` handles recent punch lookups per employee much better without adding unnecessary global write overhead.

### 2. Punch-in race
Both concurrent requests compute the same attendance date and attempt `insert_one`. MongoDB's unique compound index `(emp_code, date)` allows exactly one write into the storage engine (returning 201). The second write immediately triggers a `DuplicateKeyError`, which our `try/except` catches to return a clean 409 Conflict without checking first.

### 3. Ties
We use standard competition ranking via MongoDB's `$setWindowFields` with `$rank` (e.g. 1, 2, 2, 4). `limit` filters by `rank <= limit` *after* ranking. If two employees tie at the cutoff rank, both rows are returned to avoid arbitrarily cutting off tied employees, sorted by `total_late_minutes` desc and `emp_code` asc.

### 4. Headcount
The pipeline starts on the `employees` collection (filtering `joined_on <= end_date`), not `attendance_logs`. It joins logs using `$lookup`. Every employee matching the join date counts toward their department's `headcount`, even if their joined `logs` array is completely empty.

### 5. One thing you would change for 100x data
I would store pre-aggregated daily rollups at punch-out time instead of computing monthly metrics across millions of raw punch documents on every request, and shard `attendance_logs` on `{emp_code: "hashed", date: 1}` to distribute write load.
