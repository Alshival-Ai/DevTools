from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/alshival/', RedirectView.as_view(url='/settings/?tab=admin', permanent=False)),
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('', include('dashboard.urls')),
]
