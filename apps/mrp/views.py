from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsAccountant
from apps.core.models import DocumentSequence
from apps.manufacturing.serializers import WorkOrderSerializer
from apps.purchasing.serializers import PurchaseOrderSerializer

from . import services
from .engine import run_mrp
from .models import MRPRun, PlannedOrder
from .serializers import MRPRunSerializer, PlannedOrderSerializer


class MRPRunViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin,
                    mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    API endpoint for MRP runs. Creating a run dispatches the planning engine
    asynchronously (same pattern as saved reports); runs are snapshots and are
    never edited, only re-created.
    """
    queryset = MRPRun.objects.select_related('warehouse')
    serializer_class = MRPRunSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'warehouse']
    ordering_fields = ['created_at', 'as_of_date']

    def perform_create(self, serializer):
        run = serializer.save(
            created_by=self.request.user,
            number=DocumentSequence.next_number('MRP'),
            as_of_date=serializer.validated_data.get('as_of_date') or timezone.now().date(),
        )
        run_mrp.delay(run.id)

    @action(detail=True, methods=['post'])
    def convert(self, request, pk=None):
        """
        Convert planned orders into draft purchase orders (grouped per supplier)
        and work orders. Optional body: {"planned_order_ids": [1, 2, ...]} to
        convert a subset.
        """
        result = services.convert_run(
            self.get_object(), user=request.user,
            planned_order_ids=request.data.get('planned_order_ids'),
        )
        return Response({
            'purchase_orders': PurchaseOrderSerializer(result['purchase_orders'], many=True).data,
            'work_orders': WorkOrderSerializer(result['work_orders'], many=True).data,
        })


class PlannedOrderViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for planned orders (engine output; read-only except cancel)
    """
    queryset = PlannedOrder.objects.select_related('product', 'run')
    serializer_class = PlannedOrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['run', 'product', 'order_type', 'status', 'expedited']
    ordering_fields = ['due_date', 'release_date']

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        planned = services.cancel_planned_order(self.get_object(), user=request.user)
        return Response(self.get_serializer(planned).data)
