from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAccountant

from . import services
from .models import PurchaseOrder
from .serializers import PurchaseOrderSerializer


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """
    API endpoint for purchase orders. State transitions are actions (confirm,
    receive, cancel) backed by purchasing.services, never direct status writes.
    """
    queryset = PurchaseOrder.objects.select_related('supplier', 'warehouse').prefetch_related('lines__product')
    serializer_class = PurchaseOrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'supplier', 'warehouse', 'order_date']
    search_fields = ['number', 'notes', 'source_reference']
    ordering_fields = ['number', 'order_date', 'expected_date']

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        order = services.confirm_purchase_order(self.get_object(), user=request.user)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        """
        Receive goods. Body: {} to receive everything open, or
        {"receipts": {"<line_id>": "<quantity>", ...}} for a partial receipt,
        plus optional "receipt_date" (YYYY-MM-DD).
        """
        result = services.receive_purchase_order(
            self.get_object(),
            user=request.user,
            receipts=request.data.get('receipts'),
            receipt_date=request.data.get('receipt_date'),
        )
        return Response({
            'grn_number': result['grn_number'],
            'journal_entry': result['journal_entry'].entry_number,
            'moves': [move.reference for move in result['moves']],
            'order': self.get_serializer(result['order']).data,
        })

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        order = services.cancel_purchase_order(self.get_object(), user=request.user)
        return Response(self.get_serializer(order).data)
