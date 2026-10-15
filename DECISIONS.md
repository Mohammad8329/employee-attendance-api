# DECISIONS

### 1. Indexes

employees: i put a unique index on emp_code so two employees can never end up with the same id, and it also makes lookups by code very fast. I also added a compound index on department and joined_on, which helps when we filter by department or count headcount.

attendance_logs: there is a unique compound index on emp_code and date, this is what stops double punch ins when requests come in at the same time. Another compound index on date (newest first) and emp_code lets the attendance list come back already sorted and paginated, so we never sort in memory. A third one on date and late_minutes helps the leaderboard aggregation.

Rejected: a standalone index on punch_in in attendance_logs. a compound index on emp_code and punch_in (newest first) does the job of finding recent punches per employee much better, and it avoids extra write cost that a global index on punch_in would add for no real gain.

### 2. Punch-in race

When two requests come in together they both work out the same attendance date and both try insert_one. The unique index on emp_code and date lets only one of them through, and that one gets a 201. The other one gets a DuplicateKeyError from mongodb straight away, and we catch it in a try and except and return a clean 409 Conflict. We do not do a find first, because checking and then inserting is exactly what causes the race.

### 3. Ties

We use standard competition ranking with the setWindowFields stage and the rank operator, so the ranks look like 1, 2, 2, 4. The limit is applied on the rank after ranking is done, not before. So if two employees are tied at the cutoff, both of them are returned instead of cutting one off at random. The rows are ordered by total_late_minutes highest first and then by emp_code A to Z.

### 4. Headcount

The pipeline starts from the employees collection and not from attendance_logs. It only keeps employees who joined on or before the end date, and then it joins the logs using lookup. Because of that, every employee who had joined by then is counted in the headcount of their department, even when their logs array comes back completely empty.

### 5. One thing i would change for 100x data

I would save a daily rollup at punch out time, so monthly numbers are not calculated from millions of raw punch documents on every single request. I would also shard attendance_logs on a hashed emp_code together with date, so the write load gets spread out across shards.