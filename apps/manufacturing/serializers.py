from rest_framework import serializers

from .models import BillOfMaterials, BOMLine, WorkOrder


class BOMLineSerializer(serializers.ModelSerializer):
    component_display = serializers.StringRelatedField(source='component', read_only=True)

    class Meta:
        model = BOMLine
        fields = ['id', 'component', 'component_display', 'quantity']

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError('Component quantity must be positive.')
        return value


class BillOfMaterialsSerializer(serializers.ModelSerializer):
    lines = BOMLineSerializer(many=True)
    product_display = serializers.StringRelatedField(source='product', read_only=True)

    class Meta:
        model = BillOfMaterials
        fields = [
            'id', 'product', 'product_display', 'reference', 'quantity', 'is_active',
            'lines', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def validate(self, data):
        product = data.get('product', getattr(self.instance, 'product', None))
        quantity = data.get('quantity', getattr(self.instance, 'quantity', None))
        if quantity is not None and quantity <= 0:
            raise serializers.ValidationError('BOM output quantity must be positive.')

        lines = data.get('lines')
        if lines is not None:
            if not lines:
                raise serializers.ValidationError('A BOM must have at least one component line.')
            components = [line['component'] for line in lines]
            if len({component.id for component in components}) != len(components):
                raise serializers.ValidationError('Each component may appear only once per BOM.')
            for component in components:
                if component.id == product.id:
                    raise serializers.ValidationError('A product cannot be a component of itself.')
                self._reject_cycles(product, component)

        is_active = data.get('is_active', getattr(self.instance, 'is_active', True))
        if is_active and product is not None:
            clash = BillOfMaterials.objects.filter(product=product, is_active=True)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    f'{product.sku} already has an active BOM — deactivate it before activating another.'
                )
        return data

    def _reject_cycles(self, product, component, _seen=None):
        """
        Walk the component's own active BOM tree (same ancestor-walk idea as
        coa.AccountSerializer): if the finished product is reachable, this BOM
        would create a make-loop and MRP explosion would never terminate.
        """
        seen = _seen or set()
        if component.id in seen:
            return
        seen.add(component.id)
        for bom in BillOfMaterials.objects.filter(product=component, is_active=True).prefetch_related('lines'):
            for line in bom.lines.all():
                if line.component_id == product.id:
                    raise serializers.ValidationError(
                        f'Circular BOM: {product.sku} is already a component below {component.sku}.'
                    )
                self._reject_cycles(product, line.component, seen)

    def create(self, validated_data):
        lines_data = validated_data.pop('lines')
        validated_data['created_by'] = self.context['request'].user
        bom = BillOfMaterials.objects.create(**validated_data)
        for line_data in lines_data:
            BOMLine.objects.create(bom=bom, **line_data)
        return bom

    def update(self, instance, validated_data):
        lines_data = validated_data.pop('lines', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if lines_data is not None:
            instance.lines.all().delete()
            for line_data in lines_data:
                BOMLine.objects.create(bom=instance, **line_data)
        return instance


class WorkOrderSerializer(serializers.ModelSerializer):
    product_display = serializers.StringRelatedField(source='product', read_only=True)
    warehouse_display = serializers.StringRelatedField(source='warehouse', read_only=True)

    class Meta:
        model = WorkOrder
        fields = [
            'id', 'number', 'product', 'product_display', 'bom', 'warehouse', 'warehouse_display',
            'quantity', 'status', 'due_date', 'started_at', 'completed_at', 'source_reference',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'number', 'status', 'started_at', 'completed_at', 'source_reference',
            'created_by', 'created_at', 'updated_at',
        ]

    def validate(self, data):
        if self.instance and self.instance.status != 'draft':
            raise serializers.ValidationError('Only draft work orders can be edited.')
        quantity = data.get('quantity', getattr(self.instance, 'quantity', None))
        if quantity is not None and quantity <= 0:
            raise serializers.ValidationError('Work order quantity must be positive.')
        product = data.get('product', getattr(self.instance, 'product', None))
        bom = data.get('bom', getattr(self.instance, 'bom', None))
        if product and bom and bom.product_id != product.id:
            raise serializers.ValidationError('The BOM must belong to the product being made.')
        return data

    def create(self, validated_data):
        from apps.core.models import DocumentSequence

        validated_data['created_by'] = self.context['request'].user
        validated_data['number'] = DocumentSequence.next_number('WO')
        return super().create(validated_data)
