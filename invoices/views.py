from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAccountantOrAuditorReadOnly
from core.viewsets import ProtectedHistoryDestroyMixin

from . import services
from .models import Invoice, Payment
from .serializers import InvoiceSerializer, PaymentSerializer


class InvoiceViewSet(ProtectedHistoryDestroyMixin, viewsets.ModelViewSet):
    queryset = Invoice.objects.select_related(
        'partner', 'sales_order', 'journal_entry'
    ).prefetch_related('line_items').order_by('-created_at')
    serializer_class = InvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountantOrAuditorReadOnly]
    filterset_fields = ['status', 'customer', 'vendor', 'due_date', 'partner', 'sales_order']
    search_fields = ['invoice_number', 'customer', 'vendor']
    ordering_fields = ['due_date', 'created_at', 'total']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def check_deletable(self, instance):
        # Only an unposted draft: a posted invoice (and the payments posted against it) is ledger history.
        if instance.journal_entry_id:
            self.refuse_delete(instance, f'it is posted ({instance.journal_entry})')
        super().check_deletable(instance)

    # Named post_to_ledger (not `post`) so the action cannot shadow the HTTP verb
    # handler on non-action routes; the URL stays /invoices/{id}/post/.
    @action(detail=True, methods=['post'], url_path='post', url_name='post')
    def post_to_ledger(self, request, pk=None):
        """
        Post the invoice to the general ledger (Dr partner receivable, Cr revenue).
        Optional body: {"posting_date": "YYYY-MM-DD"}.
        """
        invoice = services.post_invoice(
            self.get_object(), user=request.user, posting_date=request.data.get('posting_date'),
        )
        return Response(self.get_serializer(invoice).data)


class PaymentViewSet(ProtectedHistoryDestroyMixin, viewsets.ModelViewSet):
    queryset = Payment.objects.select_related('invoice', 'journal_entry').order_by('-payment_date')
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountantOrAuditorReadOnly]
    filterset_fields = ['payment_method', 'payment_date', 'invoice']
    search_fields = ['payment_reference']
    ordering_fields = ['payment_date', 'amount']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def check_deletable(self, instance):
        # A payment has no status: it is history once it has posted.
        if instance.journal_entry_id:
            self.refuse_delete(instance, f'it is posted ({instance.journal_entry})')

    @action(detail=True, methods=['post'], url_path='post', url_name='post')
    def post_to_ledger(self, request, pk=None):
        """
        Post the payment to the general ledger (Dr deposit account, Cr partner receivable).
        """
        payment = services.post_payment(self.get_object(), user=request.user)
        return Response(self.get_serializer(payment).data)
