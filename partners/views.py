from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets

from .models import BusinessPartner
from .serializers import BusinessPartnerSerializer


class BusinessPartnerViewSet(viewsets.ModelViewSet):
    """
    API endpoint for business partners (customers and suppliers)
    """
    queryset = BusinessPartner.objects.select_related('receivable_account', 'payable_account')
    serializer_class = BusinessPartnerSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_customer', 'is_supplier', 'is_active']
    search_fields = ['code', 'name', 'email', 'tax_id']
    ordering_fields = ['code', 'name', 'created_at']
    ordering = ['code']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
