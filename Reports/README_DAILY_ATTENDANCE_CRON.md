# Daily Attendance Report Cronjob

The daily attendance report is emailed to managers, supervisors, and office admins at **1 AM every day**.

## What it does

- Runs at 1 AM (server timezone from `TIME_ZONE` in settings)
- For each office: fetches attendance data from biometric logs for **yesterday**
- Generates an **Excel file** with parent-only columns:
  - Employee Code, Employee Name, Device ID, Log Date, First In, Last Out, Hours Worked, Punches
- Emails the report to:
  - **Office Admins** (role: OFFICE_ADMIN, office_id = that office)
  - **Supervisors** (role: SUPERVISOR, office_id = that office)
  - **Office Managers** (linked via Office.managers M2M)

## Email format

The email is professional and branded, similar to the QuickSync reference:
- Dark header banner with "Attenova"
- Clear headline: "Your daily attendance report is ready"
- Body text with office name and report date
- Footer with copyright and support info

## Setup

### 1. Email configuration

Set these environment variables (e.g. in `.env`):

```
# For production (SMTP)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@example.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL="Attenova <noreply@yourdomain.com>"

# For development (logs to console)
# EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833).

### 2. Add cron job to system crontab

```bash
python manage.py crontab add
```

This registers the job to run at 1 AM daily. Check with:

```bash
python manage.py crontab show
```

To remove:

```bash
python manage.py crontab remove
```

### 3. Manual run (optional)

```bash
# Run for yesterday
python manage.py send_daily_attendance_report

# Run for a specific date
python manage.py send_daily_attendance_report --date 2026-03-05

# Dry run (no emails sent)
python manage.py send_daily_attendance_report --dry-run

# Single office
python manage.py send_daily_attendance_report --office 1
```

## Notes

- The report uses **DummyEsslBiometricAttendanceData** (same as the Reports API).
- Offices with no recipients (no managers/supervisors/admins) are skipped.
- Offices with no attendance data for the date still receive an email with an empty sheet.
