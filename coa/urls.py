from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AccountTypeViewSet, AccountViewSet

router = DefaultRouter()
router.register(r'accounts', AccountViewSet)
router.register(r'account-types', AccountTypeViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
