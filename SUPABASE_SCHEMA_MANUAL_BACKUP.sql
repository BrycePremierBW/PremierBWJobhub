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
