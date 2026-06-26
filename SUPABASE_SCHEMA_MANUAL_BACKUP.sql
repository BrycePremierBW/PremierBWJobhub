-- PB JobHub Supabase schema
-- Usually the app creates these tables automatically on first run.
-- Use this in Supabase SQL Editor only if you need to create tables manually.

CREATE TABLE IF NOT EXISTS builders_clients (
    id SERIAL PRIMARY KEY,
    type TEXT,
    name TEXT UNIQUE,
    contact_name TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    qbcc TEXT,
    abn TEXT,
    terms TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    job_no TEXT UNIQUE,
    job_name TEXT,
    builder_client_id INTEGER REFERENCES builders_clients(id),
    site_address TEXT,
    status TEXT,
    leading_hand TEXT,
    start_date TEXT,
    end_date TEXT,
    contract_value REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    product_code TEXT UNIQUE,
    product_name TEXT,
    supplier TEXT,
    unit TEXT,
    price_ex_gst REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS employees (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE,
    role TEXT,
    phone TEXT,
    base_hourly_rate REAL,
    rate_plus_10 REAL,
    status TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS material_entries (
    id SERIAL PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    product_id INTEGER REFERENCES products(id),
    qty_required REAL,
    qty_received REAL,
    date_ordered TEXT,
    supplier TEXT,
    notes TEXT
);


CREATE TABLE IF NOT EXISTS timesheet_entries (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    work_date TEXT,
    start_time TEXT,
    finish_time TEXT,
    break_minutes REAL DEFAULT 0,
    total_hours REAL DEFAULT 0,
    work_type TEXT,
    submitted_by TEXT,
    submitted_at TEXT,
    status TEXT DEFAULT 'Submitted',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS wage_entries (
    id SERIAL PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    employee_id INTEGER REFERENCES employees(id),
    work_date TEXT,
    hours REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS equipment_entries (
    id SERIAL PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    equipment_name TEXT,
    qty_required REAL,
    qty_taken REAL,
    qty_returned REAL,
    date_out TEXT,
    date_in TEXT,
    taken_by TEXT,
    returned_by TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS equipment_checklist_items (
    id SERIAL PRIMARY KEY,
    category TEXT,
    item_name TEXT UNIQUE,
    default_qty REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS equipment_checklist_records (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    checklist_item_id INTEGER NOT NULL REFERENCES equipment_checklist_items(id),
    qty_required REAL DEFAULT 0,
    qty_taken REAL DEFAULT 0,
    qty_returned REAL DEFAULT 0,
    is_required INTEGER DEFAULT 0,
    is_packed INTEGER DEFAULT 0,
    is_returned INTEGER DEFAULT 0,
    date_out TEXT,
    date_in TEXT,
    taken_by TEXT,
    returned_by TEXT,
    condition_out TEXT,
    condition_in TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS imported_material_entries (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    product TEXT,
    colour TEXT,
    qty_required TEXT,
    qty_loaded TEXT,
    source_file TEXT,
    imported_at TEXT,
    notes TEXT
);


CREATE TABLE IF NOT EXISTS estimate_working_sheets (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    estimate_no TEXT,
    estimate_date TEXT,
    revision TEXT,
    status TEXT,
    labour_hours REAL DEFAULT 0,
    labour_rate REAL DEFAULT 0,
    material_allowance REAL DEFAULT 0,
    access_equipment_allowance REAL DEFAULT 0,
    subcontractor_allowance REAL DEFAULT 0,
    sundries_allowance REAL DEFAULT 0,
    margin_percent REAL DEFAULT 0,
    contingency_percent REAL DEFAULT 0,
    gst_percent REAL DEFAULT 10,
    total_ex_gst REAL DEFAULT 0,
    gst_amount REAL DEFAULT 0,
    total_inc_gst REAL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS estimate_line_items (
    id SERIAL PRIMARY KEY,
    estimate_id INTEGER NOT NULL REFERENCES estimate_working_sheets(id),
    section TEXT,
    item_description TEXT,
    qty REAL DEFAULT 0,
    unit TEXT,
    unit_rate REAL DEFAULT 0,
    line_total REAL DEFAULT 0,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_timesheet_entries_job_id ON timesheet_entries(job_id);
CREATE INDEX IF NOT EXISTS idx_estimate_working_sheets_job_id ON estimate_working_sheets(job_id);
CREATE INDEX IF NOT EXISTS idx_estimate_line_items_estimate_id ON estimate_line_items(estimate_id);

CREATE TABLE IF NOT EXISTS job_photos (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    photo_name TEXT,
    photo_type TEXT,
    photo_data TEXT,
    category TEXT,
    caption TEXT,
    uploaded_by TEXT,
    uploaded_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS app_users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE,
    password_hash TEXT,
    role TEXT,
    employee_id INTEGER REFERENCES employees(id),
    active INTEGER DEFAULT 1,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT
);

-- Helpful performance indexes

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_builder_client_id ON jobs(builder_client_id);
CREATE INDEX IF NOT EXISTS idx_wage_entries_job_id ON wage_entries(job_id);
CREATE INDEX IF NOT EXISTS idx_wage_entries_employee_id ON wage_entries(employee_id);
CREATE INDEX IF NOT EXISTS idx_material_entries_job_id ON material_entries(job_id);
CREATE INDEX IF NOT EXISTS idx_equipment_entries_job_id ON equipment_entries(job_id);
CREATE INDEX IF NOT EXISTS idx_job_photos_job_id ON job_photos(job_id);



-- PB Control Centre tables
CREATE TABLE IF NOT EXISTS job_budgets (
    id SERIAL PRIMARY KEY,
    job_id INTEGER UNIQUE REFERENCES jobs(id),
    quoted_labour_hours REAL DEFAULT 0,
    quoted_labour_cost REAL DEFAULT 0,
    quoted_materials REAL DEFAULT 0,
    quoted_access_equipment REAL DEFAULT 0,
    quoted_subcontractors REAL DEFAULT 0,
    quoted_sundries REAL DEFAULT 0,
    target_gp_percent REAL DEFAULT 35,
    locked_at TEXT,
    locked_by TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS job_variations (
    id SERIAL PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    variation_no TEXT,
    description TEXT,
    reason TEXT,
    amount_ex_gst REAL DEFAULT 0,
    status TEXT DEFAULT 'Draft',
    sent_date TEXT,
    approved_date TEXT,
    approved_by TEXT,
    notes TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS invoice_claims (
    id SERIAL PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    claim_no TEXT,
    description TEXT,
    amount_ex_gst REAL DEFAULT 0,
    invoice_date TEXT,
    due_date TEXT,
    paid_date TEXT,
    status TEXT DEFAULT 'Draft',
    notes TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS staff_schedule (
    id SERIAL PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    employee_id INTEGER REFERENCES employees(id),
    schedule_date TEXT,
    start_time TEXT,
    finish_time TEXT,
    site_role TEXT,
    notes TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_budgets_job_id ON job_budgets(job_id);
CREATE INDEX IF NOT EXISTS idx_job_variations_job_id ON job_variations(job_id);
CREATE INDEX IF NOT EXISTS idx_invoice_claims_job_id ON invoice_claims(job_id);
CREATE INDEX IF NOT EXISTS idx_staff_schedule_job_id ON staff_schedule(job_id);
CREATE INDEX IF NOT EXISTS idx_staff_schedule_employee_id ON staff_schedule(employee_id);
CREATE INDEX IF NOT EXISTS idx_staff_schedule_date ON staff_schedule(schedule_date);
