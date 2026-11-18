from django.contrib.admin import AdminSite

from .forms import StaffAuthenticationForm


class RequestHubAdminSite(AdminSite):
    login_form = StaffAuthenticationForm
