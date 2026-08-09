from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsAccountant
from apps.core.models import DocumentSequence

from . import services
from .models import StockLevel, StockMove, Warehouse
from .serializers import StockLevelSerializer, StockMoveSerializer, WarehouseSerializer


class WarehouseViewSet(viewsets.ModelViewSet):
    """
    API endpoint for warehouses
    """
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active']
    search_fields = ['code', 'name']
    ordering = ['code']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class StockMoveViewSet(viewsets.ModelViewSet):
    """
    API endpoint for the stock ledger. Creates drafts (mainly manual
    adjustments); business documents create their own moves through services.
    """
    queryset = StockMove.objects.select_related('product', 'warehouse')
    serializer_class = StockMoveSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['product', 'warehouse', 'move_type', 'status', 'move_date']
    search_fields = ['reference', 'source_reference']
    ordering_fields = ['move_date', 'reference', 'created_at']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, reference=DocumentSequence.next_number('SM'))

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        move = services.complete_move(self.get_object(), user=request.user)
        return Response(self.get_serializer(move).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        move = services.cancel_move(self.get_object(), user=request.user)
        return Response(self.get_serializer(move).data)


class StockLevelViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for cached on-hand quantities (read-only; derived from moves)
    """
    queryset = StockLevel.objects.select_related('product', 'warehouse')
    serializer_class = StockLevelSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['product', 'warehouse']
    ordering_fields = ['quantity_on_hand']
