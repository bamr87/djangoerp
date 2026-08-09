from rest_framework.routers import DefaultRouter

from .views import SalesOrderViewSet

router = DefaultRouter()
router.register(r'orders', SalesOrderViewSet)

urlpatterns = router.urls
