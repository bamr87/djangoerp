from django.contrib import admin
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

schema_view = get_schema_view(
    openapi.Info(
        title="DjangoERP API",
        default_version='v1',
        description="Django ERP — accounting, supply chain, manufacturing and MRP API",
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.accounts.urls')),
    path('api/coa/', include('apps.coa.urls')),
    path('api/journal/', include('apps.journal.urls')),
    path('api/invoices/', include('apps.invoices.urls')),
    path('api/reports/', include('apps.reports.urls')),
    path('api/partners/', include('apps.partners.urls')),
    path('api/products/', include('apps.products.urls')),
    path('api/inventory/', include('apps.inventory.urls')),
    path('api/purchasing/', include('apps.purchasing.urls')),
    path('api/sales/', include('apps.sales.urls')),
    path('api/manufacturing/', include('apps.manufacturing.urls')),
    path('api/mrp/', include('apps.mrp.urls')),
    path('api/company/', include('apps.company.urls')),
    path('swagger<format>/', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    # Browser-facing routes last — apps.company.urls_web owns the site root.
    path('', include('apps.company.urls_web')),
]
