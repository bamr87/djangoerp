"""
Browser-facing routes for the company app.

Kept separate from urls.py (the DRF router, mounted under /api/company/) because
these are mounted at the site root — the API surface and the public face of the
installation have different prefixes and different renderers.
"""
from django.urls import path

from . import views

app_name = 'company'

urlpatterns = [
    path('', views.home, name='home'),
    # No trailing slash: the fleet contract (hub UPS-BE-10) names the probes GET /healthz and GET /readyz.
    path('healthz', views.healthz, name='healthz'),
    path('readyz', views.readyz, name='readyz'),
    # The original liveness probe, kept for existing callers.
    path('health/', views.health, name='health'),
]
