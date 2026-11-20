# Security policy

finintel handles financial data, so it is built to be boring and safe:

- **Read-only.** No code path moves money, pays bills or places trades.
- **No passwords.** Bank/brokerage sign-in happens on Plaid's and SnapTrade's own pages.
- **Secrets only in the OS keyring** (API keys, access tokens, encryption key, backup passphrase).
- **Local data.** The database stays on your machine; sensitive columns are encrypted.
- **Local dashboard** on 127.0.0.1 with a per-launch token and Host/Origin checks.
- **Logs are redacted**; backups are encrypted and contain no tokens or keys.

## Your checklist
1. Two-factor (authenticator app) on your email, banks, brokerage, Plaid and SnapTrade accounts.
2. Create SnapTrade connections **read-only**.
3. Turn on full-disk encryption (BitLocker / Device encryption, FileVault).
4. Never commit `.env`, `*.db`, logs, backups or statement files (already in `.gitignore`).
5. Revoke access anytime at my.plaid.com and in your SnapTrade dashboard.

## Reporting a vulnerability
Please open a private security advisory on GitHub (Security -> Report a vulnerability) instead of a
public issue. Do not include real account data.
