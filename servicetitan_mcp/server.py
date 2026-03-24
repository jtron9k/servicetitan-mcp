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
    instructions="Connect Claude to ServiceTitan — jobs, customers, invoices, estimates, dispatch, reporting, and more.",
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
    """Pretty-print API response, truncating large lists."""
    if isinstance(data, dict) and "data" in data:
        items = data["data"]
        total = data.get("totalCount", len(items))
        page = data.get("page", 1)
        if len(items) > max_items:
            items = items[:max_items]
            note = f"\n\n(Showing {max_items} of {total} results — page {page})"
        else:
            note = f"\n\n(Showing {len(items)} of {total} results — page {page})"
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
    """List customers. Optionally filter by name or active status."""
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
    """Get a single customer by ID."""
    client = _get_client()
    data = await client.get_resource("crm", "customers", customer_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_locations(
    page: int = 1,
    page_size: int = 50,
    customer_id: int | None = None,
) -> str:
    """List service locations. Optionally filter by customer."""
    client = _get_client()
    params: dict = {}
    if customer_id:
        params["customerId"] = customer_id
    data = await client.list_resource("crm", "locations", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_location(location_id: int) -> str:
    """Get a single service location by ID."""
    client = _get_client()
    data = await client.get_resource("crm", "locations", location_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_leads(
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> str:
    """List leads/opportunities. Optionally filter by status."""
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
    """List bookings. Optionally filter by status (Pending, Scheduled, etc.)."""
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
    """List customer contacts. Optionally filter by customer ID."""
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
    """List jobs. Filter by status, customer, or date range.

    status examples: Scheduled, InProgress, Completed, Canceled
    Date format: YYYY-MM-DD
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
    """Get full details for a single job by ID."""
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
    """List appointments. Filter by date range (YYYY-MM-DD)."""
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
    """List all job types configured in ServiceTitan."""
    client = _get_client()
    data = await client.list_resource("jpm", "job-types", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_projects(
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> str:
    """List projects. Optionally filter by status."""
    client = _get_client()
    params: dict = {}
    if status:
        params["status"] = status
    data = await client.list_resource("jpm", "projects", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_project(project_id: int) -> str:
    """Get a single project by ID."""
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
    """List invoices. Filter by job, customer, or creation date."""
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
    """Get a single invoice by ID including line items."""
    client = _get_client()
    data = await client.get_resource("accounting", "invoices", invoice_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_payments(
    page: int = 1,
    page_size: int = 50,
    created_on_or_after: str | None = None,
) -> str:
    """List payments received."""
    client = _get_client()
    params: dict = {}
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    data = await client.list_resource("accounting", "payments", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_payment_types(page: int = 1, page_size: int = 200) -> str:
    """List payment types (Cash, Check, Credit Card, etc.)."""
    client = _get_client()
    data = await client.list_resource("accounting", "payment-types", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_inventory_bills(
    page: int = 1,
    page_size: int = 50,
    created_on_or_after: str | None = None,
) -> str:
    """List AP / inventory bills."""
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
    """List journal entries."""
    client = _get_client()
    params: dict = {}
    if created_on_or_after:
        params["createdOnOrAfter"] = created_on_or_after
    data = await client.list_resource("accounting", "journal-entries", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_payment_terms(page: int = 1, page_size: int = 200) -> str:
    """List payment terms (Net 30, Due on Receipt, etc.)."""
    client = _get_client()
    data = await client.list_resource("accounting", "payment-terms", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_tax_zones(page: int = 1, page_size: int = 200) -> str:
    """List tax zones."""
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
    """List estimates. Filter by job, status (Open, Sold, Dismissed), or sold date."""
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
    """Get a single estimate by ID with line items."""
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
    """List dispatch appointment assignments (technician → appointment)."""
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
    """List technician shift schedules."""
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
    """List dispatch zones."""
    client = _get_client()
    data = await client.list_resource("dispatch", "zones", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_non_job_appointments(
    page: int = 1,
    page_size: int = 50,
    starts_on_or_after: str | None = None,
) -> str:
    """List non-job appointments (meetings, training, etc.)."""
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
    """List pricebook services (line items techs sell)."""
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
    """List pricebook materials."""
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
    """List pricebook equipment."""
    client = _get_client()
    params: dict = {}
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("pricebook", "equipment", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_pricebook_categories(page: int = 1, page_size: int = 200) -> str:
    """List pricebook categories."""
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
    """List purchase orders. Optionally filter by status."""
    client = _get_client()
    params: dict = {}
    if status:
        params["status"] = status
    data = await client.list_resource("inventory", "purchase-orders", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_warehouses(page: int = 1, page_size: int = 200) -> str:
    """List inventory warehouses."""
    client = _get_client()
    data = await client.list_resource("inventory", "warehouses", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_inventory_vendors(page: int = 1, page_size: int = 200) -> str:
    """List inventory vendors/suppliers."""
    client = _get_client()
    data = await client.list_resource("inventory", "vendors", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_trucks(page: int = 1, page_size: int = 200) -> str:
    """List trucks (vehicle inventory)."""
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
    """List customer memberships. Optionally filter by status (Active, Expired, Canceled)."""
    client = _get_client()
    params: dict = {}
    if status:
        params["status"] = status
    data = await client.list_resource("memberships", "customer-memberships", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_membership_types(page: int = 1, page_size: int = 200) -> str:
    """List membership type definitions."""
    client = _get_client()
    data = await client.list_resource("memberships", "membership-types", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_recurring_services(
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List recurring services."""
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
    """List employees. Optionally filter to active only."""
    client = _get_client()
    params: dict = {}
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("settings", "employees", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def get_employee(employee_id: int) -> str:
    """Get a single employee by ID."""
    client = _get_client()
    data = await client.get_resource("settings", "employees", employee_id)
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def list_technicians(
    page: int = 1,
    page_size: int = 200,
    active_only: bool = True,
) -> str:
    """List technicians."""
    client = _get_client()
    params: dict = {}
    if active_only:
        params["active"] = "True"
    data = await client.list_resource("settings", "technicians", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_business_units(page: int = 1, page_size: int = 200) -> str:
    """List business units."""
    client = _get_client()
    data = await client.list_resource("settings", "business-units", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_tag_types(page: int = 1, page_size: int = 200) -> str:
    """List tag types used for categorization."""
    client = _get_client()
    data = await client.list_resource("settings", "tag-types", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_user_roles(page: int = 1, page_size: int = 200) -> str:
    """List user roles."""
    client = _get_client()
    data = await client.list_resource("settings", "user-roles", page, page_size)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  REPORTING — Dynamic Reports
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_report_categories() -> str:
    """List available report categories."""
    client = _get_client()
    data = await client.list_resource("reporting", "report-categories", 1, 200)
    return _fmt(data)


@mcp.tool()
async def list_reports_in_category(category_id: int) -> str:
    """List reports within a specific report category."""
    client = _get_client()
    path = f"/reporting/v2/tenant/{client.tenant_id}/report-categories/{category_id}/reports"
    data = await client.get(path)
    return _fmt(data)


@mcp.tool()
async def run_report(
    report_id: int,
    parameters: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """Run a ServiceTitan report by ID.

    parameters: JSON string of report parameters, e.g. '{"From":"2024-01-01","To":"2024-12-31"}'
    """
    client = _get_client()
    params: dict = {"page": page, "pageSize": page_size}
    if parameters:
        try:
            report_params = json.loads(parameters)
            params["parameters"] = json.dumps(report_params)
        except json.JSONDecodeError:
            return "Error: 'parameters' must be a valid JSON string."
    path = f"/reporting/v2/tenant/{client.tenant_id}/dynamic-value-sets/{report_id}"
    data = await client.get(path, params=params)
    return _fmt(data)


# ═══════════════════════════════════════════════════════════════════════
#  PAYROLL
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_payrolls(
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List payroll runs."""
    client = _get_client()
    data = await client.list_resource("payroll", "payrolls", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_employee_payrolls(
    payroll_id: int,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List employee-level payroll details for a specific payroll run."""
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
    """List gross pay line items for a payroll run."""
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
    """List phone calls. Filter by date range (YYYY-MM-DD)."""
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
    """List form templates."""
    client = _get_client()
    data = await client.list_resource("forms", "forms", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_form_submissions(
    page: int = 1,
    page_size: int = 50,
    created_on_or_after: str | None = None,
) -> str:
    """List form submissions."""
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
    """List marketing campaigns."""
    client = _get_client()
    data = await client.list_resource("marketing", "campaigns", page, page_size)
    return _fmt(data)


@mcp.tool()
async def list_campaign_costs(
    page: int = 1,
    page_size: int = 50,
    campaign_id: int | None = None,
) -> str:
    """List marketing campaign costs."""
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
    """List installed equipment at customer locations."""
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
    """List tasks in ServiceTitan task management."""
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
    """List timesheet activities."""
    client = _get_client()
    params: dict = {}
    if starts_on_or_after:
        params["startsOnOrAfter"] = starts_on_or_after
    data = await client.list_resource("timesheets", "activities", page, page_size, params)
    return _fmt(data)


@mcp.tool()
async def list_activity_categories(page: int = 1, page_size: int = 200) -> str:
    """List timesheet activity categories (Drive, Job, Break, etc.)."""
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
    """Make an arbitrary ServiceTitan API call for advanced use.

    method: GET, POST, PATCH, PUT
    path: Full path starting with / e.g. /crm/v2/tenant/{tenant_id}/customers
         Use {tenant_id} as a placeholder — it will be replaced automatically.
    query_params: JSON string of query parameters
    body: JSON string for request body (POST/PATCH/PUT)
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
