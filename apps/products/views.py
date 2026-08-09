from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets

from .models import Product, ProductCategory, UnitOfMeasure
from .serializers import ProductCategorySerializer, ProductSerializer, UnitOfMeasureSerializer


class UnitOfMeasureViewSet(viewsets.ModelViewSet):
    """
    API endpoint for units of measure
    """
    queryset = UnitOfMeasure.objects.all()
    serializer_class = UnitOfMeasureSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'name']
    ordering = ['code']


class ProductCategoryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for product categories and their GL posting defaults
    """
    queryset = ProductCategory.objects.select_related('inventory_account', 'revenue_account', 'cogs_account')
    serializer_class = ProductCategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name']
    ordering = ['name']


class ProductViewSet(viewsets.ModelViewSet):
    """
    API endpoint for the item master
    """
    queryset = Product.objects.select_related('category', 'uom', 'default_supplier')
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'product_type', 'procurement_type', 'is_active', 'default_supplier']
    search_fields = ['sku', 'name', 'description']
    ordering_fields = ['sku', 'name', 'created_at']
    ordering = ['sku']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
