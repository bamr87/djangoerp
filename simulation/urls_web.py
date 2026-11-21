from django.urls import path

from .views import console

app_name = 'simulation'

urlpatterns = [
    path('', console, name='console'),
]
