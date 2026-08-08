from django.contrib import admin

from .models import ReportTemplate, SavedReport


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'report_type', 'is_system', 'created_by', 'updated_at')
    list_filter = ('report_type', 'is_system')
    search_fields = ('name', 'description')
    readonly_fields = ('created_by', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        obj.save()


@admin.register(SavedReport)
class SavedReportAdmin(admin.ModelAdmin):
    list_display = ('name', 'template', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'template__report_type', 'created_at')
    search_fields = ('name',)
    readonly_fields = ('created_by', 'created_at', 'status', 'error_message', 'result_data')

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        obj.save()
