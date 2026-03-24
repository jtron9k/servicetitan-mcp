# ServiceTitan MCP Server

Connect Claude to ServiceTitan via API. 60 tools covering CRM, Jobs, Accounting, Estimates, Dispatch, Pricebook, Inventory, Memberships, Payroll, Marketing, Reporting, Timesheets, Telecom, and more.

Built for any ServiceTitan customer — each tenant manages its own credentials independently.

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
7. The **App Key** is: `REDACTED`

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

## Rate Limits

- Regular APIs: 60 calls/second per app per tenant
- Reporting APIs: 5 requests/minute per report per tenant

## License

MIT
