from django.utils import timezone
from rest_framework import serializers

from . import experiments
from . import parameters as params_module
from .models import Agent, AgentDecision, Experiment, ProductionLine, Scenario, TickSnapshot


class ScenarioSerializer(serializers.ModelSerializer):
    """
    A sandbox economy. On create every field but `name` is optional; `parameters`
    may hold any subset of the catalogue (GET /scenarios/{id}/design/ lists it).
    On update, `parameters` is merged into the current values.
    """

    start_date = serializers.DateField(required=False)

    class Meta:
        model = Scenario
        fields = [
            'id', 'name', 'description', 'seed', 'start_date', 'current_date', 'tick', 'tick_days', 'status',
            'driver', 'agent_model', 'parameters', 'state', 'warehouse', 'error_message', 'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['current_date', 'tick', 'status', 'state', 'warehouse', 'error_message', 'created_by',
                            'created_at', 'updated_at']

    def validate_parameters(self, value):
        return params_module.validate(value)

    def validate_tick_days(self, value):
        if not 1 <= value <= 31:
            raise serializers.ValidationError('A tick is 1 to 31 days')
        return value

    def validate(self, attrs):
        if self.instance is not None:
            for field in ('name', 'seed', 'start_date'):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({field: 'fixed once the scenario exists'})
        else:
            attrs.setdefault('start_date', timezone.now().date())
        return attrs


class AdvanceSerializer(serializers.Serializer):
    ticks = serializers.IntegerField(min_value=1, max_value=120, default=1)


class ProductionLineSerializer(serializers.ModelSerializer):
    """
    A production line. POST commissions one (posts its capex) from `scenario`,
    `family` ('A' or 'B') and optional `machines`, `shifts`, `crew_per_machine`,
    `capex`; PATCH changes `shifts` and `crew_per_machine` only.
    """

    family_code = serializers.ChoiceField(choices=['A', 'B'], write_only=True, required=False)
    family_name = serializers.CharField(source='family.name', read_only=True)

    class Meta:
        model = ProductionLine
        fields = [
            'id', 'scenario', 'code', 'name', 'family', 'family_code', 'family_name', 'machines', 'shifts',
            'crew_per_machine', 'capex', 'useful_life_days', 'commissioned_on', 'retired_on', 'status', 'wear',
            'run_hours', 'accumulated_depreciation', 'capex_entry', 'created_at', 'updated_at',
        ]
        read_only_fields = ['code', 'name', 'family', 'useful_life_days', 'commissioned_on', 'retired_on', 'status',
                            'wear', 'run_hours', 'accumulated_depreciation', 'capex_entry', 'created_at',
                            'updated_at']
        extra_kwargs = {'capex': {'required': False}, 'machines': {'required': False},
                        'shifts': {'required': False}, 'crew_per_machine': {'required': False}}

    def validate(self, attrs):
        if self.instance is None and 'family_code' not in attrs:
            raise serializers.ValidationError({'family_code': 'required: the product family the line makes'})
        if self.instance is not None:
            allowed = {'shifts', 'crew_per_machine'}
            extra = set(attrs) - allowed
            if extra:
                raise serializers.ValidationError({key: 'fixed once commissioned' for key in extra})
        return attrs


class AgentSerializer(serializers.ModelSerializer):
    """
    An agent and its resource economics. PATCH may change `brain`, `model`,
    `daily_cost`, `decision_interval`, `is_active` and `policy` (merged into the
    current policy — the agent acts on it from the next tick).
    """

    role_label = serializers.CharField(source='get_role_display', read_only=True)
    line_code = serializers.CharField(source='line.code', read_only=True, default=None)
    partner_code = serializers.CharField(source='partner.code', read_only=True, default=None)

    class Meta:
        model = Agent
        fields = [
            'id', 'scenario', 'code', 'name', 'role', 'role_label', 'resource_kind', 'partner', 'partner_code',
            'line', 'line_code', 'brain', 'model', 'daily_cost', 'cost_account', 'decision_interval', 'policy',
            'state', 'is_active', 'decisions_count', 'llm_calls', 'ai_cost', 'created_at', 'updated_at',
        ]
        read_only_fields = ['scenario', 'code', 'name', 'role', 'resource_kind', 'partner', 'line', 'cost_account',
                            'state', 'decisions_count', 'llm_calls', 'ai_cost', 'created_at', 'updated_at']

    def validate_policy(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('policy must be an object')
        return {**(self.instance.policy if self.instance else {}), **value}

    def validate_decision_interval(self, value):
        if not 1 <= value <= 365:
            raise serializers.ValidationError('between 1 and 365 ticks')
        return value

    def validate_daily_cost(self, value):
        if value < 0:
            raise serializers.ValidationError('cannot be negative')
        return value


class AgentDecisionSerializer(serializers.ModelSerializer):
    agent_code = serializers.CharField(source='agent.code', read_only=True)
    role = serializers.CharField(source='agent.role', read_only=True)

    class Meta:
        model = AgentDecision
        fields = [
            'id', 'scenario', 'agent', 'agent_code', 'role', 'tick', 'sim_date', 'kind', 'action', 'summary',
            'rationale', 'observation', 'payload', 'documents', 'driver', 'status', 'error', 'model',
            'input_tokens', 'output_tokens', 'cost', 'created_at',
        ]
        read_only_fields = fields


class TickSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = TickSnapshot
        fields = ['id', 'scenario', 'tick', 'sim_date', 'kpis', 'created_at']
        read_only_fields = fields


class ExperimentSerializer(serializers.ModelSerializer):
    """
    A what-if. POST {scenario, name, horizon_ticks, changes, driver} runs a
    baseline and a variant from the current state on the same random numbers,
    both rolled back, and stores only the comparison (`summary`).
    """

    class Meta:
        model = Experiment
        fields = [
            'id', 'scenario', 'name', 'description', 'horizon_ticks', 'changes', 'driver', 'status', 'base_tick',
            'base_date', 'baseline', 'variant', 'summary', 'error_message', 'completed_at', 'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['status', 'base_tick', 'base_date', 'baseline', 'variant', 'summary', 'error_message',
                            'completed_at', 'created_by', 'created_at', 'updated_at']

    def validate_changes(self, value):
        return experiments.validate_changes(value)

    def validate_horizon_ticks(self, value):
        if not 1 <= value <= 180:
            raise serializers.ValidationError('between 1 and 180 ticks')
        return value

    def validate(self, attrs):
        if self.instance is not None:
            for field in ('scenario', 'horizon_ticks', 'changes', 'driver'):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({field: 'create a new experiment to change the setup'})
        return attrs
