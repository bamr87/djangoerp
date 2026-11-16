from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets

from .models import Company
from .serializers import CompanySerializer


class CompanyViewSet(viewsets.ModelViewSet):
    """
    API endpoint for the legal entity/entities this installation keeps books for
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = [permissions.IsAuthenticated]
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


def health(request):
    """Liveness probe for CI smoke tests and deployment healthchecks."""
    return JsonResponse({
        'status': 'ok',
        'app': 'djangoerp',
        'version': settings.APP_VERSION,
        'debug': settings.DEBUG,
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
