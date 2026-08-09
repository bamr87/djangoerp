from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsAccountant

from .models import ReportTemplate, SavedReport
from .report_generators import generate_report
from .serializers import ReportTemplateSerializer, SavedReportSerializer


class ReportTemplateViewSet(viewsets.ModelViewSet):
    """
    API endpoint for report templates
    """
    queryset = ReportTemplate.objects.all()
    serializer_class = ReportTemplateSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['report_type', 'is_system']
    search_fields = ['name', 'description']

    def get_queryset(self):
        """
        Filter templates to show only system templates and user's own templates
        """
        # Schema generation probes viewsets with an anonymous user; short-circuit
        # per drf-yasg docs so swagger.json builds without a noisy traceback.
        if getattr(self, 'swagger_fake_view', False):
            return ReportTemplate.objects.none()
        user = self.request.user
        return ReportTemplate.objects.filter(Q(is_system=True) | Q(created_by=user))

    def get_permissions(self):
        """
        Custom permissions:
        - Anyone can view templates
        - Only accountants can create/edit/delete templates
        """
        if self.action in ['list', 'retrieve']:
            permission_classes = [permissions.IsAuthenticated]
        else:
            permission_classes = [permissions.IsAuthenticated, IsAccountant]
        return [permission() for permission in permission_classes]


class SavedReportViewSet(viewsets.ModelViewSet):
    """
    API endpoint for saved reports
    """
    queryset = SavedReport.objects.all()
    serializer_class = SavedReportSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['template', 'status']
    search_fields = ['name']

    def get_queryset(self):
        """
        Show only reports created by the user unless user is admin/accountant
        """
        if getattr(self, 'swagger_fake_view', False):
            return SavedReport.objects.none()
        user = self.request.user
        try:
            if user.role.role in ('admin', 'accountant'):
                return SavedReport.objects.all()
        except AttributeError:
            pass
        return SavedReport.objects.filter(created_by=user)

    @action(detail=True, methods=['post'])
    def regenerate(self, request, pk=None):
        """
        Regenerate a report with the same parameters
        """
        report = self.get_object()
        report.status = 'generating'
        report.result_data = {}
        report.error_message = ''
        report.save()

        try:
            generate_report.delay(report.id)
            return Response({'status': 'Report generation started'})
        except Exception as e:
            report.status = 'failed'
            report.error_message = str(e)
            report.save()
            return Response(
                {'error': 'Failed to start report generation'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
