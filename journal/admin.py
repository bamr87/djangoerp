from django.contrib import admin

from .models import JournalEntry, JournalLine


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 1
    autocomplete_fields = ['account']


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ('entry_number', 'date', 'description', 'status', 'created_by')
    list_filter = ('status', 'date')
    search_fields = ('entry_number', 'description')
    readonly_fields = ('created_by',)
    inlines = [JournalLineInline]
    date_hierarchy = 'date'

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        obj.save()


@admin.register(JournalLine)
class JournalLineAdmin(admin.ModelAdmin):
    list_display = ('entry', 'account', 'debit', 'credit', 'reference')
    list_filter = ('entry__status', 'account')
    search_fields = ('entry__entry_number', 'entry__description', 'account__name', 'reference')
    autocomplete_fields = ['entry', 'account']
