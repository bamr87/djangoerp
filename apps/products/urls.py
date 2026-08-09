from rest_framework.routers import DefaultRouter

from .views import ProductCategoryViewSet, ProductViewSet, UnitOfMeasureViewSet

router = DefaultRouter()
router.register(r'products', ProductViewSet)
router.register(r'categories', ProductCategoryViewSet)
router.register(r'uoms', UnitOfMeasureViewSet)

urlpatterns = router.urls
