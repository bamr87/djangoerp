from django.urls import path, re_path

from .views import shell

app_name = 'workspace'

urlpatterns = [
    path('', shell, name='shell'),
    # Deep links: the client router owns everything under /app/.
    re_path(r'^(?P<path>.+)$', shell, name='shell-path'),
]
