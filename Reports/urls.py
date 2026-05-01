from django.urls import path

from Reports.views import AttendanceReport, RegularizationReport, SendAttendanceEmailReport

urlpatterns = [
    path("attendance/", AttendanceReport.as_view(), name="report-attendance"),
    path("regularization/", RegularizationReport.as_view(), name="report-regularization"),
    path("attendance/send-email/", SendAttendanceEmailReport.as_view(), name="report-send-email"),
]
