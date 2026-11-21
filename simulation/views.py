from django.shortcuts import render
from django.utils.dateparse import parse_date
from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAccountantOrReadOnly
from core.viewsets import ProtectedHistoryDestroyMixin

from . import services
from .models import Agent, AgentDecision, Experiment, ProductionLine, Scenario, TickSnapshot
from .serializers import (AdvanceSerializer, AgentDecisionSerializer, AgentSerializer, ExperimentSerializer,
                          ProductionLineSerializer, ScenarioSerializer, TickSnapshotSerializer)

PERMISSIONS = [permissions.IsAuthenticated, IsAccountantOrReadOnly]


def _date(request, name):
    value = request.query_params.get(name)
    return parse_date(value) if value else None


class ScenarioViewSet(ProtectedHistoryDestroyMixin, viewsets.ModelViewSet):
    """
    API endpoint for the agent simulation sandbox's scenario (one per database)

    Agents post real documents and journal entries, so writes need the
    accountant role and a database flagged SIMULATION_SANDBOX; a scenario's
    postings are ledger history, so it is never deleted.
    """
    queryset = Scenario.objects.select_related('warehouse')
    serializer_class = ScenarioSerializer
    permission_classes = PERMISSIONS
    filterset_fields = ['status', 'driver']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'tick']

    def check_deletable(self, instance):
        self.refuse_delete(instance, 'its agents posted to the ledger; start a fresh sandbox database instead')

    def perform_create(self, serializer):
        serializer.instance = services.create_scenario(user=self.request.user, **serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = services.update_scenario(serializer.instance, serializer.validated_data,
                                                       user=self.request.user)

    @action(detail=True, methods=['post'])
    def advance(self, request, pk=None):
        """
        Run the economy forward: body {"ticks": 1..120}. Each tick is one
        transaction; returns the KPI snapshots compiled after each tick.
        """
        params = AdvanceSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        results = services.advance(self.get_object(), params.validated_data['ticks'], user=request.user)
        return Response({'ticks': len(results), 'snapshots': results})

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        """Clear a guardrail halt so the scenario can advance again (the halted tick was rolled back)."""
        scenario = services.resume(self.get_object(), user=request.user)
        return Response(self.get_serializer(scenario).data)

    @action(detail=True, methods=['get'])
    def dashboard(self, request, pk=None):
        """Everything the console's cockpit shows: KPIs, series, lines, agents, decisions, stock, jobs."""
        return Response(services.dashboard(self.get_object()))

    @action(detail=True, methods=['get'])
    def financials(self, request, pk=None):
        """
        Trial balance, balance sheet and income statements from the report
        generators, cost drivers, unit economics and reconciliations. Query:
        as_of, from (YYYY-MM-DD).
        """
        return Response(services.financials(self.get_object(), as_of=_date(request, 'as_of'),
                                            from_date=_date(request, 'from')))

    @action(detail=True, methods=['get'])
    def ledger(self, request, pk=None):
        """General ledger for one account: ?account=<code>&from=&to=."""
        return Response(services.ledger(self.get_object(), request.query_params.get('account', ''),
                                        _date(request, 'from'), _date(request, 'to')))

    @action(detail=True, methods=['get'])
    def trace(self, request, pk=None):
        """Agent decisions and journal entries behind a document reference: ?reference=SO-00012."""
        return Response(services.trace(self.get_object(), request.query_params.get('reference', '')))

    @action(detail=True, methods=['get'])
    def design(self, request, pk=None):
        """The system map: agent roles, parameter catalogue, posting matrix, invariants and flows, live."""
        return Response(services.design(self.get_object()))


class ProductionLineViewSet(ProtectedHistoryDestroyMixin, viewsets.ModelViewSet):
    """
    API endpoint for production lines

    POST commissions a line (posts its capex); PATCH changes shifts and crew;
    a line is retired, never deleted.
    """
    queryset = ProductionLine.objects.select_related('family', 'scenario')
    serializer_class = ProductionLineSerializer
    permission_classes = PERMISSIONS
    filterset_fields = ['scenario', 'status', 'family']
    ordering_fields = ['code', 'commissioned_on']

    def check_deletable(self, instance):
        self.refuse_delete(instance, 'its capex is on the books; retire it instead')

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        scenario = data.pop('scenario')
        data['family'] = data.pop('family_code')
        serializer.instance = services.commission_line(scenario, data, user=self.request.user)

    def perform_update(self, serializer):
        serializer.instance = services.update_line(serializer.instance, serializer.validated_data,
                                                   user=self.request.user)

    @action(detail=True, methods=['post'])
    def retire(self, request, pk=None):
        """Retire the line: queued jobs move to a sister line, the asset is disposed of at book value."""
        line = services.retire_line(self.get_object(), user=request.user)
        return Response(self.get_serializer(line).data)


class AgentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                   viewsets.GenericViewSet):
    """
    API endpoint for simulation agents (the roster is created with the scenario)

    PATCH steers an agent: its brain (heuristic or Claude), model, resource
    cost, review cadence, active flag and policy.
    """
    queryset = Agent.objects.select_related('line', 'partner')
    serializer_class = AgentSerializer
    permission_classes = PERMISSIONS
    filterset_fields = ['scenario', 'role', 'resource_kind', 'brain', 'is_active']
    search_fields = ['code', 'name']
    ordering_fields = ['code', 'role', 'decisions_count', 'ai_cost']


class AgentDecisionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for agent decisions (read-only): what each agent saw, chose and
    why, which brain decided, what it cost, and the documents it produced
    """
    queryset = AgentDecision.objects.select_related('agent')
    serializer_class = AgentDecisionSerializer
    permission_classes = PERMISSIONS
    filterset_fields = {
        'scenario': ['exact'], 'agent': ['exact'], 'agent__role': ['exact'], 'kind': ['exact'],
        'driver': ['exact'], 'status': ['exact'], 'tick': ['exact', 'gte', 'lte'],
    }
    search_fields = ['summary', 'rationale', 'action']
    ordering_fields = ['id', 'tick', 'cost']


class TickSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for per-tick KPI snapshots compiled from the ledger (read-only)"""
    queryset = TickSnapshot.objects.all()
    serializer_class = TickSnapshotSerializer
    permission_classes = PERMISSIONS
    filterset_fields = {'scenario': ['exact'], 'tick': ['exact', 'gte', 'lte']}
    ordering_fields = ['tick']


class ExperimentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for what-if experiments

    Creating one runs a baseline and a variant from the current state on the
    same random numbers, both rolled back; only the comparison is kept.
    """
    queryset = Experiment.objects.select_related('scenario')
    serializer_class = ExperimentSerializer
    permission_classes = PERMISSIONS
    filterset_fields = ['scenario', 'status']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at']

    def perform_create(self, serializer):
        experiment = serializer.save(created_by=self.request.user)
        serializer.instance = services.start_experiment(experiment)


def console(request):
    """The simulation console: a page of the common workspace shell that drives /api/simulation/."""
    return render(request, 'simulation/console.html', {'page_title': 'Simulation console'})
