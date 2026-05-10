from django.urls import path
from Users.views import ChangePasswordView, LoginView, MeView, ProfileView

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("me/", MeView.as_view(), name="auth-me"),
    path("me/profile/", ProfileView.as_view(), name="auth-me-profile"),
    path("me/password/", ChangePasswordView.as_view(), name="auth-me-password"),
]
