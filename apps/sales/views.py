from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsAccountant
from apps.invoices.serializers import InvoiceSerializer

from . import services
from .models import SalesOrder
from .serializers import SalesOrderSerializer


class SalesOrderViewSet(viewsets.ModelViewSet):
    """
    API endpoint for sales orders. State transitions are actions (confirm, ship,
    invoice, cancel) backed by sales.services, never direct status writes.
    """
    queryset = SalesOrder.objects.select_related('customer', 'warehouse').prefetch_related('lines__product')
    serializer_class = SalesOrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'customer', 'warehouse', 'order_date']
    search_fields = ['number', 'notes']
    ordering_fields = ['number', 'order_date', 'requested_date']

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        order = services.confirm_sales_order(self.get_object(), user=request.user)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['post'])
    def ship(self, request, pk=None):
        """
        Ship goods. Body: {} to ship everything open, or
        {"shipments": {"<line_id>": "<quantity>", ...}} for a partial shipment,
        plus optional "ship_date" (YYYY-MM-DD).
        """
        result = services.ship_sales_order(
            self.get_object(),
            user=request.user,
            shipments=request.data.get('shipments'),
            ship_date=request.data.get('ship_date'),
        )
        return Response({
            'shipment_number': result['shipment_number'],
            'journal_entry': result['journal_entry'].entry_number,
            'moves': [move.reference for move in result['moves']],
            'order': self.get_serializer(result['order']).data,
        })

    @action(detail=True, methods=['post'])
    def invoice(self, request, pk=None):
        invoice = services.create_invoice_from_order(
            self.get_object(), user=request.user, due_date=request.data.get('due_date'),
        )
        return Response(InvoiceSerializer(invoice).data, status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        order = services.cancel_sales_order(self.get_object(), user=request.user)
        return Response(self.get_serializer(order).data)
