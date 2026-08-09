"""
Shared demo-company builder used by the demo_erp management command and the
end-to-end integration tests: a minimal but complete configuration — chart of
accounts, GL mappings, partners, warehouse, a two-level make/buy product
structure and its BOM — on which the whole order-to-cash / procure-to-pay /
make-to-stock loop can run.
"""
from decimal import Decimal

from coa.models import Account, AccountType
from inventory.models import Warehouse
from manufacturing.models import BillOfMaterials, BOMLine
from partners.models import BusinessPartner
from products.models import Product, ProductCategory, UnitOfMeasure


def build_demo_company(created_by=None):
    """
    Idempotent (get_or_create throughout): safe to call on a database that
    already holds the demo master data. Returns a dict of every object the
    demo flow needs.
    """
    types = {t.code: t for t in AccountType.objects.all()}

    def account(code, name, type_code):
        acc, _ = Account.objects.get_or_create(
            code=code, defaults={'name': name, 'account_type': types[type_code]}
        )
        return acc

    cash = account('1000', 'Cash', 'AS')
    receivable = account('1100', 'Accounts Receivable', 'AS')
    inventory_acc = account('1200', 'Inventory', 'AS')
    payable = account('2100', 'Accounts Payable', 'LI')
    capital = account('3000', 'Share Capital', 'EQ')
    revenue = account('4000', 'Sales Revenue', 'RE')
    cogs = account('5000', 'Cost of Goods Sold', 'EX')

    each, _ = UnitOfMeasure.objects.get_or_create(code='EA', defaults={'name': 'Each'})

    manufactured, _ = ProductCategory.objects.get_or_create(
        name='Manufactured goods',
        defaults={'inventory_account': inventory_acc, 'revenue_account': revenue, 'cogs_account': cogs},
    )
    components, _ = ProductCategory.objects.get_or_create(
        name='Components',
        defaults={'inventory_account': inventory_acc, 'revenue_account': revenue, 'cogs_account': cogs},
    )

    acme, _ = BusinessPartner.objects.get_or_create(
        code='ACME',
        defaults={
            'name': 'ACME Industries', 'is_customer': True,
            'receivable_account': receivable, 'created_by': created_by,
        },
    )
    supco, _ = BusinessPartner.objects.get_or_create(
        code='SUPCO',
        defaults={
            'name': 'Supply Co', 'is_supplier': True,
            'payable_account': payable, 'created_by': created_by,
        },
    )

    warehouse, _ = Warehouse.objects.get_or_create(
        code='WH1', defaults={'name': 'Main Warehouse', 'created_by': created_by},
    )

    widget, _ = Product.objects.get_or_create(
        sku='WIDGET',
        defaults={
            'name': 'Widget', 'category': manufactured, 'uom': each,
            'procurement_type': 'make', 'standard_cost': Decimal('70'),
            'sales_price': Decimal('250'), 'lead_time_days': 2, 'created_by': created_by,
        },
    )
    frame, _ = Product.objects.get_or_create(
        sku='FRAME',
        defaults={
            'name': 'Widget Frame', 'category': components, 'uom': each,
            'procurement_type': 'buy', 'standard_cost': Decimal('40'),
            'lead_time_days': 5, 'safety_stock': Decimal('10'),
            'default_supplier': supco, 'created_by': created_by,
        },
    )
    wheel, _ = Product.objects.get_or_create(
        sku='WHEEL',
        defaults={
            'name': 'Widget Wheel', 'category': components, 'uom': each,
            'procurement_type': 'buy', 'standard_cost': Decimal('15'),
            'lead_time_days': 3, 'safety_stock': Decimal('20'),
            'default_supplier': supco, 'created_by': created_by,
        },
    )

    bom = BillOfMaterials.objects.filter(product=widget, is_active=True).first()
    if bom is None:
        bom = BillOfMaterials.objects.create(
            product=widget, reference='WIDGET-v1', quantity=Decimal('1'), created_by=created_by,
        )
        BOMLine.objects.create(bom=bom, component=frame, quantity=Decimal('1'))
        BOMLine.objects.create(bom=bom, component=wheel, quantity=Decimal('2'))

    return {
        'accounts': {
            'cash': cash, 'receivable': receivable, 'inventory': inventory_acc,
            'payable': payable, 'capital': capital, 'revenue': revenue, 'cogs': cogs,
        },
        'uom': each,
        'categories': {'manufactured': manufactured, 'components': components},
        'customer': acme,
        'supplier': supco,
        'warehouse': warehouse,
        'products': {'widget': widget, 'frame': frame, 'wheel': wheel},
        'bom': bom,
    }
