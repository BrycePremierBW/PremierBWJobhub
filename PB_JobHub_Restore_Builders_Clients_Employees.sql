-- Premier Brushworks JobHub
-- Restore master builders/clients and employees.
-- Safe to run more than once.
-- It updates matching names and inserts missing records.

INSERT INTO builders_clients
(type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
VALUES
('Builder', 'Ausmar Homes Pty Ltd', 'Compliance Team', '07 5319 1500', 'compliance@ausmargroup.com.au', '8 Flinders Lane, Maroochydore QLD 4558', '1083000', '55 087 236 208', '30 Days', 'Annual Period Trade Contract'),
('Developer / Builder', 'OneLife Property Group', 'Bryce Curran', '0421 069 817', 'brycecurran@hotmail.com', 'Sunshine Coast', '', '', '30 Days', 'Multi-residential complexes'),
('Builder', 'Thompson Homes', '', '', '', '', '', '', '30 Days', 'Existing JobHub builder'),
('Client / Developer', 'Palm Lakes', '', '', '', 'Pelican Waters', '', '', '30 Days', 'Palm Lakes Pelican Waters'),
('Interior Designer', 'Box Clever Interiors', 'Design Team', '07 5309 5640', 'info@boxcleverinteriors.com.au', 'PO Box 208, Moffat Beach QLD 4551', '', '08 007 428 613', '', 'Bannister project designer'),
('Interior Designer', 'Inka Interiors', 'Sheena Hanks', '0438 308 672', 'info@inkainteriors.com.au', 'Basement Level, 811 Stanley St, Woolloongabba', '', '', '', 'Cunningham project designer'),
('Painting Contractor', 'Emerald Painting Company Pty Ltd', 'Anthony Des Johnston', '0410 949 719', 'des@emeraldpainting.com.au', '20 Warenna Crescent, Glenvale QLD 4350', '', '85 169 333 957', '', 'Industry contact'),
('Supplier', 'Dulux Australia', '', '07 5443 7255', '', 'Cnr Amaroo St & Maroochydore Rd, Maroochydore QLD 4558', '', '67 000 049 427', '', 'Supplier'),
('Builder', 'Greenrock Building', '', '', '', '', '', '', '30 Days', 'Client history'),
('Builder', 'Rejuvenate Group', '', '', '', '', '', '', '30 Days', 'School works'),
('Builder', 'Adlar Homes', '', '', '', 'Maroochydore', '', '', '30 Days', 'Client history'),
('Builder', 'Darren Hunt Homes', '', '', '', '', '', '', '30 Days', 'Custom homes'),
('Builder', 'Watherston Building', '', '', '', '', '', '', '30 Days', 'Custom homes'),
('Commercial Client', 'Stockland Aura', '', '', '', 'Aura', '', '', '', 'Commercial developments'),
('Commercial Builder', 'FDC Constructions', 'Simon Hawkins / Adam Pickering', '', '', '', '', '', '', 'Outreach'),
('Commercial Client', 'Comiskey Group', 'Paul / David / Rob & team', '', '', 'Sunshine Coast', '', '', '', 'Hospitality venue'),
('Education Client', 'Nambour State College', '', '', '', 'Nambour', '', '', '', 'School works'),
('Education Client', 'Currimundi State School', '', '', '', 'Currimundi', '', '', '', 'School works'),
('Education Client', 'Currimundi Special School', '', '', '', 'Currimindi', '', '', '', 'School works'),
('Education Client', 'Gympie South State School', '', '', '', 'Gympie', '', '', '', 'School works'),
('Education Client', 'Good Shepherd Lutheran School', '', '', '', '', '', '', '', 'School works')
ON CONFLICT (name) DO UPDATE SET
    type = EXCLUDED.type,
    contact_name = EXCLUDED.contact_name,
    phone = EXCLUDED.phone,
    email = EXCLUDED.email,
    address = EXCLUDED.address,
    qbcc = EXCLUDED.qbcc,
    abn = EXCLUDED.abn,
    terms = EXCLUDED.terms,
    notes = EXCLUDED.notes;

INSERT INTO employees
(name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
VALUES
('Bryce', '', '', 60.0, 66.0, 'Active', ''),
('Brodrick', '', '', 45.0, 49.5, 'Active', ''),
('Sol', '', '', 50.0, 55.0, 'Active', ''),
('Critter', '', '', 40.0, 44.0, 'Active', ''),
('Greg', '', '', 46.0, 50.6, 'Active', ''),
('Chris Nagy', '', '', 50.0, 55.0, 'Active', ''),
('Isaac', '', '', 46.0, 50.6, 'Active', ''),
('Rob Pullin', '', '', 45.0, 49.5, 'Active', ''),
('Ian', '', '', 46.0, 50.6, 'Active', ''),
('Tim', '', '', 45.0, 49.5, 'Active', ''),
('Anth', '', '', 35.0, 38.5, 'Active', ''),
('River', '', '', 32.5, 35.75, 'Active', ''),
('Dipper', '', '', 45.0, 49.5, 'Active', ''),
('Vlad 1', '', '', 45.0, 49.5, 'Active', ''),
('Vlad 2', '', '', 45.0, 49.5, 'Active', ''),
('Ryan', '', '', 45.0, 49.5, 'Active', '')
ON CONFLICT (name) DO UPDATE SET
    role = EXCLUDED.role,
    phone = EXCLUDED.phone,
    base_hourly_rate = EXCLUDED.base_hourly_rate,
    rate_plus_10 = EXCLUDED.rate_plus_10,
    status = EXCLUDED.status,
    notes = EXCLUDED.notes;

SELECT 'builders_clients' AS table_name, COUNT(*) AS record_count FROM builders_clients
UNION ALL
SELECT 'employees' AS table_name, COUNT(*) AS record_count FROM employees;
