from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsAccountant

from . import services
from .models import BillOfMaterials, WorkOrder
from .serializers import BillOfMaterialsSerializer, WorkOrderSerializer


class BillOfMaterialsViewSet(viewsets.ModelViewSet):
    """
    API endpoint for bills of materials
    """
    queryset = BillOfMaterials.objects.select_related('product').prefetch_related('lines__component')
    serializer_class = BillOfMaterialsSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['product', 'is_active']
    search_fields = ['reference', 'product__sku', 'product__name']
    ordering_fields = ['product__sku', 'created_at']


class WorkOrderViewSet(viewsets.ModelViewSet):
    """
    API endpoint for work orders. State transitions are actions (confirm, start,
    complete, cancel) backed by manufacturing.services.
    """
    queryset = WorkOrder.objects.select_related('product', 'bom', 'warehouse')
    serializer_class = WorkOrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'product', 'warehouse']
    search_fields = ['number', 'source_reference', 'product__sku']
    ordering_fields = ['number', 'due_date', 'created_at']

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        work_order = services.confirm_work_order(self.get_object(), user=request.user)
        return Response(self.get_serializer(work_order).data)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        work_order = services.start_work_order(self.get_object(), user=request.user)
        return Response(self.get_serializer(work_order).data)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """
        Consume components, receive the finished good at rolled-up cost, and post
        the production journal entry. Optional body: {"completion_date": "YYYY-MM-DD"}.
        """
        result = services.complete_work_order(
            self.get_object(), user=request.user, completion_date=request.data.get('completion_date'),
        )
        return Response({
            'work_order': self.get_serializer(result['work_order']).data,
            'journal_entry': result['journal_entry'].entry_number,
            'issue_moves': [move.reference for move in result['issue_moves']],
            'receipt_move': result['receipt_move'].reference,
        })

    @action(detail=True, methods=['get'])
    def availability(self, request, pk=None):
        """Preview component availability for completing this work order."""
        return Response(services.component_availability(self.get_object()))

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        work_order = services.cancel_work_order(self.get_object(), user=request.user)
        return Response(self.get_serializer(work_order).data)
