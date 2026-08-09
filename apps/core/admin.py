from django.contrib import admin

from .models import DocumentSequence


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(admin.ModelAdmin):
    list_display = ['prefix', 'next_value', 'padding']
    search_fields = ['prefix']
