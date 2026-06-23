-- Premier Brushworks JobHub
-- Delete duplicate user accounts from app_users.
-- This only deletes login accounts.
-- It does NOT delete employees, jobs, wages, timesheets, materials, photos, or history.
--
-- What it treats as duplicates:
-- 1. Same username ignoring spaces/case.
-- 2. More than one user account linked to the same employee_id.
--
-- Safety:
-- - Keeps one account per duplicate group.
-- - Prefers active admin, then active account, then lowest id.
-- - Will not delete the last active admin account.

BEGIN;

WITH username_ranked AS (
    SELECT
        id,
        username,
        role,
        active,
        LOWER(TRIM(username)) AS username_key,
        ROW_NUMBER() OVER (
            PARTITION BY LOWER(TRIM(username))
            ORDER BY
                CASE WHEN role = 'admin' AND active = 1 THEN 0 ELSE 1 END,
                CASE WHEN active = 1 THEN 0 ELSE 1 END,
                id
        ) AS rn,
        COUNT(*) OVER (PARTITION BY LOWER(TRIM(username))) AS group_count
    FROM app_users
    WHERE username IS NOT NULL AND TRIM(username) <> ''
),
employee_ranked AS (
    SELECT
        id,
        username,
        role,
        active,
        employee_id,
        ROW_NUMBER() OVER (
            PARTITION BY employee_id
            ORDER BY
                CASE WHEN role = 'admin' AND active = 1 THEN 0 ELSE 1 END,
                CASE WHEN active = 1 THEN 0 ELSE 1 END,
                id
        ) AS rn,
        COUNT(*) OVER (PARTITION BY employee_id) AS group_count
    FROM app_users
    WHERE employee_id IS NOT NULL
),
duplicate_candidates AS (
    SELECT id FROM username_ranked WHERE group_count > 1 AND rn > 1
    UNION
    SELECT id FROM employee_ranked WHERE group_count > 1 AND rn > 1
),
safe_to_delete AS (
    SELECT u.id
    FROM app_users u
    JOIN duplicate_candidates d ON d.id = u.id
    WHERE NOT (
        u.role = 'admin'
        AND u.active = 1
        AND (SELECT COUNT(*) FROM app_users WHERE role = 'admin' AND active = 1) <= 1
    )
)
DELETE FROM app_users
WHERE id IN (SELECT id FROM safe_to_delete);

COMMIT;

-- Check remaining duplicate/suspect accounts by username.
SELECT username, COUNT(*) AS duplicate_count
FROM app_users
GROUP BY LOWER(TRIM(username)), username
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, username;

-- Check remaining duplicate/suspect accounts by employee link.
SELECT employee_id, COUNT(*) AS duplicate_count
FROM app_users
WHERE employee_id IS NOT NULL
GROUP BY employee_id
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, employee_id;
