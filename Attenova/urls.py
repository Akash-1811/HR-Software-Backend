"""
URL configuration for Attenova project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

from Organization.views import OfficeView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("Users.urls")),
    path("api/organizations/", include("Organization.urls")),
    path("api/offices/", OfficeView.as_view(), name="office-list-create"),
    path("api/offices/<int:pk>/", OfficeView.as_view(), name="office-detail"),
    path("api/employees/", include("Employee.urls")),
    path("api/shifts/", include("Shifts.urls")),
    path("api/biometric/", include("Biometric.urls")),
    path("api/attendance/", include("Attendance.urls")),
    path("api/dashboard/", include("dashboard.urls")),
    path("api/reports/", include("Reports.urls")),
    path("api/contact/", include("marketing.urls")),
    path("api/notifications/", include("Notifications.urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
