-- Premier Brushworks JobHub Control Centre tables
-- Run this in Supabase SQL Editor if the app does not create the new tables automatically.

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
