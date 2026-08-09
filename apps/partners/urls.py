from rest_framework.routers import DefaultRouter

from .views import BusinessPartnerViewSet

router = DefaultRouter()
router.register(r'partners', BusinessPartnerViewSet)

urlpatterns = router.urls
