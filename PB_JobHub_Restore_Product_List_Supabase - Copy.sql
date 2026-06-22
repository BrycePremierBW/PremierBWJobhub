-- Premier Brushworks JobHub - Restore Product List
-- Run this in Supabase SQL Editor if the product list is missing.

CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    product_code TEXT UNIQUE,
    product_name TEXT,
    supplier TEXT,
    unit TEXT,
    price_ex_gst REAL,
    notes TEXT
);

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00001', 'Coverplus Interior L/S White', 'Haymes', '', 168.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00002', 'Elite Ceiling Toned White, 15L', 'Haymes', '15L', 90.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00003', 'Elite Ceiling White, 15L', 'Haymes', '15L', 90.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00004', 'Elite Interior Low Sheen White', 'Haymes', '', 118.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00005', 'Elite Interior Matt White, 15L', 'Haymes', '15L', 125.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00006', 'Elite Acrylic Sealer Undercoat', 'Haymes', '', 105.36, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00007', 'Elite Quick Dry Primer Undercoat', 'Haymes', '', 123.55, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00008', 'Expressions Low Sheen DKT, 4L', 'Haymes', '4L', 74.13, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00009', 'Expressions Low Sheen EDT, 4L', 'Haymes', '4L', 74.13, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00010', 'Expressions Low Sheen UDT, 4L', 'Haymes', '4L', 74.13, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00011', 'Expressions Low Sheen White', 'Haymes', '', 107.48, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00012', 'Expressions Low Sheen White', 'Haymes', '', 145.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00013', 'Expressions Low Sheen White, 4L', 'Haymes', '4L', 67.26, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00014', 'Solashield Low Sheen DKT, 10L', 'Haymes', '10L', 115.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00015', 'Solashield Low Sheen DKT, 15L', 'Haymes', '15L', 160.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00016', 'Solashield Low Sheen DKT, 4L', 'Haymes', '4L', 73.55, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00017', 'Solashield Low Sheen EDT, 10L', 'Haymes', '10L', 115.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00018', 'Solashield Low Sheen EDT, 15L', 'Haymes', '15L', 160.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00019', 'Solashield Low Sheen EDT, 4L', 'Haymes', '4L', 73.55, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00020', 'Solashield Low Sheen UDT, 10L', 'Haymes', '10L', 115.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00021', 'Solashield Low Sheen UDT, 15L', 'Haymes', '15L', 160.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00022', 'Solashield Low Sheen UDT, 4L', 'Haymes', '4L', 73.55, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00023', 'Solashield Low Sheen White, 10L', 'Haymes', '10L', 107.42, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00024', 'Solashield Low Sheen White, 15L', 'Haymes', '15L', 148.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00025', 'Solashield Low Sheen White, 4L', 'Haymes', '4L', 67.4, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00026', 'R/Tex Roll On Coarse, 15L', 'Haymes', '15L', 175.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00027', 'Solashield Satin DKT, 15L', 'Haymes', '15L', 160.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00028', 'Solashield Satin EDT, 15L', 'Haymes', '15L', 160.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00029', 'Solashield Satin UDT, 15L', 'Haymes', '15L', 160.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00030', 'Solashield Satin White, 10L', 'Haymes', '10L', 115.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00031', 'Solashield Satin White, 15L', 'Haymes', '15L', 148.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00032', 'Ultra Premium Primer Sealer', 'Haymes', '', 167.46, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00033', 'Acrylic Sealer Undercoat', 'Haymes', '', 120.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00034', 'Ultratrim High Gloss White', 'Haymes', '', 130.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00035', 'Ultratrim Semi Gloss White', 'Haymes', '', 130.0, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

INSERT INTO products
(product_code, product_name, supplier, unit, price_ex_gst, notes)
VALUES ('PB-H00036', 'Woodcare Aqualac Floor Satin', 'Haymes', '', 250.44, '')
ON CONFLICT (product_code) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    supplier = EXCLUDED.supplier,
    unit = EXCLUDED.unit,
    price_ex_gst = EXCLUDED.price_ex_gst,
    notes = EXCLUDED.notes;

SELECT COUNT(*) AS product_count FROM products;