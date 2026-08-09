from rest_framework.routers import DefaultRouter

from .views import StockLevelViewSet, StockMoveViewSet, WarehouseViewSet

router = DefaultRouter()
router.register(r'warehouses', WarehouseViewSet)
router.register(r'stock-moves', StockMoveViewSet)
router.register(r'stock-levels', StockLevelViewSet)

urlpatterns = router.urls
