from django.urls import path

from dashboard.views import AttentionReportView, DashboardHomeView

urlpatterns = [
    path("summary/", DashboardHomeView.as_view(), name="dashboard-summary"),
    path("attention/", AttentionReportView.as_view(), name="dashboard-attention-report"),
]
