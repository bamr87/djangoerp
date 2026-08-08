from rest_framework import permissions, viewsets

from .models import Invoice, Payment
from .serializers import InvoiceSerializer, PaymentSerializer


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.all().order_by('-created_at')
    serializer_class = InvoiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['status', 'customer', 'vendor', 'due_date']
    search_fields = ['invoice_number', 'customer', 'vendor']
    ordering_fields = ['due_date', 'created_at', 'total']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.all().order_by('-payment_date')
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['payment_method', 'payment_date', 'invoice']
    search_fields = ['payment_reference']
    ordering_fields = ['payment_date', 'amount']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
