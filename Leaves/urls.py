from django.urls import path

from Leaves.views import (
    LeaveApplicationView,
    LeaveBalanceView,
    LeaveTypeView,
    leave_application_approve,
    leave_application_reject,
    leave_dashboard_summary,
)

urlpatterns = [
    path("summary/", leave_dashboard_summary, name="leaves-summary"),
    path("types/", LeaveTypeView.as_view(), name="leave-type-list-create"),
    path("types/<int:pk>/", LeaveTypeView.as_view(), name="leave-type-detail"),
    path("balances/", LeaveBalanceView.as_view(), name="leave-balance-list-upsert"),
    path("applications/", LeaveApplicationView.as_view(), name="leave-application-list-create"),
    path("applications/<int:pk>/", LeaveApplicationView.as_view(), name="leave-application-detail"),
    path("applications/<int:pk>/approve/", leave_application_approve, name="leave-application-approve"),
    path("applications/<int:pk>/reject/", leave_application_reject, name="leave-application-reject"),
]
