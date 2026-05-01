# Attenova — Flow and ER diagrams

This page contains **Mermaid** source for the software flow and the database ER view. You can preview it in GitHub, VS Code / Cursor (Mermaid preview), or export images (see [Download as PNG or SVG](diagrams/README.md)).

---

## 1. Software flow diagram

High-level paths: SPA → JSON APIs → primary DB; cron → ESSL (optional) → staging table → attendance processor; daily report → SMTP.

```mermaid
flowchart TB
  subgraph clients [Clients]
    SPA[Web_or_mobile_SPA]
  end

  subgraph api [Django_JSON_API]
    AUTH["/api/auth JWT_Bearer"]
    ORG["/api/organizations /api/offices"]
    EMP["/api/employees"]
    SH["/api/shifts"]
    BIO["/api/biometric devices + essl-logs"]
    ATT["/api/attendance"]
    REP["/api/reports"]
    NOTIF["/api/notifications"]
  end

  subgraph cron [django-crontab]
    C1["every_5_min run_attendance_sync"]
    C2["daily_1am send_daily_attendance_report"]
  end

  subgraph pipeline [Attendance_pipeline]
    ESSL[(ESSL_MySQL_optional)]
    DUMMY[DummyEsslBiometricAttendanceData]
    PROC[BiometricAttendanceProcessor]
    ATTREC[Attendance + AttendancePunch]
  end

  subgraph data [Primary_database]
    DB[(SQLite_PostgreSQL_or_MySQL)]
  end

  subgraph mail [Email]
    SMTP[SMTP]
  end

  SPA --> AUTH
  SPA --> ORG
  SPA --> EMP
  SPA --> SH
  SPA --> BIO
  SPA --> ATT
  SPA --> REP
  SPA --> NOTIF

  AUTH --> DB
  ORG --> DB
  EMP --> DB
  SH --> DB
  BIO --> DB
  ATT --> DB
  REP --> DB
  NOTIF --> DB

  C1 --> ESSL
  ESSL --> DUMMY
  C1 --> DUMMY
  DUMMY --> PROC
  PROC --> ATTREC
  ATTREC --> DB

  C2 --> REP
  REP --> SMTP

  BIO -.->|read_only_pymysql| ESSL
```

---

## 2. ER diagram (core entities)

Relationships reflect Django models: multi-tenant org/office, users, employees, shifts, attendance, punches, regularization, biometric staging, notifications. `BiometricLog` and `DummyEsslBiometricAttendanceData` are not FK-linked to `Employee` in the schema; matching is by `emp_code` / `UserId` at processing time.

```mermaid
erDiagram
  Organization ||--o{ Office : has
  Organization ||--o{ User : users
  Organization ||--o{ Employee : employees

  Office ||--o{ Shift : defines
  Office ||--o{ Employee : employs
  Office ||--o{ BiometricDevice : devices
  Office ||--o{ Attendance : attendances
  Office ||--o{ AttendanceRun : runs

  User }o--|| Organization : organization_fk
  User }o--o| Office : office_fk_optional

  Office }o--o{ User : managers_m2m

  Shift ||--o{ Employee : assigned
  Shift ||--o{ Attendance : used

  Employee }o--|| Organization : belongs
  Employee }o--|| Office : works_at
  Employee }o--o| Shift : shift_fk
  Employee }o--o| User : linked_user

  Employee ||--o{ Attendance : daily_records
  Attendance ||--o{ AttendancePunch : punches
  Attendance ||--o{ AttendanceRegularization : requests

  AttendanceRegularization }o--|| User : requested_by
  AttendanceRegularization }o--o| User : reviewed_by

  Notification }o--|| User : recipient
  Notification }o--o| User : created_by

  Organization {
    int id PK
    string name
    bool is_active
  }

  Office {
    int id PK
    int organization_id FK
    string name
    int num_biometric_devices
  }

  User {
    int id PK
    string email UK
    string role
    int organization_id FK
    int office_id FK
  }

  Shift {
    int id PK
    int office_id FK
    string name
    time start_time
    time end_time
    int grace_minutes
    bool is_default
  }

  Employee {
    int id PK
    int organization_id FK
    int office_id FK
    int shift_id FK
    int user_id FK
    string emp_code
    string name
    bool is_active
  }

  Attendance {
    int id PK
    int employee_id FK
    int office_id FK
    int shift_id FK
    date date
    string status
    string source
  }

  AttendancePunch {
    int id PK
    int attendance_id FK
    datetime punch_time
    string direction
  }

  AttendanceRegularization {
    int id PK
    int attendance_id FK
    int employee_id FK
    int requested_by_id FK
    int reviewed_by_id FK
    string status
  }

  AttendanceRun {
    int id PK
    int office_id FK
    datetime from_datetime
    datetime to_datetime
    string status
  }

  BiometricDevice {
    int id PK
    int office_id FK
    string device_id
  }

  BiometricLog {
    int id PK
    string emp_code
    datetime punch_time
    string device_id
  }

  DummyEsslBiometricAttendanceData {
    bigint DeviceLogId PK
    string UserId
    datetime LogDate
    string Direction
    string DeviceId
  }

  Notification {
    int id PK
    int recipient_id FK
    int created_by_id FK
    string notification_type
  }
```

---

## 3. Attendance pipeline (sequence)

Cron and optional ESSL sync into the app database, then processing into `Attendance`.

```mermaid
sequenceDiagram
  participant Cron as django_crontab
  participant ESSL as ESSL_MySQL
  participant Dummy as DummyEssl_staging
  participant Proc as BiometricAttendanceProcessor
  participant Att as Attendance_tables

  Cron->>ESSL: pull_new_DeviceLog_rows_if_configured
  ESSL-->>Dummy: INSERT_ignore_duplicates
  Cron->>Dummy: read
  Dummy->>Proc: group_by_emp_and_date
  Proc->>Att: upsert_Attendance_and_punches
```

---

## Source files

| File | Purpose |
|------|---------|
| [`diagrams/attenova_software_flow.mmd`](diagrams/attenova_software_flow.mmd) | Flow diagram (Mermaid only) |
| [`diagrams/attenova_er.mmd`](diagrams/attenova_er.mmd) | ER diagram (Mermaid only) |

See [diagrams/README.md](diagrams/README.md) for **exporting PNG or SVG** to download.
