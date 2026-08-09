from rest_framework.routers import DefaultRouter

from .views import ReportTemplateViewSet, SavedReportViewSet

router = DefaultRouter()
router.register(r'templates', ReportTemplateViewSet, basename='template')
router.register(r'saved-reports', SavedReportViewSet, basename='savedreport')

urlpatterns = router.urls
