-- Premier Brushworks JobHub
-- Employee cleanup helper.
-- This SQL marks duplicate employee records inactive where names are duplicated.
-- It does NOT delete employee history.
-- Use the in-app Employee List bulk delete/deactivate button for manual cleanup.

BEGIN;

WITH ranked AS (
    SELECT
        id,
        name,
        status,
        ROW_NUMBER() OVER (
            PARTITION BY LOWER(TRIM(name))
            ORDER BY
                CASE WHEN status = 'Active' THEN 0 ELSE 1 END,
                id
        ) AS rn,
        COUNT(*) OVER (PARTITION BY LOWER(TRIM(name))) AS group_count
    FROM employees
    WHERE name IS NOT NULL AND TRIM(name) <> ''
)
UPDATE employees
SET status = 'Inactive',
    notes = COALESCE(notes, '') || ' | Duplicate employee record marked inactive by cleanup SQL'
WHERE id IN (
    SELECT id
    FROM ranked
    WHERE group_count > 1 AND rn > 1
);

COMMIT;

SELECT id, name, role, status, notes
FROM employees
WHERE LOWER(TRIM(name)) IN (
    SELECT LOWER(TRIM(name))
    FROM employees
    GROUP BY LOWER(TRIM(name))
    HAVING COUNT(*) > 1
)
ORDER BY LOWER(TRIM(name)), id;
