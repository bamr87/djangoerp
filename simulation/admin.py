from django.contrib import admin

from .models import Agent, AgentDecision, Experiment, ProductionJob, ProductionLine, Scenario, SupplierPayable


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ['name', 'tick', 'current_date', 'status', 'driver', 'agent_model']
    readonly_fields = ['tick', 'current_date', 'state', 'status', 'error_message']


@admin.register(ProductionLine)
class ProductionLineAdmin(admin.ModelAdmin):
    list_display = ['code', 'scenario', 'family', 'machines', 'shifts', 'status', 'wear', 'capex']
    list_filter = ['status', 'family']


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ['code', 'role', 'resource_kind', 'brain', 'daily_cost', 'decisions_count', 'llm_calls', 'ai_cost']
    list_filter = ['role', 'resource_kind', 'brain']
    search_fields = ['code', 'name']


@admin.register(AgentDecision)
class AgentDecisionAdmin(admin.ModelAdmin):
    list_display = ['tick', 'sim_date', 'agent', 'action', 'driver', 'status', 'summary']
    list_filter = ['kind', 'driver', 'status', 'agent__role']
    search_fields = ['summary', 'rationale']

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProductionJob)
class ProductionJobAdmin(admin.ModelAdmin):
    list_display = ['work_order', 'line', 'status', 'hours_required', 'hours_done', 'absorbed_value']
    list_filter = ['status', 'line']


@admin.register(SupplierPayable)
class SupplierPayableAdmin(admin.ModelAdmin):
    list_display = ['grn', 'supplier', 'amount', 'received_on', 'due_on', 'paid_on']
    list_filter = ['supplier']


@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ['name', 'scenario', 'horizon_ticks', 'driver', 'status', 'completed_at']
    readonly_fields = ['baseline', 'variant', 'summary']
