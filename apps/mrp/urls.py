from rest_framework.routers import DefaultRouter

from .views import MRPRunViewSet, PlannedOrderViewSet

router = DefaultRouter()
router.register(r'runs', MRPRunViewSet)
router.register(r'planned-orders', PlannedOrderViewSet)

urlpatterns = router.urls
