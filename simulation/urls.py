from rest_framework.routers import DefaultRouter

from .views import (AgentDecisionViewSet, AgentViewSet, ExperimentViewSet, ProductionLineViewSet, ScenarioViewSet,
                    TickSnapshotViewSet)

router = DefaultRouter()
router.register(r'scenarios', ScenarioViewSet)
router.register(r'lines', ProductionLineViewSet)
router.register(r'agents', AgentViewSet)
router.register(r'decisions', AgentDecisionViewSet)
router.register(r'snapshots', TickSnapshotViewSet)
router.register(r'experiments', ExperimentViewSet)

urlpatterns = router.urls
