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


def home(request):
    """Public landing page: shows the company charter once one is recorded."""
    company = Company.objects.filter(is_active=True).first()
    return render(request, 'company/home.html', {'company': company})


def health(request):
    """Liveness probe for CI smoke tests and deployment healthchecks."""
    return JsonResponse({
        'status': 'ok',
        'app': 'djangoerp',
        'version': settings.APP_VERSION,
        'debug': settings.DEBUG,
    })
