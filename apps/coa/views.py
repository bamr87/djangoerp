from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Account, AccountType
from .serializers import AccountSerializer, AccountTreeSerializer, AccountTypeSerializer


class AccountTypeViewSet(viewsets.ModelViewSet):
    """
    API endpoint for account types (Asset, Liability, Equity, etc.)
    """
    queryset = AccountType.objects.all()
    serializer_class = AccountTypeSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'name']
    ordering_fields = ['code', 'name']
    ordering = ['code']


class AccountViewSet(viewsets.ModelViewSet):
    """
    API endpoint for chart of accounts
    """
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['account_type', 'is_active', 'parent_account']
    search_fields = ['code', 'name', 'description']
    ordering_fields = ['code', 'name', 'account_type__name']
    ordering = ['code']

    @action(detail=False, methods=['get'])
    def hierarchy(self, request):
        """
        Returns accounts in hierarchical structure
        """
        root_accounts = Account.objects.filter(parent_account=None).order_by('code')
        serializer = AccountTreeSerializer(root_accounts, many=True)
        return Response(serializer.data)
