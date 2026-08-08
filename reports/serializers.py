from rest_framework import serializers

from .models import ReportTemplate, SavedReport


class ReportTemplateSerializer(serializers.ModelSerializer):
    created_by_display = serializers.StringRelatedField(source='created_by', read_only=True)

    class Meta:
        model = ReportTemplate
        fields = [
            'id', 'name', 'report_type', 'description', 'configuration',
            'is_system', 'created_by', 'created_by_display', 'created_at', 'updated_at'
        ]
        read_only_fields = ['is_system', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class SavedReportSerializer(serializers.ModelSerializer):
    created_by_display = serializers.StringRelatedField(source='created_by', read_only=True)
    template_name = serializers.StringRelatedField(source='template', read_only=True)

    class Meta:
        model = SavedReport
        fields = [
            'id', 'template', 'template_name', 'name', 'parameters',
            'result_data', 'status', 'error_message', 'created_by',
            'created_by_display', 'created_at'
        ]
        read_only_fields = ['status', 'result_data', 'error_message', 'created_by', 'created_at']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)
