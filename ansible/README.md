# Ansible Deployment

This deployment path provisions Tennis Calendar Bridge onto a VPS with Docker
Compose. Secrets are rendered from Ansible Vault on the target machine; no local
`.env` file is required.

## First-Time Setup

```bash
cp ansible/inventory.example.yml ansible/inventory.yml
cp ansible/group_vars/tennis_servers/vault.yml.example \
  ansible/group_vars/tennis_servers/vault.yml
```

Edit `ansible/inventory.yml` for your SSH target.

Edit the vault file with tokens and club credentials. Credential keys should
match the `username_env` and `password_env` values in your club config:

```bash
$EDITOR ansible/group_vars/tennis_servers/vault.yml
```

```yaml
vault_tennis_credentials:
  MY_CLUB_USERNAME: your-login
  MY_CLUB_PASSWORD: your-password
```

Encrypt it:

```bash
ansible-vault encrypt ansible/group_vars/tennis_servers/vault.yml
```

For later changes:

```bash
ansible-vault edit ansible/group_vars/tennis_servers/vault.yml
```

Optional deployment-specific non-secret overrides can go in an ignored local
file:

```bash
cp ansible/group_vars/tennis_servers/zz-local.yml.example \
  ansible/group_vars/tennis_servers/zz-local.yml
$EDITOR ansible/group_vars/tennis_servers/zz-local.yml
```

Use `zz-local.yml` to override the public example `tennis_clubs` list with your
real providers and env-var names. The `zz-` prefix makes it load after
`vars.yml`.

Deploy:

```bash
bash scripts/deploy_ansible.sh
```

Or directly:

```bash
cd ansible
ansible-playbook playbook.yml --ask-vault-pass
```

## Paths

The default remote directory is:

```text
/home/ubuntu/projects/tennis-calendar-bridge
```

Change it in `ansible/group_vars/tennis_servers/zz-local.yml`:

```yaml
tennis_remote_dir: /home/ubuntu/projects/tennis-calendar-bridge
```

## Calendar Token And URL

The calendar feed token comes from this vault variable:

```yaml
vault_tennis_calendar_token: your-long-random-calendar-token
```

Ansible renders it on the VPS as:

```bash
TENNIS_CALENDAR_TOKEN=your-long-random-calendar-token
```

Your feed URL is:

```text
https://YOUR_DOMAIN/calendar/your-long-random-calendar-token/tennis.ics
```

If you want the dashboard to display a ready-to-copy URL, set this in
`zz-local.yml`:

```yaml
tennis_public_base_url: https://YOUR_DOMAIN
```

## Tailscale Calendar Access

Tailscale Serve works well for private Apple Calendar subscriptions when the
device doing the refresh is on your tailnet.

The playbook runs this by default:

```bash
sudo tailscale serve --bg --yes 8081
```

That publishes a tailnet-only HTTPS URL and proxies requests to the local app at:

```text
http://127.0.0.1:8081
```

Set your tailnet URL in ignored `zz-local.yml`:

```yaml
tennis_public_base_url: https://YOUR_DEVICE.YOUR_TAILNET.ts.net
```

Disable Tailscale Serve management if you want to configure it manually:

```yaml
tennis_tailscale_serve_enabled: false
```

For Google Calendar, a private tailnet URL usually will not work because Google
fetches subscribed calendar URLs from Google's servers. Use a public HTTPS
endpoint instead:

- Tailscale Funnel pointing to the app
- a normal domain with Caddy/nginx reverse proxy
- a public VPS IP/domain with HTTPS

If you make the service public, put authentication in front of the dashboard or
serve only the calendar feed path.

## Port Conflicts

If deployment fails with a message like `port is already allocated`, change the
host port in `zz-local.yml`:

```yaml
tennis_port: 8081
```

For direct Tailscale access without Tailscale Serve, bind to the VPS Tailscale
IP or to all interfaces:

```yaml
tennis_host_bind: 100.x.y.z
```

For Tailscale Serve/Funnel or a local reverse proxy, keep:

```yaml
tennis_host_bind: 127.0.0.1
```

## Docker Permissions

The playbook assumes the SSH user can run Docker without sudo. If your VPS needs
sudo for Docker, set this in `zz-local.yml`:

```yaml
tennis_docker_become: true
```
