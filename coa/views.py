from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import Account, AccountType
from .serializers import AccountSerializer, AccountTreeSerializer, AccountTypeSerializer


class AccountTypeViewSet(viewsets.ModelViewSet):
    """
    API endpoint for account types (Asset, Liability, Equity, etc.)

    The seeded AS/LI/EQ/RE/EX types cannot be deleted or recoded: the report
    generators hard-code them.
    """
    queryset = AccountType.objects.all()
    serializer_class = AccountTypeSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'name']
    ordering_fields = ['code', 'name']
    ordering = ['code']

    def perform_destroy(self, instance):
        if instance.code in AccountType.SEEDED_CODES:
            raise ValidationError(
                f'{instance.code} is a seeded account type the reports depend on; it cannot be deleted.'
            )
        instance.delete()


class AccountViewSet(viewsets.ModelViewSet):
    """
    API endpoint for chart of accounts

    An account that has journal lines cannot be deleted (400): retire it with
    is_active=false. The reports keep listing a retired account's posted balance.
    """
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['account_type', 'is_active', 'parent_account']
    search_fields = ['code', 'name', 'description']
    ordering_fields = ['code', 'name', 'account_type__name']
    ordering = ['code']

    def perform_destroy(self, instance):
        # JournalLine.account is PROTECT, so the database would refuse anyway (a 409 from
        # core.exceptions); checking first gives the caller the way out instead.
        if instance.journalline_set.exists():
            raise ValidationError(
                f'Account {instance.code} has ledger lines and cannot be deleted; set is_active=false to retire it.'
            )
        instance.delete()

    @action(detail=False, methods=['get'])
    def hierarchy(self, request):
        """
        Returns accounts in hierarchical structure
        """
        root_accounts = Account.objects.filter(parent_account=None).order_by('code')
        serializer = AccountTreeSerializer(root_accounts, many=True)
        return Response(serializer.data)
