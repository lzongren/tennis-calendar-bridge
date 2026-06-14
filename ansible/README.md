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

For unattended local deploys, put the Ansible Vault password in an ignored file:

```bash
printf '%s\n' 'YOUR_ANSIBLE_VAULT_PASSWORD' > ansible/.vault-pass
chmod 600 ansible/.vault-pass
bash scripts/deploy_ansible.sh
```

You can also point at another ignored password file:

```bash
ANSIBLE_VAULT_PASSWORD_FILE=/path/to/private/vault-pass bash scripts/deploy_ansible.sh
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

For a root-mounted deployment, the feed URL is:

```text
https://YOUR_DOMAIN/calendar/your-long-random-calendar-token/tennis.ics
```

If you want the dashboard to display a ready-to-copy URL for a root-mounted
deployment, set this in `zz-local.yml`:

```yaml
tennis_public_base_url: https://YOUR_DOMAIN
```

## Base Path And Routing

The playbook owns only the tennis app container. It does not configure
Tailscale Serve, Funnel, Caddy, nginx, or any other routing layer.

By default the app is configured for a private local backend mounted under
`/tennis`:

```yaml
tennis_host_bind: 127.0.0.1
tennis_port: 8081
tennis_base_path: /tennis
```

The expected backend target for your VPS management repo is:

```text
http://127.0.0.1:8081
```

This repo includes a non-secret app handoff artifact at
`deploy/vps-management-handoff.example.json`.

The expected `vps-management/config/routes.json` entry is:

```json
{
  "name": "tennis",
  "kind": "node-subpath",
  "enabled": true,
  "exposure": "tailnet-only",
  "host": "YOUR_NODE.YOUR_TAILNET.ts.net",
  "path": "/tennis/",
  "target": "http://127.0.0.1:8081",
  "app_base_path_env": "TENNIS_BASE_PATH=/tennis",
  "description": "Tennis app mounted under /tennis/. The tennis repo owns app deployment only and should not manage Tailscale Serve by default."
}
```

Set the externally visible app URL in ignored `zz-local.yml` so the dashboard
can render the correct calendar subscription link:

```yaml
tennis_public_base_url: https://YOUR_DOMAIN/tennis
```

With that configuration, the feed URL is:

```text
https://YOUR_DOMAIN/tennis/calendar/your-long-random-calendar-token/tennis.ics
```

Apple Calendar can subscribe to private Tailscale URLs when the device doing the
refresh is on your tailnet. Google Calendar usually cannot, because Google
fetches subscribed calendar URLs from Google's servers. Use a public HTTPS
endpoint if you need Google Calendar to refresh the feed directly.

If you make any route public, put authentication in front of the dashboard or
serve only the calendar feed path.

## Port Conflicts

If deployment fails with a message like `port is already allocated`, change the
host port in `zz-local.yml`:

```yaml
tennis_port: 8081
```

For vps-management, Tailscale Serve/Funnel, or a local reverse proxy, keep:

```yaml
tennis_host_bind: 127.0.0.1
```

## Docker Permissions

The playbook assumes the SSH user can run Docker without sudo. If your VPS needs
sudo for Docker, set this in `zz-local.yml`:

```yaml
tennis_docker_become: true
```
