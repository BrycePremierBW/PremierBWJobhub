# Security operations

- Use a unique administrator password with at least 12 characters, uppercase,
  lowercase, a number, and a symbol.
- Remove `JOBHUB_BOOTSTRAP_ADMIN_PASSWORD` immediately after the first
  administrator signs in.
- To recover an existing staging administrator, set a unique
  `JOBHUB_BOOTSTRAP_ADMIN_RESET_ID` together with the temporary bootstrap
  password. This is ignored outside staging and consumed only once. Remove
  both values after the administrator changes the temporary password.
- Give each person an individual login. Do not share administrator accounts.
- Disable departed users promptly and review employee job assignments.
- Review the login and application audit at least monthly.
- Keep database and file-storage backups encrypted and test restoration.
- Store database and API credentials only in the hosting secret manager.
- Leave `JOBHUB_ENABLE_SELF_EDIT=false` in production.
- External AI must remain off until the organisation approves what data may be
  sent and documents retention and access rules.
- Never commit customer, employee, payroll, database, or API secrets into this
  source package.
- Apply dependency and platform security updates in a staging environment
  before production.

## Incident response

If an account or credential may be compromised:

1. Disable the affected login.
2. Rotate database and API credentials.
3. Preserve the audit log and hosting logs.
4. Restore from a known-good backup if integrity is uncertain.
5. Record what data and time period may have been affected.
