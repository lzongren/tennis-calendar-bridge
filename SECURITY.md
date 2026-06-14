# Security Policy

Tennis Calendar Bridge handles club credentials and personal schedule data. Run
it as a private service unless you add authentication in front of the dashboard.

## Supported Versions

This project is early-stage. Security fixes are applied to the main branch.

## Reporting A Vulnerability

Please do not open public issues that contain credentials, tokens, screenshots,
HTML dumps, or personal schedule data.

If you find a vulnerability:

1. Open a private GitHub security advisory when available.
2. Otherwise, contact the repository owner through the contact method listed on
   their GitHub profile.
3. Include a minimal reproduction that avoids real credentials and personal
   booking data.

## Operational Notes

- Keep `.env`, `config/clubs.toml`, Ansible Vault files, SQLite data, and
  `data/debug/` artifacts out of Git.
- Treat calendar-feed URLs as secrets because the token is embedded in the path.
- Prefer Tailscale Serve, a VPN, or an authenticated reverse proxy for the web
  dashboard.
- If a public endpoint is required for Google Calendar, expose only what is
  necessary and use a long random calendar token.
