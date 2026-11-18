from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAccountant, IsAccountantOrAuditorReadOnly
from core.viewsets import ProtectedHistoryDestroyMixin

from . import services
from .models import JournalEntry, JournalLine
from .serializers import JournalEntrySerializer, JournalLineSerializer, ReverseEntrySerializer


class JournalEntryViewSet(ProtectedHistoryDestroyMixin, viewsets.ModelViewSet):
    """
    API endpoint for journal entries

    Only a draft can be deleted; a posted entry is voided or reversed.
    """
    queryset = JournalEntry.objects.select_related('reversed_by').order_by('-date', '-id')
    serializer_class = JournalEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'date', 'created_by']
    search_fields = ['entry_number', 'description']
    ordering_fields = ['entry_number', 'date', 'status']

    # POST /entries/{id}/reverse/ (route name journalentry-reverse), delegating to journal.services.
    @action(detail=True, methods=['post'], url_path='reverse', url_name='reverse')
    def reverse_entry(self, request, pk=None):
        """
        Post a reversing entry for this posted manual entry: the same lines with
        debits and credits swapped, linked through `reverses`. 201 the first time;
        a repeat call returns the same reversal with 200. Optional body:
        {"date": "YYYY-MM-DD", "description": "..."}.
        """
        params = ReverseEntrySerializer(data=request.data)
        params.is_valid(raise_exception=True)
        reversal, created = services.reverse_entry(self.get_object(), user=request.user, **params.validated_data)
        return Response(
            self.get_serializer(reversal).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class JournalLineViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for journal lines (read-only)
    """
    queryset = JournalLine.objects.all()
    serializer_class = JournalLineSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountantOrAuditorReadOnly]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['entry', 'account']
    ordering_fields = ['entry__date', 'entry__entry_number', 'account__code']
