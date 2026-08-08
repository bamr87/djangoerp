from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets

from accounts.permissions import IsAccountant

from .models import JournalEntry, JournalLine
from .serializers import JournalEntrySerializer, JournalLineSerializer


class JournalEntryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for journal entries
    """
    queryset = JournalEntry.objects.all().order_by('-date', '-id')
    serializer_class = JournalEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'date', 'created_by']
    search_fields = ['entry_number', 'description']
    ordering_fields = ['entry_number', 'date', 'status']


class JournalLineViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for journal lines (read-only)
    """
    queryset = JournalLine.objects.all()
    serializer_class = JournalLineSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['entry', 'account']
    ordering_fields = ['entry__date', 'entry__entry_number', 'account__code']
