# ServiceTitan MCP Server

Connect Claude to ServiceTitan via API. 60 tools covering CRM, Jobs, Accounting, Estimates, Dispatch, Pricebook, Inventory, Memberships, Payroll, Marketing, Reporting, Timesheets, Telecom, and more.

Built for any ServiceTitan customer — each tenant manages its own credentials independently.

## Credits

This project is a fork of [glassdoc/servicetitan-mcp](https://github.com/glassdoc/servicetitan-mcp), created by the [glassdoc](https://github.com/glassdoc) team. All credit for the original design and the bulk of the implementation goes to them. This fork adds fixes for production auth, improved error surfacing, and a corrected `run_report` endpoint.

## Quick Start

```bash
# Clone and install
git clone https://github.com/glassdoc/servicetitan-mcp.git
cd servicetitan-mcp
pip install -e .
```

## Configuration

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, or `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "servicetitan": {
      "command": "python",
      "args": ["-m", "servicetitan_mcp.server"],
      "env": {
        "ST_APP_KEY": "your-app-key",
        "ST_CLIENT_ID": "cid.your-client-id",
        "ST_CLIENT_SECRET": "cs1.your-client-secret",
        "ST_TENANT_ID": "your-tenant-id"
      }
    }
  }
}
```

For multiple tenants, add separate server entries (see `claude_desktop_config_example.json`).

## Getting Your Credentials

1. Go to **Settings > Integrations > API Application Access** in your ServiceTitan tenant
2. Find the **Claude MCP Integration** app and click **Connect**
3. Fill in the restriction fields (booking_provider, gps_provider, report_category)
4. Accept Terms and Conditions
5. Copy your **Client ID** and generate a **Client Secret**
6. Your **Tenant ID** is shown in the top-right corner of any ServiceTitan page
7. Your **App Key** is provided by ServiceTitan when your integration app is registered — obtain it directly from your own ServiceTitan account/integration setup

## Available Tools (60)

### CRM
- `list_customers` — Search and list customers
- `get_customer` — Get customer details by ID
- `list_locations` — List service locations
- `get_location` — Get location details
- `list_leads` — List leads/opportunities
- `list_bookings` — List bookings
- `list_contacts` — List customer contacts

### Job Planning & Management
- `list_jobs` — List jobs with filters (status, customer, date range)
- `get_job` — Get full job details
- `list_appointments` — List appointments by date range
- `list_job_types` — List configured job types
- `list_projects` — List projects
- `get_project` — Get project details

### Accounting
- `list_invoices` — List invoices (filter by job, customer, date)
- `get_invoice` — Get invoice with line items
- `list_payments` — List payments received
- `list_payment_types` — List payment types
- `list_inventory_bills` — List AP/inventory bills
- `list_journal_entries` — List journal entries
- `list_payment_terms` — List payment terms
- `list_tax_zones` — List tax zones

### Sales & Estimates
- `list_estimates` — List estimates (filter by job, status, sold date)
- `get_estimate` — Get estimate with line items

### Dispatch
- `list_appointment_assignments` — Technician-to-appointment assignments
- `list_technician_shifts` — Shift schedules
- `list_zones` — Dispatch zones
- `list_non_job_appointments` — Meetings, training, etc.

### Pricebook
- `list_pricebook_services` — Sellable services
- `list_pricebook_materials` — Materials
- `list_pricebook_equipment` — Equipment
- `list_pricebook_categories` — Categories

### Inventory
- `list_purchase_orders` — Purchase orders
- `list_warehouses` — Warehouses
- `list_inventory_vendors` — Vendors/suppliers
- `list_trucks` — Vehicle inventory

### Memberships
- `list_memberships` — Customer memberships
- `list_membership_types` — Membership type definitions
- `list_recurring_services` — Recurring services

### Settings
- `list_employees` — Employees
- `get_employee` — Employee details
- `list_technicians` — Technicians
- `list_business_units` — Business units
- `list_tag_types` — Tag types
- `list_user_roles` — User roles

### Reporting
- `list_report_categories` — Report categories
- `list_reports_in_category` — Reports in a category
- `run_report` — Run a dynamic report

### Payroll
- `list_payrolls` — Payroll runs
- `list_employee_payrolls` — Employee payroll details
- `list_gross_pay_items` — Gross pay line items

### Telecom
- `list_calls` — Phone calls

### Forms
- `list_forms` — Form templates
- `list_form_submissions` — Submitted forms

### Marketing
- `list_campaigns` — Marketing campaigns
- `list_campaign_costs` — Campaign costs

### Equipment, Tasks, Timesheets
- `list_installed_equipment` — Installed equipment at locations
- `list_tasks` — Task management tasks
- `list_activities` — Timesheet activities
- `list_activity_categories` — Activity categories

### Power User
- `servicetitan_api_call` — Make any arbitrary API call (GET/POST/PATCH/PUT)

## Authentication

Uses OAuth 2.0 Client Credentials flow. Tokens auto-refresh every 15 minutes. No user interaction required.

## Common Workflows

Recipes for frequent questions, with the tool sequence Claude should pick.

### Find a customer by name (then drill down)
1. `list_customers(name="Smith")` → returns candidates with IDs, contact blocks, primary location.
2. Optional `get_customer(customer_id=...)` only if you need custom fields not in the list response.

### Find a customer by phone or email
The `list_customers` endpoint has no phone/email filter. Two options:
- `servicetitan_api_call("GET", "/crm/v2/tenant/{tenant_id}/customers", query_params='{"phone":"555-0100"}')` — works if ST exposes `phone` on this tenant.
- Fallback: `list_contacts` with date range and match locally.

### Full job timeline for a customer
1. `list_customers(name=...)` → customerId
2. `list_jobs(customer_id=...)` → jobIds (status, dates)
3. For a specific job: `list_appointments(starts_on_or_after=...)` filtered to that job, or `get_job(job_id=...)` for full detail
4. `list_invoices(job_id=...)` for billing side
5. `list_estimates(job_id=...)` if a quote exists

### Revenue for a date range
- Simple total: `list_invoices(created_on_or_after="2024-01-01")` — sum client-side.
- Broken down by Business Unit or campaign: use `run_report` (reporting quota applies, slower). Step through `list_report_categories` → `list_reports_in_category` to find the right report and its parameter schema first.

### Technician's schedule today
1. `list_appointment_assignments(starts_on_or_after="2024-04-22", starts_on_or_before="2024-04-23")`
2. Cross-reference with `list_technicians` (cache this — small and static) to map IDs → names.

### Open estimates by business unit
1. `list_business_units()` — cache the BU ID → name map.
2. `list_estimates(status="Open")` — note `businessUnitId` per estimate, group client-side.

### Membership renewals due this month
1. `list_memberships(status="Active")` — look at `nextScheduledDate` / `to` expiry.
2. For the tune-up visits themselves: `list_recurring_services()`.

### Marketing channel ROI
1. `list_campaigns()` — cache campaign ID → name.
2. `list_campaign_costs(campaign_id=...)` for spend.
3. `list_invoices(created_on_or_after=...)` then group by each job's campaignId for revenue attribution.

## Rate limits and error handling

ServiceTitan enforces:
- **Main API:** 60 requests/second per app per tenant.
- **Reporting API:** 5 requests/minute per report per tenant.

This server layers the following in front of those limits:

- **Two token buckets** keyed by path. Main API paths use one, `/reporting/*` uses a separate, stricter one. Defaults are `30 rps` and `3 rpm` (half the hard caps) to leave headroom for bursts. Override via env vars:
  - `ST_RATE_LIMIT_RPS` (default `30`)
  - `ST_REPORTING_RPM` (default `3`)
  - `ST_MAX_CONCURRENCY` (default `10`) — cap on in-flight requests
- **Automatic retry** on `429`, `502`, `503`, `504`:
  - Honors `Retry-After` header if the server sends one.
  - Otherwise exponential backoff: 1s → 2s → 4s, max 3 retries.
  - On final failure, the HTTP error (including response body) is raised so Claude sees what went wrong.
- **Shared connection pool**: one `httpx.AsyncClient` is reused across tool calls to avoid per-request TCP handshakes.

Retries log to stderr at the `[servicetitan-mcp]` prefix — tail the Claude Desktop logs to observe backoff behavior when investigating slowness.

## License

MIT
