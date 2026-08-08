from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import JournalEntryViewSet, JournalLineViewSet

router = DefaultRouter()
router.register(r'entries', JournalEntryViewSet)
router.register(r'lines', JournalLineViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
