#!/usr/bin/env bash
# End-to-end DjangoERP conformance smoke test, driven entirely through the REST API.
#
# `manage.py demo_erp` proves the same business loops in-process against the
# services. This proves them over HTTP, which is where serializer, permission and
# viewset-queryset behaviour becomes observable -- two defects in this repository
# (the prefetch-cache status roll-up in sales/purchasing, and the missing Celery
# dispatch on saved-report creation) were invisible from the service side and only
# showed up here. Keep both gates.
#
# Usage:
#   CELERY_TASK_ALWAYS_EAGER=False docker compose --profile celery up -d --wait
#   bash tools/e2e-api-smoke.sh                 # defaults to http://127.0.0.1:8000
#   BASE=http://host:port bash tools/e2e-api-smoke.sh
#
# Expects a freshly migrated database with the DJANGO_SUPERUSER_* admin seeded
# (the container entrypoint does this) and no prior demo data -- it creates its own
# company, chart of accounts, partners, products and warehouse from scratch.
#
# Exits non-zero on the first failing assertion count. Requires curl and jq.
set -uo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
FAILED=0

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32mok\033[0m   %s\n' "$*"; }
bad()  { printf '   \033[31mFAIL\033[0m %s\n' "$*"; FAILED=$((FAILED+1)); }

# api METHOD PATH [BODY] -> body on stdout; non-2xx is a failure
api() {
    local method="$1" path="$2" body="${3:-}" out code
    if [ -n "$body" ]; then
        out=$(curl -s -w '\n%{http_code}' -X "$method" "$BASE$path" \
              -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "$body")
    else
        out=$(curl -s -w '\n%{http_code}' -X "$method" "$BASE$path" \
              -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')
    fi
    code=$(printf '%s' "$out" | tail -n1)
    body=$(printf '%s' "$out" | sed '$d')
    if [ "${code:0:1}" != "2" ]; then
        printf '   \033[31mHTTP %s\033[0m %s %s\n%s\n' "$code" "$method" "$path" "$body" >&2
        FAILED=$((FAILED+1))
        printf '%s' "$body"
        return 1
    fi
    printf '%s' "$body"
}

id_of() { jq -r '.id'; }

TODAY=$(date +%F)
D=$(date -v+10d +%F 2>/dev/null || date -d '+10 days' +%F)

# ---------------------------------------------------------------- auth
say "1. Authentication (JWT)"
LOGIN=$(curl -s -X POST "$BASE/api/auth/login/" -H 'Content-Type: application/json' \
        -d '{"username":"djangoerp","password":"djangoerp"}')
TOKEN=$(printf '%s' "$LOGIN" | jq -r '.access')
[ "$TOKEN" != "null" ] && [ -n "$TOKEN" ] && ok "token acquired, role=$(printf '%s' "$LOGIN" | jq -r '.user.role')" || { bad "login"; exit 1; }

# unauthenticated request must be rejected
UNAUTH=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/journal/entries/")
[ "$UNAUTH" = "401" ] && ok "unauthenticated request rejected (401)" || bad "expected 401, got $UNAUTH"

# The script claims fixed unique keys (account codes, partner codes, SKUs), so a
# second run against the same database fails with a cascade of 400s that looks like
# a regression. Fail fast and say what to do instead.
EXISTING=$(api GET '/api/products/products/?page_size=1' | jq -r '.count // 0')
if [ "${EXISTING:-0}" != "0" ]; then
    printf '\n\033[31mThis database already holds demo data (%s product(s)).\033[0m\n' "$EXISTING"
    printf 'This smoke test claims fixed unique keys and needs a clean database:\n'
    printf '   docker compose --profile celery down -v\n'
    printf '   CELERY_TASK_ALWAYS_EAGER=False docker compose --profile celery up -d --wait\n\n'
    exit 2
fi

# ---------------------------------------------------------------- company + COA
say "2. Company and chart of accounts"
CO=$(api POST /api/company/companies/ '{"name":"Northwind Widgets","legal_name":"Northwind Widgets LLC","currency_code":"USD"}' | id_of)
ok "company id=$CO"

AT=$(api GET '/api/coa/account-types/?page_size=50')
at_id() { printf '%s' "$AT" | jq -r --arg c "$1" '.results[] | select(.code==$c) | .id'; }
ok "account types seeded: $(printf '%s' "$AT" | jq -r '[.results[].code] | join(",")')"

mkacct() { api POST /api/coa/accounts/ "{\"code\":\"$1\",\"name\":\"$2\",\"account_type\":$3}" | id_of; }
CASH=$(mkacct 1000 "Cash" "$(at_id AS)")
AR=$(mkacct   1100 "Accounts Receivable" "$(at_id AS)")
INV=$(mkacct  1200 "Inventory" "$(at_id AS)")
AP=$(mkacct   2000 "Accounts Payable" "$(at_id LI)")
CAP=$(mkacct  3000 "Owner Capital" "$(at_id EQ)")
REV=$(mkacct  4000 "Sales Revenue" "$(at_id RE)")
COGS=$(mkacct 5000 "Cost of Goods Sold" "$(at_id EX)")
ok "7 accounts created (cash=$CASH ar=$AR inv=$INV ap=$AP cap=$CAP rev=$REV cogs=$COGS)"

# COA invariants
CYC=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$BASE/api/coa/accounts/$CASH/" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "{\"parent_account\":$CASH}")
[ "$CYC" = "400" ] && ok "self-parent cycle rejected (400)" || bad "COA cycle guard: expected 400, got $CYC"

# ---------------------------------------------------------------- opening capital
say "3. Opening capital (manual journal entry)"
JE=$(api POST /api/journal/entries/ "{\"entry_number\":\"JE-OPEN-1\",\"date\":\"$TODAY\",\"description\":\"Owner capital\",\"status\":\"posted\",\"lines\":[{\"account\":$CASH,\"debit\":\"25000.00\",\"credit\":\"0.00\"},{\"account\":$CAP,\"debit\":\"0.00\",\"credit\":\"25000.00\"}]}")
ok "posted $(printf '%s' "$JE" | jq -r '.entry_number') debit=$(printf '%s' "$JE" | jq -r '.total_debit') credit=$(printf '%s' "$JE" | jq -r '.total_credit')"

UNBAL=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/journal/entries/" \
        -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        -d "{\"entry_number\":\"JE-BAD-1\",\"date\":\"$TODAY\",\"lines\":[{\"account\":$CASH,\"debit\":\"10.00\",\"credit\":\"0.00\"},{\"account\":$CAP,\"debit\":\"0.00\",\"credit\":\"7.00\"}]}")
[ "$UNBAL" = "400" ] && ok "unbalanced entry rejected (400)" || bad "double-entry guard: expected 400, got $UNBAL"

NOLINES=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/journal/entries/" \
        -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        -d "{\"entry_number\":\"JE-BAD-2\",\"date\":\"$TODAY\",\"lines\":[]}")
[ "$NOLINES" = "400" ] && ok "entry with no lines rejected (400)" || bad "empty-entry guard: expected 400, got $NOLINES"

# ---------------------------------------------------------------- master data
say "4. Master data (partners, UOM, category, products, warehouse)"
CUST=$(api POST /api/partners/partners/ "{\"code\":\"ACME\",\"name\":\"Acme Retail\",\"is_customer\":true,\"receivable_account\":$AR}" | id_of)
SUPP=$(api POST /api/partners/partners/ "{\"code\":\"SUPCO\",\"name\":\"Supply Co\",\"is_supplier\":true,\"payable_account\":$AP}" | id_of)
ok "partners: customer=$CUST supplier=$SUPP"

UOM=$(api POST /api/products/uoms/ '{"code":"EA","name":"Each"}' | id_of)
CAT=$(api POST /api/products/categories/ "{\"name\":\"Widgets\",\"inventory_account\":$INV,\"revenue_account\":$REV,\"cogs_account\":$COGS}" | id_of)
ok "uom=$UOM category=$CAT (GL mappings wired)"

mkprod() { # sku name procurement cost lead safety
  api POST /api/products/products/ "{\"sku\":\"$1\",\"name\":\"$2\",\"uom\":$UOM,\"category\":$CAT,\"procurement_type\":\"$3\",\"standard_cost\":\"$4\",\"lead_time_days\":$5,\"safety_stock\":\"$6\",\"default_supplier\":$SUPP}" | id_of
}
FRAME=$(mkprod FRAME "Widget Frame" buy 40.0000 3 10.000)
WHEEL=$(mkprod WHEEL "Widget Wheel" buy 15.0000 5 20.000)
WIDGET=$(api POST /api/products/products/ "{\"sku\":\"WIDGET\",\"name\":\"Finished Widget\",\"uom\":$UOM,\"category\":$CAT,\"procurement_type\":\"make\",\"standard_cost\":\"70.0000\",\"sales_price\":\"250.0000\",\"lead_time_days\":2,\"safety_stock\":\"0.000\"}" | id_of)
ok "products: FRAME=$FRAME WHEEL=$WHEEL WIDGET=$WIDGET"

WH=$(api POST /api/inventory/warehouses/ '{"code":"WH1","name":"Main Warehouse"}' | id_of)
ok "warehouse=$WH"

BOM=$(api POST /api/manufacturing/boms/ "{\"product\":$WIDGET,\"reference\":\"BOM-WIDGET-v1\",\"quantity\":\"1.000\",\"is_active\":true,\"lines\":[{\"component\":$FRAME,\"quantity\":\"1.000\"},{\"component\":$WHEEL,\"quantity\":\"2.000\"}]}" | id_of)
ok "BOM=$BOM (1x FRAME + 2x WHEEL per WIDGET)"

CYCBOM=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/manufacturing/boms/" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d "{\"product\":$FRAME,\"reference\":\"BOM-CYCLE\",\"quantity\":\"1.000\",\"lines\":[{\"component\":$WIDGET,\"quantity\":\"1.000\"}]}")
[ "$CYCBOM" = "400" ] && ok "BOM cycle rejected (400)" || bad "BOM cycle guard: expected 400, got $CYCBOM"

# ---------------------------------------------------------------- demand
say "5. Sales order (demand signal)"
SO=$(api POST /api/sales/orders/ "{\"customer\":$CUST,\"warehouse\":$WH,\"order_date\":\"$TODAY\",\"requested_date\":\"$D\",\"lines\":[{\"product\":$WIDGET,\"quantity\":\"5.000\",\"unit_price\":\"250.0000\"}]}")
SO_ID=$(printf '%s' "$SO" | id_of)
ok "$(printf '%s' "$SO" | jq -r '.number') total=$(printf '%s' "$SO" | jq -r '.total_amount') status=$(printf '%s' "$SO" | jq -r '.status')"
SO=$(api POST "/api/sales/orders/$SO_ID/confirm/" '{}')
ok "confirmed -> status=$(printf '%s' "$SO" | jq -r '.status')"

# ---------------------------------------------------------------- MRP (real Celery)
say "6. MRP run (async, dispatched to the Celery worker)"
RUN=$(api POST /api/mrp/runs/ "{\"warehouse\":$WH,\"as_of_date\":\"$TODAY\"}")
RUN_ID=$(printf '%s' "$RUN" | id_of)
for i in $(seq 1 40); do
    RUN=$(api GET "/api/mrp/runs/$RUN_ID/")
    ST=$(printf '%s' "$RUN" | jq -r '.status')
    [ "$ST" = "completed" ] || [ "$ST" = "failed" ] && break
    sleep 0.5
done
[ "$ST" = "completed" ] && ok "$(printf '%s' "$RUN" | jq -r '.number') completed async, planned_orders=$(printf '%s' "$RUN" | jq -r '.planned_order_count')" \
    || bad "MRP run status=$ST error=$(printf '%s' "$RUN" | jq -r '.error_message')"

PO_LIST=$(api GET "/api/mrp/planned-orders/?mrp_run=$RUN_ID&page_size=50")
printf '%s' "$PO_LIST" | jq -r '.results[] | "        plan \(.order_type) \(.quantity) \(.product_display)  release \(.release_date) due \(.due_date)"'

say "7. Convert plan into draft purchase and work orders"
CONV=$(api POST "/api/mrp/runs/$RUN_ID/convert/" '{}')
NPO=$(printf '%s' "$CONV" | jq '.purchase_orders | length')
NWO=$(printf '%s' "$CONV" | jq '.work_orders | length')
ok "converted -> $NPO purchase order(s), $NWO work order(s)"
POID=$(printf '%s' "$CONV" | jq -r '.purchase_orders[0].id')
WOID=$(printf '%s' "$CONV" | jq -r '.work_orders[0].id')

# ---------------------------------------------------------------- procure to pay
say "8. Purchasing: confirm and receive (Dr Inventory / Cr AP)"
api POST "/api/purchasing/orders/$POID/confirm/" '{}' >/dev/null && ok "PO confirmed"
RCV=$(api POST "/api/purchasing/orders/$POID/receive/" "{\"receipt_date\":\"$TODAY\"}")
ok "received $(printf '%s' "$RCV" | jq -r '.grn_number'): moves=$(printf '%s' "$RCV" | jq '.moves|length') JE=$(printf '%s' "$RCV" | jq -r '.journal_entry')"
PO_ST=$(printf '%s' "$RCV" | jq -r '.order.status')
[ "$PO_ST" = "received" ] && ok "PO rolled up to 'received'" || bad "PO status is '$PO_ST', expected 'received'"

# ---------------------------------------------------------------- make to stock
say "9. Manufacturing: availability, confirm, start, complete (backflush)"
AVAIL=$(api GET "/api/manufacturing/work-orders/$WOID/availability/")
printf '%s' "$AVAIL" | jq -r '.[] | "        \(.component): required \(.required) on_hand \(.on_hand) sufficient=\(.sufficient)"'
printf '%s' "$AVAIL" | jq -e 'all(.[]; .sufficient)' >/dev/null && ok "all components available" || bad "components short"
api POST "/api/manufacturing/work-orders/$WOID/confirm/" '{}' >/dev/null && ok "WO confirmed"
api POST "/api/manufacturing/work-orders/$WOID/start/" '{}' >/dev/null && ok "WO started"
CMP=$(api POST "/api/manufacturing/work-orders/$WOID/complete/" "{\"completion_date\":\"$TODAY\"}")
ok "WO completed: status=$(printf '%s' "$CMP" | jq -r '.work_order.status') issues=$(printf '%s' "$CMP" | jq '.issue_moves|length') JE=$(printf '%s' "$CMP" | jq -r '.journal_entry')"

STK=$(api GET "/api/inventory/stock-levels/?warehouse=$WH&page_size=50")
printf '%s' "$STK" | jq -r '.results[] | "        \(.product_display): on_hand \(.quantity_on_hand) @ avg \(.average_cost)"'

# ---------------------------------------------------------------- order to cash
say "10. Sales: ship (Dr COGS / Cr Inventory) then invoice"
SHP=$(api POST "/api/sales/orders/$SO_ID/ship/" "{\"ship_date\":\"$TODAY\"}")
ok "shipped $(printf '%s' "$SHP" | jq -r '.shipment_number'): moves=$(printf '%s' "$SHP" | jq '.moves|length') JE=$(printf '%s' "$SHP" | jq -r '.journal_entry')"
SO_ST=$(printf '%s' "$SHP" | jq -r '.order.status')
[ "$SO_ST" = "shipped" ] && ok "SO rolled up to 'shipped'" || bad "SO status is '$SO_ST', expected 'shipped'"
INVOICE=$(api POST "/api/sales/orders/$SO_ID/invoice/" "{\"due_date\":\"$D\"}")
INV_ID=$(printf '%s' "$INVOICE" | id_of)
ok "invoice $(printf '%s' "$INVOICE" | jq -r '.invoice_number') total=$(printf '%s' "$INVOICE" | jq -r '.total') status=$(printf '%s' "$INVOICE" | jq -r '.status')"

POSTED=$(api POST "/api/invoices/invoices/$INV_ID/post/" "{\"posting_date\":\"$TODAY\"}")
ok "invoice posted -> JE $(printf '%s' "$POSTED" | jq -r '.journal_entry_display // .journal_entry')"

PAY=$(api POST /api/invoices/payments/ "{\"payment_reference\":\"PAY-0001\",\"invoice\":$INV_ID,\"amount\":\"1250.00\",\"payment_method\":\"bank_transfer\",\"payment_date\":\"$TODAY\",\"deposit_account\":$CASH}")
PAY_ID=$(printf '%s' "$PAY" | id_of)
PAY=$(api POST "/api/invoices/payments/$PAY_ID/post/" '{}')
ok "payment posted -> JE $(printf '%s' "$PAY" | jq -r '.journal_entry_display // .journal_entry')"

# ---------------------------------------------------------------- immutability
say "11. Immutability guards"
IMM=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$BASE/api/invoices/invoices/$INV_ID/" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"total":"9999.00"}')
[ "$IMM" = "400" ] && ok "posted invoice rejects edits (400)" || bad "posted invoice edit: expected 400, got $IMM"

JE_ID=$(api GET '/api/journal/entries/?ordering=-id&page_size=1' | jq -r '.results[0].id')
IMM2=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$BASE/api/journal/entries/$JE_ID/" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"description":"tamper"}')
[ "$IMM2" = "400" ] && ok "posted journal entry rejects edits (400)" || bad "posted JE edit: expected 400, got $IMM2"

# ---------------------------------------------------------------- reports (real Celery)
say "12. Financial reports (async, dispatched to the Celery worker)"
run_report() { # type name
    local t="$1" n="$2" tpl sr st
    tpl=$(api POST /api/reports/templates/ "{\"name\":\"$n\",\"report_type\":\"$t\"}" | id_of)
    sr=$(api POST /api/reports/saved-reports/ "{\"template\":$tpl,\"name\":\"$n run\",\"parameters\":{\"as_of_date\":\"$TODAY\",\"start_date\":\"2026-01-01\",\"end_date\":\"$TODAY\"}}" | id_of)
    for i in $(seq 1 40); do
        R=$(api GET "/api/reports/saved-reports/$sr/")
        st=$(printf '%s' "$R" | jq -r '.status')
        [ "$st" = "completed" ] || [ "$st" = "failed" ] && break
        sleep 0.5
    done
    printf '%s' "$R"
}

TB=$(run_report trial_balance "Trial Balance")
printf '%s' "$TB" | jq -e '.status=="completed"' >/dev/null \
    && ok "trial balance: debits=$(printf '%s' "$TB" | jq -r '.result_data.total_debits') credits=$(printf '%s' "$TB" | jq -r '.result_data.total_credits') balanced=$(printf '%s' "$TB" | jq -r '.result_data.balanced')" \
    || bad "trial balance: $(printf '%s' "$TB" | jq -r '.status + " " + (.error_message//"")')"
printf '%s' "$TB" | jq -e '.result_data.balanced==true' >/dev/null && ok "TRIAL BALANCE FOOTS" || bad "trial balance does not foot"

BS=$(run_report balance_sheet "Balance Sheet")
printf '%s' "$BS" | jq -e '.status=="completed"' >/dev/null \
    && ok "balance sheet: assets=$(printf '%s' "$BS" | jq -r '.result_data.total_assets') liab=$(printf '%s' "$BS" | jq -r '.result_data.total_liabilities') equity=$(printf '%s' "$BS" | jq -r '.result_data.total_equity') cpe=$(printf '%s' "$BS" | jq -r '.result_data.current_period_earnings')" \
    || bad "balance sheet: $(printf '%s' "$BS" | jq -r '.status + " " + (.error_message//"")')"
printf '%s' "$BS" | jq -e '.result_data.balanced==true' >/dev/null && ok "BALANCE SHEET BALANCES" || bad "balance sheet does not balance"

IS=$(run_report income_statement "Income Statement")
printf '%s' "$IS" | jq -e '.status=="completed"' >/dev/null \
    && ok "income statement: revenue=$(printf '%s' "$IS" | jq -r '.result_data.total_revenue') expenses=$(printf '%s' "$IS" | jq -r '.result_data.total_expenses') net=$(printf '%s' "$IS" | jq -r '.result_data.net_income')" \
    || bad "income statement: $(printf '%s' "$IS" | jq -r '.status + " " + (.error_message//"")')"

GL=$(run_report general_ledger "General Ledger")
printf '%s' "$GL" | jq -e '.status=="completed"' >/dev/null && ok "general ledger generated" \
    || bad "general ledger: $(printf '%s' "$GL" | jq -r '.status + " " + (.error_message//"")')"

CF=$(run_report cash_flow "Cash Flow")
printf '%s' "$CF" | jq -e '.status=="completed"' >/dev/null && ok "cash flow generated" \
    || bad "cash flow: $(printf '%s' "$CF" | jq -r '.status + " " + (.error_message//"")')"

# ---------------------------------------------------------------- audit + regen MRP
say "13. Audit trail and regenerative MRP"
AUD=$(api GET '/api/auth/audit-logs/?page_size=100')
ok "audit rows: $(printf '%s' "$AUD" | jq -r '.count')"
printf '%s' "$AUD" | jq -r '[.results[].details.event] | group_by(.) | map({event:.[0], n:length}) | .[] | "        \(.event): \(.n)"' 2>/dev/null | head -20

RUN2=$(api POST /api/mrp/runs/ "{\"warehouse\":$WH,\"as_of_date\":\"$TODAY\"}")
R2=$(printf '%s' "$RUN2" | id_of)
for i in $(seq 1 40); do
    RUN2=$(api GET "/api/mrp/runs/$R2/"); ST2=$(printf '%s' "$RUN2" | jq -r '.status')
    [ "$ST2" = "completed" ] || [ "$ST2" = "failed" ] && break; sleep 0.5
done
ok "second MRP run: status=$ST2 planned_orders=$(printf '%s' "$RUN2" | jq -r '.planned_order_count')"

say "RESULT"
if [ "$FAILED" -eq 0 ]; then
    printf '   \033[32mALL CHECKS PASSED\033[0m\n'; exit 0
else
    printf '   \033[31m%s CHECK(S) FAILED\033[0m\n' "$FAILED"; exit 1
fi
