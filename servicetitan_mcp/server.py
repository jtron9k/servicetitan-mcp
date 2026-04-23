"""ServiceTitan MCP Server — full-featured integration for Claude.

Environment variables (set in claude_desktop_config.json or .env):
  ST_APP_KEY        — App Key from the developer portal
  ST_CLIENT_ID      — Client ID for this tenant
  ST_CLIENT_SECRET  — Client Secret for this tenant
  ST_TENANT_ID      — Tenant ID
"""

from __future__ import annotations

import json
import os
import sys
import traceback

from mcp.server.fastmcp import FastMCP

from .client import ServiceTitanClient

# ── Bootstrap ────────────────────────────────────────────────────────

mcp = FastMCP(
    "ServiceTitan",
    instructions="""ServiceTitan field-service API: customers, jobs, invoices, estimates, dispatch, pricebook, payroll, memberships, reporting.

HOW TO USE THIS SERVER EFFICIENTLY:

1. PREFER FILTERS OVER LISTING-THEN-SCANNING. Most `list_*` tools accept
   server-side filters (name, status, customer_id, date ranges). Use them.
   Listing every customer then filtering locally wastes API quota and tokens.

2. PAGINATION. `list_*` tools default to page_size=50. If you need more, raise
   `page_size` up to 200 BEFORE calling the same tool multiple times with
   different `page` values. The `(Showing N of T results — page P, hasMore=…)`
   footer tells you whether to fetch more pages.

3. RATE LIMITS ARE HANDLED FOR YOU. The client retries 429s with backoff, so
   transient throttling is invisible. But you can still exhaust quotas:
     - Main API: ~30 req/sec (soft cap, ST allows 60/sec)
     - Reporting API: ~3 req/min (ST hard cap is 5/min) — run_report is SLOW
   If you see a surfaced 429, reduce parallel tool calls or batch via filters.

4. REPORTING IS A LAST RESORT. Prefer domain-specific tools (list_invoices,
   list_jobs, list_payments) with date filters over `run_report`. Reports are
   slow, quota-constrained, and return schemas that vary by report_id. Only
   reach for `run_report` when no typed tool can answer the question (e.g.
   aggregated revenue breakdowns).

5. FIND-BEFORE-GET. `get_customer(customer_id)` needs an ID you don't know
   upfront. Use `list_customers(name=...)` to find the ID, then `get_customer`
   only if you need the full record.

6. ESCAPE HATCH. `servicetitan_api_call` exposes arbitrary endpoints (use
   `{tenant_id}` placeholder). Use when a typed tool doesn't cover the
   endpoint you need — e.g. unusual search params, POST/PATCH writes, or
   beta endpoints. Check ServiceTitan's API docs for the exact path.
""",
)

def _get_client() -> ServiceTitanClient:
    """Build a client from env vars. Raises if not configured."""
    app_key = os.environ.get("ST_APP_KEY", "")
    client_id = os.environ.get("ST_CLIENT_ID", "")
    client_secret = os.environ.get("ST_CLIENT_SECRET", "")
    tenant_id = os.environ.get("ST_TENANT_ID", "")
    if not all([app_key, client_id, client_secret, tenant_id]):
        missing = [
            k for k, v in {
                "ST_APP_KEY": app_key,
                "ST_CLIENT_ID": client_id,
                "ST_CLIENT_SECRET": client_secret,
                "ST_TENANT_ID": tenant_id,
            }.items() if not v
        ]
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Set them in your Claude Desktop config or .env file."
        )
    return ServiceTitanClient(app_key, client_id, client_secret, tenant_id)


def _fmt(data: dict | list, max_items: int = 25) -> str:
    """Pretty-print API response, truncating large lists and exposing pagination state.

    The trailer always includes page, pageSize, totalCount, and hasMore so the
    caller can decide whether to fetch more pages.
    """
    if isinstance(data, dict) and "data" in data:
        items = data["data"]
        total = data.get("totalCount", len(items))
        page = data.get("page", 1)
        page_size = data.get("pageSize", len(items))
        has_more = data.get("hasMore")
        if has_more is None:
            # Infer when ST didn't send it explicitly.
            has_more = (page * page_size) < total if total else False
        shown = min(len(items), max_items)
        if len(items) > max_items:
            items = items[:max_items]
        note = (
            f"\n\n(Showing {shown} of {total} results — "
            f"page {page}, pageSize {page_size}, hasMore={has_more})"
        )
        return json.dumps(items, indent=2, default=str) + note
    return json.dumps(data, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════
#  CRM — Customers, Contacts, Locations, Leads, Bookings
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_customers(
    page: int = 1,
    page_size: int = 50,
    name: str | None = None,
    active_only: bool = True,
) -> str:
    """Search/list customer accounts (the billing entity, not individuals).

    When to use: starting from a customer's name, or enumerating customers for
    bulk analysis. The `name` param filters server-side (substring match).
    When NOT: if you already have a numeric customer_id, call `get_customer`
    instead. For individuals tied to a customer, use `list_contacts`.
    Note: ST has no phone/email filter on this endpoint — for those, use
    `servicetitan_api_call` with a specific search endpoint or search via a
    broader `list_contacts` call and match locally.
    """
    client = _get_client()
    params: dict = {}
    if name:
        params["name"] = name
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("crm", "customers", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_customer(customer_id: int) -> str:
    """Get the full record for one customer by numeric ID.

    When to use: you already have an ID (e.g. from `list_customers`,
    `list_jobs`, or an invoice) and need contact blocks, addresses, balance,
    or custom fields.
    When NOT: if you only know the name, call `list_customers(name=...)`
    first — that returns enough detail for most lookups without the follow-up.
    """
    client = _get_client()
    data = await client.get_resource("crm", "customers", customer_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_locations(
    page: int = 1,
    page_size: int = 50,
    customer_id: int | None = None,
) -> str:
    """List service locations (physical addresses a customer owns).

    When to use: a customer has multiple sites and you need to pick one, or
    enumerating locations for route/territory analysis.
    When NOT: a customer with a single location — the address is already
    inside the `list_customers` / `get_customer` response.
    Tip: always pass `customer_id` when you have it — otherwise this returns
    every location in the tenant.
    """
    client = _get_client()
    params: dict = {}
    if customer_id:
        params["customerId"] = customer_id
    data = await client.list_resource("crm", "locations", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_location(location_id: int) -> str:
    """Get the full record for one service location by ID.

    When to use: you have a location_id (from a job or `list_locations`) and
    need site-specific details (zone, access notes, installed equipment link).
    When NOT: if looking up by address, use `list_locations` with
    `customer_id`.
    """
    client = _get_client()
    data = await client.get_resource("crm", "locations", location_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_leads(
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> str:
    """List leads/sales opportunities (not yet converted to jobs).

    When to use: pipeline questions ("how many open leads", "which leads
    stalled this month"). Filter by `status` (Open, Won, Lost, Dismissed).
    When NOT: for work that's already been booked as a job, use `list_jobs`.
    For booking call intake, use `list_bookings`.
    """
    client = _get_client()
    params: dict = {}
    if status:
        params["status"] = status
    data = await client.list_resource("crm", "leads", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_bookings(
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> str:
    """List inbound bookings (call-in / web-form requests awaiting scheduling).

    When to use: front-office / CSR questions — what came in today, which
    bookings are still Pending. Status values: Pending, Scheduled, Converted,
    Dismissed.
    When NOT: if the booking has already been converted to a scheduled job,
    query `list_jobs` with a date filter instead.
    """
    client = _get_client()
    params: dict = {}
    if status:
        params["status"] = status
    data = await client.list_resource("crm", "bookings", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_contacts(
    page: int = 1,
    page_size: int = 50,
    customer_id: int | None = None,
) -> str:
    """List individual contacts (phone/email) attached to customer accounts.

    When to use: finding who to call for a given customer_id, or enumerating
    contacts for a marketing export. Always pass `customer_id` when known —
    without it, this returns every contact in the tenant (many pages).
    When NOT: if the customer only has one primary contact, `get_customer`
    already includes it inline.
    """
    client = _get_client()
    params: dict = {}
    if customer_id:
        params["customerId"] = customer_id
    data = await client.list_resource("crm", "contacts", page, page_size, params)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  JOB PLANNING & MANAGEMENT — Jobs, Appointments, Projects
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_jobs(
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    customer_id: int | None = None,
    created_on_or_after: str | None = None,
    completed_on_or_after: str | None = None,
) -> str:
    """List jobs (scheduled or completed work orders). THE core work entity.

    When to use: "what jobs for customer X", "jobs completed last week",
    "open in-progress jobs". Always pass at least one filter (status,
    customer_id, or a date) — an unfiltered list is huge.
    When NOT: if you want the individual scheduling slots, use
    `list_appointments` (one job can have multiple appointments). For
    technician → appointment assignments, use `list_appointment_assignments`.

    status values: Scheduled, InProgress, Hold, Completed, Canceled.
    Date format: YYYY-MM-DD.
    """
    client = _get_client()
    params: dict = {}
    if status:
        params["jobStatus"] = status
    if customer_id:
        params["customerId"] = customer_id
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    if completed_on_or_after:
        params["completedOnOrAfter"] = completed_on_or_after
    data = await client.list_resource("jpm", "jobs", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_job(job_id: int) -> str:
    """Get full details for one job by ID.

    When to use: you have a job_id and need customer/location/appointment/
    invoice links plus custom fields.
    When NOT: `list_jobs` already returns enough for summary work — only
    call `get_job` when you need a specific field (tags, custom fields,
    full notes) not present in the list response.
    """
    client = _get_client()
    data = await client.get_resource("jpm", "jobs", job_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_appointments(
    page: int = 1,
    page_size: int = 50,
    starts_on_or_after: str | None = None,
    starts_on_or_before: str | None = None,
) -> str:
    """List appointment slots (a scheduled visit; one job can have many).

    When to use: "what's on the calendar tomorrow", "reschedule count this
    week". ALWAYS pass a date range — a full-tenant appointment list is
    very large.
    When NOT: to see who's assigned to run an appointment, use
    `list_appointment_assignments`. For non-customer-facing time blocks
    (meetings, training), use `list_non_job_appointments`.

    Date format: YYYY-MM-DD.
    """
    client = _get_client()
    params: dict = {}
    if starts_on_or_after:
        params["startsOnOrAfter"] = starts_on_or_after
    if starts_on_or_before:
        params["startsOnOrBefore"] = starts_on_or_before
    data = await client.list_resource("jpm", "appointments", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_job_types(page: int = 1, page_size: int = 200) -> str:
    """List all job-type definitions in ServiceTitan (configuration, not jobs).

    When to use: mapping a jobTypeId from a job record to its human name;
    enumerating service offerings.
    When NOT: don't call this to find actual jobs — use `list_jobs` instead.
    The list is small and static; cache the result in your working memory.
    """
    client = _get_client()
    data = await client.list_resource("jpm", "job-types", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_projects(
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> str:
    """List multi-job projects (umbrella entity that groups related jobs).

    When to use: commercial / construction contexts where one engagement
    spans multiple jobs. Usually empty for pure residential service shops.
    When NOT: residential-only tenants rarely use projects — check by
    running once without a filter to see if any exist.
    """
    client = _get_client()
    params: dict = {}
    if status:
        params["status"] = status
    data = await client.list_resource("jpm", "projects", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_project(project_id: int) -> str:
    """Get one project (multi-job umbrella) by ID.

    When to use: you have a project_id and need rollup details across its
    child jobs. See `list_projects` to discover IDs first.
    """
    client = _get_client()
    data = await client.get_resource("jpm", "projects", project_id)
    return json.dumps(data, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════
#  ACCOUNTING — Invoices, Payments, Bills
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_invoices(
    page: int = 1,
    page_size: int = 50,
    job_id: int | None = None,
    customer_id: int | None = None,
    created_on_or_after: str | None = None,
) -> str:
    """List invoices (AR — money owed by customers).

    When to use: "invoice for job X", "customer's invoices this quarter",
    "revenue since date Y". Always filter — unfiltered is huge. For
    aggregate revenue across BUs, prefer a `run_report` call if the
    breakdown matters (slower but pre-aggregated).
    When NOT: for payments RECEIVED (the cash side), use `list_payments`.
    For vendor bills (AP), use `list_inventory_bills`.

    Tip: the list response includes totals; `get_invoice` is only needed
    for line-item detail.
    """
    client = _get_client()
    params: dict = {}
    if job_id:
        params["jobId"] = job_id
    if customer_id:
        params["customerId"] = customer_id
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    data = await client.list_resource("accounting", "invoices", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_invoice(invoice_id: int) -> str:
    """Get one invoice by ID with full line items.

    When to use: you need each line (service, material, equipment) with
    quantities and prices — e.g. to answer "what was actually sold on this
    job".
    When NOT: for totals and customer/job linkage, `list_invoices` already
    has enough.
    """
    client = _get_client()
    data = await client.get_resource("accounting", "invoices", invoice_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_payments(
    page: int = 1,
    page_size: int = 50,
    created_on_or_after: str | None = None,
) -> str:
    """List payments received from customers (cash in).

    When to use: cash-flow questions, deposit reconciliation, "what came in
    yesterday". Use with `created_on_or_after` for date slices.
    When NOT: for amounts invoiced (billed but not necessarily collected),
    use `list_invoices`. Those are distinct accounting events.
    """
    client = _get_client()
    params: dict = {}
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    data = await client.list_resource("accounting", "payments", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_payment_types(page: int = 1, page_size: int = 200) -> str:
    """List configured payment types (Cash, Check, Visa, ACH, etc.) — static config.

    When to use: resolving a paymentTypeId from a `list_payments` row.
    When NOT: small, static; cache the result rather than calling repeatedly.
    """
    client = _get_client()
    data = await client.list_resource("accounting", "payment-types", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_inventory_bills(
    page: int = 1,
    page_size: int = 50,
    created_on_or_after: str | None = None,
) -> str:
    """List AP bills from vendors (inventory purchases, cost side).

    When to use: vendor spend analysis, AP aging.
    When NOT: for customer-facing revenue, use `list_invoices` /
    `list_payments`.
    """
    client = _get_client()
    params: dict = {}
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    data = await client.list_resource("accounting", "inventory-bills", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_journal_entries(
    page: int = 1,
    page_size: int = 50,
    created_on_or_after: str | None = None,
) -> str:
    """List GL journal entries posted from ServiceTitan.

    When to use: GL reconciliation against an external accounting system
    (QuickBooks, NetSuite). Bookkeeper-level detail.
    When NOT: for operational P&L questions, use reports or
    `list_invoices` / `list_payments`.
    """
    client = _get_client()
    params: dict = {}
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    data = await client.list_resource("accounting", "journal-entries", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_payment_terms(page: int = 1, page_size: int = 200) -> str:
    """List configured payment terms (Net 30, Due on Receipt, etc.) — static config.

    When to use: resolving paymentTermsId on an invoice.
    """
    client = _get_client()
    data = await client.list_resource("accounting", "payment-terms", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_tax_zones(page: int = 1, page_size: int = 200) -> str:
    """List configured sales tax zones — static config.

    When to use: resolving a taxZoneId or auditing tax configuration.
    """
    client = _get_client()
    data = await client.list_resource("accounting", "tax-zones", page, page_size)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  SALES & ESTIMATES
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_estimates(
    page: int = 1,
    page_size: int = 50,
    job_id: int | None = None,
    status: str | None = None,
    sold_after: str | None = None,
) -> str:
    """List estimates / proposals (pre-sale quotes).

    When to use: "open estimates", "estimates sold this month", or
    estimates for a given job. Status values: Open, Sold, Dismissed.
    `sold_after` (YYYY-MM-DD) is the right filter for close-rate analysis.
    When NOT: once an estimate is `Sold`, the work lives in `list_jobs`
    and the money in `list_invoices`.
    """
    client = _get_client()
    params: dict = {}
    if job_id:
        params["jobId"] = job_id
    if status:
        params["status"] = status
    if sold_after:
        params["soldAfter"] = sold_after
    data = await client.list_resource("sales", "estimates", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_estimate(estimate_id: int) -> str:
    """Get one estimate by ID with line items.

    When to use: you need the proposed line items / options on a quote
    (often multiple Good/Better/Best tiers).
    When NOT: for totals and status, `list_estimates` is enough.
    """
    client = _get_client()
    data = await client.get_resource("sales", "estimates", estimate_id)
    return json.dumps(data, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════
#  DISPATCH — Appointments, Technician Shifts, Zones
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_appointment_assignments(
    page: int = 1,
    page_size: int = 50,
    starts_on_or_after: str | None = None,
    starts_on_or_before: str | None = None,
) -> str:
    """List technician-to-appointment assignments (who is running which slot).

    When to use: dispatcher questions — "who's on this job", "tech's
    schedule today". Always pass a date range.
    When NOT: for the appointment slot itself (time, job, customer), use
    `list_appointments`. For planned shift availability (not actual dispatch),
    use `list_technician_shifts`.
    """
    client = _get_client()
    params: dict = {}
    if starts_on_or_after:
        params["startsOnOrAfter"] = starts_on_or_after
    if starts_on_or_before:
        params["startsOnOrBefore"] = starts_on_or_before
    data = await client.list_resource("dispatch", "appointment-assignments", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_technician_shifts(
    page: int = 1,
    page_size: int = 50,
    starts_on_or_after: str | None = None,
    starts_on_or_before: str | None = None,
) -> str:
    """List scheduled shifts (planned availability blocks, not dispatch).

    When to use: capacity planning — "who's working Saturday", shift-fill
    analysis.
    When NOT: for actual on-the-day dispatch, use
    `list_appointment_assignments`. For time-clock actuals, use
    `list_activities` (timesheets).
    """
    client = _get_client()
    params: dict = {}
    if starts_on_or_after:
        params["startsOnOrAfter"] = starts_on_or_after
    if starts_on_or_before:
        params["startsOnOrBefore"] = starts_on_or_before
    data = await client.list_resource("dispatch", "technician-shifts", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_zones(page: int = 1, page_size: int = 200) -> str:
    """List dispatch zones (geographic service areas) — static config.

    When to use: resolving zoneId from a location or technician record.
    Cache the result.
    """
    client = _get_client()
    data = await client.list_resource("dispatch", "zones", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_non_job_appointments(
    page: int = 1,
    page_size: int = 50,
    starts_on_or_after: str | None = None,
) -> str:
    """List internal time blocks that aren't customer work (training, meetings).

    When to use: understanding why a tech's calendar is "full" without
    billable work, capacity blocked for non-revenue reasons.
    When NOT: for customer-facing appointments, use `list_appointments`.
    """
    client = _get_client()
    params: dict = {}
    if starts_on_or_after:
        params["startsOnOrAfter"] = starts_on_or_after
    data = await client.list_resource("dispatch", "non-job-appointments", page, page_size, params)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  PRICEBOOK — Services, Materials, Equipment, Categories
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_pricebook_services(
    page: int = 1,
    page_size: int = 50,
    active_only: bool = True,
) -> str:
    """List pricebook SERVICES (labor / diagnostic line items techs sell).

    When to use: pricebook audit ("what services are we selling"), margin
    analysis, service catalog exports. Services typically have a fixed
    price + billable time component.
    When NOT: for physical parts, use `list_pricebook_materials`; for
    replaceable units (a water heater, a furnace), use
    `list_pricebook_equipment`.
    """
    client = _get_client()
    params: dict = {}
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("pricebook", "services", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_pricebook_materials(
    page: int = 1,
    page_size: int = 50,
    active_only: bool = True,
) -> str:
    """List pricebook MATERIALS (consumable parts: pipe, fittings, filters).

    When to use: parts catalog audit, vendor cost reconciliation.
    When NOT: for larger replaceable units (water heaters, HVAC
    equipment), use `list_pricebook_equipment`. For labor / diagnostic
    charges, use `list_pricebook_services`.
    """
    client = _get_client()
    params: dict = {}
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("pricebook", "materials", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_pricebook_equipment(
    page: int = 1,
    page_size: int = 50,
    active_only: bool = True,
) -> str:
    """List pricebook EQUIPMENT (big-ticket installable units: AC, furnace, water heater).

    When to use: replacement-project catalog, installation SKU lookup.
    When NOT: for consumable parts, use `list_pricebook_materials`.
    """
    client = _get_client()
    params: dict = {}
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("pricebook", "equipment", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_pricebook_categories(page: int = 1, page_size: int = 200) -> str:
    """List pricebook categories (folder structure in the pricebook) — static config.

    When to use: resolving categoryId to a human name, or browsing the
    catalog hierarchy.
    """
    client = _get_client()
    data = await client.list_resource("pricebook", "categories", page, page_size)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  INVENTORY — Purchase Orders, Warehouses, Vendors, Trucks
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_purchase_orders(
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> str:
    """List purchase orders (POs to vendors for parts/equipment).

    When to use: PO aging, open PO audit. Status values: Pending, Sent,
    PartiallyReceived, Received, Canceled.
    When NOT: for the received bills, use `list_inventory_bills`.
    """
    client = _get_client()
    params: dict = {}
    if status:
        params["status"] = status
    data = await client.list_resource("inventory", "purchase-orders", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_warehouses(page: int = 1, page_size: int = 200) -> str:
    """List inventory warehouses (storage locations) — static config.

    When to use: resolving warehouseId on a PO or stock record.
    """
    client = _get_client()
    data = await client.list_resource("inventory", "warehouses", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_inventory_vendors(page: int = 1, page_size: int = 200) -> str:
    """List vendors / suppliers — static config.

    When to use: resolving vendorId on a PO or bill.
    """
    client = _get_client()
    data = await client.list_resource("inventory", "vendors", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_trucks(page: int = 1, page_size: int = 200) -> str:
    """List trucks (mobile warehouses tied to technicians) — static config.

    When to use: fleet / rolling-stock audit, resolving truckId on an
    inventory transfer.
    """
    client = _get_client()
    data = await client.list_resource("inventory", "trucks", page, page_size)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  MEMBERSHIPS — Recurring Revenue
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_memberships(
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> str:
    """List customer memberships (maintenance agreements tied to a customer).

    When to use: churn / renewal questions, revenue tied to active plans.
    Status values: Active, Expired, Canceled, Deleted, Suspended.
    When NOT: for the membership CATALOG (plan definitions), use
    `list_membership_types`. For the recurring visits scheduled under each
    membership, use `list_recurring_services`.
    """
    client = _get_client()
    params: dict = {}
    if status:
        params["status"] = status
    data = await client.list_resource("memberships", "customer-memberships", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_membership_types(page: int = 1, page_size: int = 200) -> str:
    """List membership PLAN definitions (Gold, Silver, etc.) — static config.

    When to use: resolving membershipTypeId, auditing what plans exist.
    When NOT: for actual customer memberships, use `list_memberships`.
    """
    client = _get_client()
    data = await client.list_resource("memberships", "membership-types", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_recurring_services(
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List recurring services (tune-ups scheduled under memberships).

    When to use: "upcoming maintenance visits", tune-up schedule planning.
    When NOT: if you want the membership itself, use `list_memberships`.
    """
    client = _get_client()
    data = await client.list_resource("memberships", "recurring-services", page, page_size)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  SETTINGS — Employees, Technicians, Business Units, Tags
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_employees(
    page: int = 1,
    page_size: int = 200,
    active_only: bool = True,
) -> str:
    """List all employees (office staff + techs; superset of technicians).

    When to use: headcount questions, looking up anyone with a ServiceTitan
    login (CSRs, dispatchers, managers).
    When NOT: for field techs specifically (who can be dispatched on jobs),
    use `list_technicians` — it's a filtered subset with tech-only fields
    like skills and licenses.
    """
    client = _get_client()
    params: dict = {}
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("settings", "employees", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_employee(employee_id: int) -> str:
    """Get one employee by ID.

    When to use: you have an employeeId (from a job, payroll row, or
    activity) and need full contact / role / login details.
    """
    client = _get_client()
    data = await client.get_resource("settings", "employees", employee_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_technicians(
    page: int = 1,
    page_size: int = 200,
    active_only: bool = True,
) -> str:
    """List field technicians (subset of employees who run jobs).

    When to use: dispatch / crew questions, "how many active techs",
    technician performance rollups.
    When NOT: for non-tech staff (CSRs, managers), use `list_employees`.
    """
    client = _get_client()
    params: dict = {}
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("settings", "technicians", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_business_units(page: int = 1, page_size: int = 200) -> str:
    """List business units (BU — the top-level revenue buckets: HVAC, Plumbing, etc.).

    When to use: almost always needed when slicing revenue or jobs by BU —
    BU ids show up on nearly every job, invoice, and report. Cache the
    mapping (id → name) once per session.
    """
    client = _get_client()
    data = await client.list_resource("settings", "business-units", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_tag_types(page: int = 1, page_size: int = 200) -> str:
    """List tag-type definitions (color labels applied to customers/jobs/locations).

    When to use: resolving a tagTypeId to human label for filtering.
    """
    client = _get_client()
    data = await client.list_resource("settings", "tag-types", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_user_roles(page: int = 1, page_size: int = 200) -> str:
    """List user-role definitions (permission groups) — static config.

    When to use: auditing who has what permission level.
    """
    client = _get_client()
    data = await client.list_resource("settings", "user-roles", page, page_size)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  REPORTING — Dynamic Reports
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_report_categories() -> str:
    """Discover report CATEGORIES (first step in the reporting flow).

    When to use: you don't know which report to run yet. Categories are
    broad groupings (Marketing, Operations, Accounting, etc.).
    Next step: call `list_reports_in_category(category_id)` to find a
    specific report.

    Rate-limit note: lives under the reporting quota (~3 req/min in this
    server). Batch reporting work together.
    """
    client = _get_client()
    data = await client.list_resource("reporting", "report-categories", 1, 200)
    return _fmt(data)


@mcp.tool()
async def list_reports_in_category(category_id: int) -> str:
    """List reports inside one category, along with their parameter schemas.

    When to use: you've found a category and need to know (a) which
    report_id to run and (b) what parameters that report requires.
    Read the `parameters` block carefully — parameter names and value
    types vary per report.
    """
    client = _get_client()
    path = f"/reporting/v2/tenant/{client.tenant_id}/report-categories/{category_id}/reports"
    data = await client.get(path)
    return _fmt(data)


@mcp.tool()
async def run_report(
    report_id: int,
    category: str,
    parameters: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """Run a configured ServiceTitan report — LAST RESORT, slow + quota-constrained.

    When to use: pre-aggregated analytics that no typed tool can answer
    cheaply (e.g. total revenue by BU and month combined). Reports are
    the right call for exec dashboards.
    When NOT: if the question is "list X with filter Y", use the typed
    list_* tool — far faster and doesn't burn the 5/min reporting quota.

    Required flow:
      1. `list_report_categories` → pick a category (the slug goes in the
         `category` arg).
      2. `list_reports_in_category` → pick a report_id AND read its
         required parameters.
      3. Call this tool with the parameters as a JSON STRING, e.g.
         `parameters='{"From":"2024-01-01","To":"2024-12-31","BusinessUnitIds":[1,2,3]}'`

    Rate limits: reporting has its own bucket (~3 req/min). If you need
    multiple reports, space them out.
    """
    client = _get_client()

    body: dict = {"parameters": []}
    if parameters:
        try:
            param_map = json.loads(parameters)
        except json.JSONDecodeError:
            return "Error: 'parameters' must be a valid JSON string."
        body["parameters"] = [
            {"name": name, "value": value} for name, value in param_map.items()
        ]

    path = (
        f"/reporting/v2/tenant/{client.tenant_id}/report-category/{category}"
        f"/reports/{report_id}/data?page={page}&pageSize={page_size}"
    )
    data = await client.post(path, json_body=body)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  PAYROLL
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_payrolls(
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List payroll RUNS (each payroll cycle — pay period envelope).

    When to use: entry point to payroll data — find a payrollId, then
    drill into `list_employee_payrolls` or `list_gross_pay_items`.
    """
    client = _get_client()
    data = await client.list_resource("payroll", "payrolls", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_employee_payrolls(
    payroll_id: int,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """Per-employee summary for one payroll run (gross/net, hours, taxes).

    When to use: payroll review at the employee level for a specific
    period. Requires payroll_id from `list_payrolls`.
    When NOT: for the underlying line items (each shift, bonus, tip),
    use `list_gross_pay_items`.
    """
    client = _get_client()
    path = f"/payroll/v2/tenant/{client.tenant_id}/payrolls/{payroll_id}/employee-payrolls"
    data = await client.get(path, params={"page": page, "pageSize": page_size})
    return _fmt(data)


@mcp.tool()
async def list_gross_pay_items(
    payroll_id: int,
    employee_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List individual gross-pay line items (shift, bonus, commission, tip).

    When to use: auditing one employee's earnings composition, spiff
    verification, commission calculation.
    Tip: always pass `employee_id` when investigating one person — reduces
    result size considerably.
    """
    client = _get_client()
    params: dict = {"page": page, "pageSize": page_size}
    if employee_id:
        params["employeeId"] = employee_id
    path = f"/payroll/v2/tenant/{client.tenant_id}/gross-pay-items"
    data = await client.get(path, params=params)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  TELECOM — Calls
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_calls(
    page: int = 1,
    page_size: int = 50,
    created_on_or_after: str | None = None,
    created_on_or_before: str | None = None,
) -> str:
    """List telecom calls (inbound/outbound phone records with recordings).

    When to use: call-center analysis, CSR QA, lead-to-call mapping,
    after-hours missed-call audits. Always pass a date range — call
    volume is typically high.
    When NOT: for the resulting booking, use `list_bookings`.
    """
    client = _get_client()
    params: dict = {}
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    if created_on_or_before:
        params["createdOnOrBefore"] = created_on_or_before
    data = await client.list_resource("telecom", "calls", page, page_size, params)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  FORMS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_forms(page: int = 1, page_size: int = 50) -> str:
    """List form TEMPLATES (definitions: "Post-Install Checklist", etc.).

    When to use: form-config audit, resolving formId from a submission.
    When NOT: for actual completed forms, use `list_form_submissions`.
    """
    client = _get_client()
    data = await client.list_resource("forms", "forms", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_form_submissions(
    page: int = 1,
    page_size: int = 50,
    created_on_or_after: str | None = None,
) -> str:
    """List completed form submissions (techs filling in checklists on jobs).

    When to use: QA review — "did the tech complete the post-install
    form", compliance audits. Filter by date to stay manageable.
    When NOT: for the template definition, use `list_forms`.
    """
    client = _get_client()
    params: dict = {}
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    data = await client.list_resource("forms", "submissions", page, page_size, params)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  MARKETING — Campaigns
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_campaigns(page: int = 1, page_size: int = 200) -> str:
    """List marketing campaigns (the source attribution for jobs/leads).

    When to use: resolving campaignId on a job or lead to the channel
    name, or enumerating what channels exist. Small and static — cache it.
    When NOT: for spend data, use `list_campaign_costs`.
    """
    client = _get_client()
    data = await client.list_resource("marketing", "campaigns", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_campaign_costs(
    page: int = 1,
    page_size: int = 50,
    campaign_id: int | None = None,
) -> str:
    """List marketing campaign COSTS (spend per campaign per period).

    When to use: CPL / ROAS / marketing-efficiency questions. Pair with
    `list_invoices` filtered by campaign for revenue side.
    Always pass `campaign_id` when investigating one channel.
    """
    client = _get_client()
    params: dict = {}
    if campaign_id:
        params["campaignId"] = campaign_id
    data = await client.list_resource("marketing", "costs", page, page_size, params)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  EQUIPMENT SYSTEMS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_installed_equipment(
    page: int = 1,
    page_size: int = 50,
    location_id: int | None = None,
) -> str:
    """List equipment installed at customer locations (serial numbers, install dates).

    When to use: "what equipment is at this customer's home", warranty
    lookups, replacement-age targeting. Always pass `location_id` when
    you have it.
    When NOT: for pricebook definitions (what we sell), use
    `list_pricebook_equipment`.
    """
    client = _get_client()
    params: dict = {}
    if location_id:
        params["locationId"] = location_id
    data = await client.list_resource("equipment-systems", "installed-equipment", page, page_size, params)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  TASK MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_tasks(
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List internal tasks (the follow-up/todo module, not field jobs).

    When to use: office follow-up audit — "callbacks owed", "open
    complaints to resolve".
    When NOT: for actual field work, use `list_jobs`. These are separate
    entities with a separate lifecycle.
    """
    client = _get_client()
    data = await client.list_resource("task-management", "tasks", page, page_size)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  TIMESHEETS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_activities(
    page: int = 1,
    page_size: int = 50,
    starts_on_or_after: str | None = None,
) -> str:
    """List tech timesheet activities (drive time, job time, breaks — actual clock).

    When to use: labor cost analysis, tech productivity (wrench time /
    drive time ratio), timesheet audits.
    When NOT: for the SCHEDULED shifts, use `list_technician_shifts`.
    For payroll-level aggregates, use `list_gross_pay_items`.
    """
    client = _get_client()
    params: dict = {}
    if starts_on_or_after:
        params["startsOnOrAfter"] = starts_on_or_after
    data = await client.list_resource("timesheets", "activities", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_activity_categories(page: int = 1, page_size: int = 200) -> str:
    """List timesheet activity categories (Drive, Job, Break, Training) — static config.

    When to use: resolving activityCategoryId to a human label.
    """
    client = _get_client()
    data = await client.list_resource("timesheets", "activity-codes", page, page_size)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  GENERIC / POWER-USER
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def servicetitan_api_call(
    method: str,
    path: str,
    query_params: str | None = None,
    body: str | None = None,
) -> str:
    """ESCAPE HATCH: raw HTTP call to any ServiceTitan API endpoint.

    When to use: the typed tools above don't cover what you need —
    unusual search params, POST/PATCH/PUT writes, or endpoints from the
    ST docs that aren't wrapped here (e.g. `/crm/v2/tenant/{tenant_id}/
    customers/{id}/contacts`, scheduling booking conversions, etc.).
    When NOT: for anything a typed tool already covers — typed tools
    validate params, return clean JSON, and the docstring tells the LLM
    when to use them. Reach for this only after checking.

    Args:
      method: GET | POST | PATCH | PUT
      path: full API path starting with /. Use the literal string
            `{tenant_id}` as a placeholder — it's substituted at runtime.
            Example: `/crm/v2/tenant/{tenant_id}/customers?phone=555-0100`
      query_params: JSON STRING of query params, e.g. `'{"pageSize":100}'`
      body: JSON STRING of request body for POST/PATCH/PUT

    Still goes through the same rate-limit + retry layer as typed tools
    (including the reporting bucket for `/reporting/*` paths).
    """
    client = _get_client()
    # Replace tenant placeholder
    resolved_path = path.replace("{tenant_id}", client.tenant_id)

    params = None
    if query_params:
        try:
            params = json.loads(query_params)
        except json.JSONDecodeError:
            return "Error: query_params must be valid JSON."

    json_body = None
    if body:
        try:
            json_body = json.loads(body)
        except json.JSONDecodeError:
            return "Error: body must be valid JSON."

    try:
        method_upper = method.upper()
        if method_upper == "GET":
            data = await client.get(resolved_path, params=params)
        elif method_upper == "POST":
            data = await client.post(resolved_path, json_body=json_body)
        elif method_upper == "PATCH":
            data = await client.patch(resolved_path, json_body=json_body)
        elif method_upper == "PUT":
            data = await client.put(resolved_path, json_body=json_body)
        else:
            return f"Unsupported method: {method}. Use GET, POST, PATCH, or PUT."
        return _fmt(data)
    except Exception as e:
        return f"API Error: {e}\n{traceback.format_exc()}"


# ═══════════════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════

def main():
    """Run the MCP server via stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
