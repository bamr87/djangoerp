from django.shortcuts import render
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import registry, services


class WorkspaceViewSet(viewsets.ViewSet):
    """
    API endpoint for the workspace shell: the navigation registry, the home
    overview, global search, financial statements and document flow
    """
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get'])
    def registry(self, request):
        """
        Every module and business object the shell shows: navigation groups,
        endpoints, field types, filters, lifecycles, actions and the roles
        that may read and write each, introspected from the live viewsets.
        """
        return Response(registry.build())

    @action(detail=False, methods=['get'])
    def overview(self, request):
        """Home page: work queues, record counts and recent activity, for what this user may read."""
        return Response(services.overview(request))

    @action(detail=False, methods=['get'])
    def search(self, request):
        """Global search (?search=) across every resource this user may list, using each viewset's search fields."""
        return Response(services.search(request))

    @action(detail=False, methods=['get'])
    def statements(self, request):
        """
        A live financial statement from reports.report_generators:
        ?report=trial_balance|balance_sheet|income_statement|general_ledger
        with as_of, from, to and (for the general ledger) account.
        """
        return Response(services.statements(request))

    @action(detail=False, methods=['get'], url_path=r'flow/(?P<resource>[a-z0-9.-]+)/(?P<object_id>[0-9]+)')
    def flow(self, request, resource=None, object_id=None):
        """
        Document flow for one business object: related documents, the journal
        entries and stock moves it caused, and its audit trail — each section
        only where the user's role may read it.
        """
        return Response(services.flow(request, resource, int(object_id)))


def shell(request, path=''):
    """The workspace single-page app: one shell, every module, deep-linkable at /app/<module>/<resource>/<id>/."""
    return render(request, 'workspace/app.html', {'registry': registry.build(), 'page_title': 'Workspace'})
