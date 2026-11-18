import logging

import redis
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets

from accounts.permissions import IsAccountantOrReadOnly

from .models import Company
from .serializers import CompanySerializer

logger = logging.getLogger(__name__)

# Seconds the readiness probe waits for the broker: docker/healthcheck-web.sh gives /readyz 5 in total.
BROKER_TIMEOUT = 2


class CompanyViewSet(viewsets.ModelViewSet):
    """
    API endpoint for the legal entity/entities this installation keeps books for
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountantOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'currency_code']
    search_fields = ['name', 'legal_name', 'email', 'tax_id']
    ordering_fields = ['name', 'founded_on', 'created_at']
    ordering = ['name']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# The API areas the landing page lists, in djangoerp/urls.py order, as (label, prefix): each is served under
# /api/<prefix>/, and drf-yasg tags that area's operations <prefix>, so /swagger/#/<prefix> opens its section.
API_AREAS = (
    ('Authentication (JWT)', 'auth'),
    ('Chart of accounts', 'coa'),
    ('Journal (double-entry ledger)', 'journal'),
    ('Invoices & payments', 'invoices'),
    ('Reports', 'reports'),
    ('Partners', 'partners'),
    ('Products', 'products'),
    ('Inventory', 'inventory'),
    ('Purchasing', 'purchasing'),
    ('Sales', 'sales'),
    ('Manufacturing', 'manufacturing'),
    ('MRP', 'mrp'),
    ('Company', 'company'),
)


def home(request):
    """Public landing page: shows the company charter once one is recorded."""
    company = Company.objects.filter(is_active=True).first()
    return render(request, 'company/home.html', {
        'company': company,
        'page_title': company.name if company else 'Get started',
        'api_areas': API_AREAS,
    })


@never_cache
def healthz(request):
    """Liveness probe (hub UPS-BE-10): 200 `{"status": "ok"}` from a live process; touches no dependency."""
    return JsonResponse({'status': 'ok'})


@never_cache
def readyz(request):
    """
    Readiness probe (hub UPS-BE-10): 200 when the database and the Celery broker answer, else 503 `"degraded"`.

    The body is `{"status": "ok" | "degraded", "checks": {"db": …, "broker": …}}`. `db` runs `SELECT 1`; `broker`
    pings Redis, and reads `skipped` while CELERY_TASK_ALWAYS_EAGER runs tasks in-process without one. A failed
    check reads `error`; the reason goes to the log, not to this public response.
    """
    checks = {'db': _check_db(), 'broker': _check_broker()}
    ready = 'error' not in checks.values()
    return JsonResponse({'status': 'ok' if ready else 'degraded', 'checks': checks}, status=200 if ready else 503)


def _check_db():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        logger.warning('readyz: database check failed', exc_info=True)
        return 'error'
    return 'ok'


def _check_broker():
    if settings.CELERY_TASK_ALWAYS_EAGER:
        return 'skipped'
    try:
        with redis.Redis.from_url(
            settings.CELERY_BROKER_URL, socket_connect_timeout=BROKER_TIMEOUT, socket_timeout=BROKER_TIMEOUT
        ) as client:
            client.ping()
    except (redis.RedisError, ValueError):  # ValueError: CELERY_BROKER_URL is not a redis:// URL
        logger.warning('readyz: broker check failed', exc_info=True)
        return 'error'
    return 'ok'


def health(request):
    """Liveness probe for CI smoke tests and deployment healthchecks."""
    return JsonResponse({
        'status': 'ok',
        'app': 'djangoerp',
        'version': settings.APP_VERSION,
    })


def version(request):
    """
    Build identity: the release version and the commit and build time of the running image.

    The hub's UPS-BE-11 contract, exactly {name, version, commit, built_at}. `commit` and `built_at` are null
    unless the image was built with GIT_SHA / BUILT_AT (see the Dockerfile).
    """
    return JsonResponse({
        'name': 'djangoerp',
        'version': settings.APP_VERSION,
        'commit': settings.GIT_SHA,
        'built_at': settings.BUILT_AT,
    })
