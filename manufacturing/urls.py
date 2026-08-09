from rest_framework.routers import DefaultRouter

from .views import BillOfMaterialsViewSet, WorkOrderViewSet

router = DefaultRouter()
router.register(r'boms', BillOfMaterialsViewSet)
router.register(r'work-orders', WorkOrderViewSet)

urlpatterns = router.urls
