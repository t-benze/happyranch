# HappyRanch Remote Access — Self-Hosted headscale + DERP Runbook

**Phase-2 Part A** — deploy artifact runbook (THR-097)
**Author:** dev_agent (config + docs only — founder executes)
**Status:** Ready for founder provisioning

---

## Overview

This runbook walks through provisioning a **self-hosted headscale control plane**
with an **embedded DERP relay**, all behind **Caddy + Let's Encrypt** for TLS.

When complete, you will have:
- A headscale coordination server at your chosen hostname (e.g. `headscale.example.com`)
- A DERP relay on the same host (STUN on UDP 3478, DERP HTTPS on 443)
- **No dependency on Tailscale's SaaS control plane or public DERP fleet**

The HappyRanch Mac client (Phase 1, PR #425) will connect to this control plane
via its `HAPPYRANCH_TSNET_TRANSPORT=1` / `HAPPYRANCH_TSNET_CONTROL_URL` /
`HAPPYRANCH_TSNET_AUTH_KEY` environment variables.

---

## Prerequisites

You need:
1. A **Linux host** (VM or bare-metal) with a **public IPv4 address**
   - Recommended: 2+ GB RAM, 10 GB disk
   - OS: Ubuntu 22.04+ or Debian 12+
2. **Docker** and **Docker Compose v2** installed on the host
3. A **domain name** you control (e.g. `example.com`) — you will create an
   A record for the headscale server
4. **Firewall rules** allowing inbound:
   - TCP 80   (Let's Encrypt HTTP-01 challenge)
   - TCP 443  (headscale API + DERP HTTPS)
   - UDP 3478 (DERP STUN)

---

## Step 1: Host provisioning

Provision a Linux VM or bare-metal host with a public IPv4 address.

```bash
# Example: Ubuntu 22.04
sudo apt update && sudo apt upgrade -y

# Install Docker (official)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER

# Install Docker Compose plugin (v2)
sudo apt install -y docker-compose-v2

# Verify
docker --version
docker compose version

# Open firewall ports (example: UFW)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 3478/udp
sudo ufw enable
```

> **Cloud-specific note:** If your cloud provider has a separate firewall
> (AWS security groups, GCP firewall rules, DigitalOcean cloud firewall),
> open TCP 80, TCP 443, and UDP 3478 there as well.

---

## Step 2: DNS — create an A record for the control-plane hostname

Choose a subdomain for your headscale server, e.g. `headscale.example.com`.

Create a **DNS A record** pointing that hostname to the host's **public IPv4**:

| Type | Name               | Value          | TTL   |
|------|--------------------|----------------|-------|
| A    | headscale          | `<public-ip>`  | 300   |

Wait for DNS propagation (usually < 5 minutes). Verify:

```bash
dig +short headscale.example.com
# Should return the host's public IP
```

---

## Step 3: Clone the deploy artifact and configure secrets

On the provisioned host:

```bash
# Clone the happyranch repo (or copy the deploy/remote-access/ directory)
git clone https://github.com/t-benze/happyranch.git
cd happyranch/deploy/remote-access
```

Copy the environment template and fill in real values:

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```bash
# The full URL of your headscale server (must use https://)
HEADSCALE_SERVER_URL=https://headscale.example.com

# The DERP public hostname (same host, without https://)
DERP_PUBLIC_HOSTNAME=headscale.example.com

# Optional: custom STUN port if your cloud maps it differently
# DERP_STUN_PORT=3478

# Optional: Let's Encrypt email for TLS certificate issuance
# Uncomment the tls line in Caddyfile if setting this
# TLS_EMAIL=you@example.com
```

---

## Step 4: Configure TLS in Caddyfile

Open `Caddyfile` and **uncomment the `tls` directive** with your email:

```caddy
{$HEADSCALE_SERVER_URL:localhost} {
        tls {$TLS_EMAIL}
        reverse_proxy headscale:8080 { ... }
}
```

If `TLS_EMAIL` is not set, Caddy will still work but will use a
zero-SSL/Let's Encrypt default — the directive is recommended for
certificate expiry notifications.

---

## Step 5: Start the stack

```bash
docker compose up -d
```

Check that both services are running:

```bash
docker compose ps
```

Expected output:

```
NAME                  STATUS
happyranch-headscale  Up (healthy)
happyranch-caddy      Up
```

Check logs for any startup errors:

```bash
docker compose logs headscale
docker compose logs caddy
```

---

## Step 6: Verify the control plane is reachable

From the host itself:

```bash
# headscale health endpoint (internal, via Docker network)
curl -s http://localhost:8080/health
# Expected: OK

# Control plane public URL (via Caddy + TLS)
curl -s https://headscale.example.com/health
# Expected: OK
```

> **Troubleshooting:** If the public URL returns a TLS error, check:
> - DNS A record is correct (`dig +short headscale.example.com`)
> - Port 443 is open to the internet
> - Caddy logs: `docker compose logs caddy`
> - Let's Encrypt may take 30-60 seconds on first request

---

## Step 7: Health check — headscale nodes list

Use the headscale CLI inside the container:

```bash
# Should return an empty list (no nodes registered yet)
docker compose exec headscale headscale nodes list
```

Expected output:

```
ID | Hostname | Name | ... | LastSeen | Expiry
(empty — no nodes registered)
```

---

## Step 8: DERP reachability probe

Verify the DERP relay is accessible:

```bash
# Check that the STUN port is open (from an external machine, or the host)
nc -vzu headscale.example.com 3478
# Expected: Connection to headscale.example.com 3478 port [udp/stun] succeeded!

# Check DERP HTTPS endpoint (should return WebSocket upgrade or 404 — either is fine)
curl -s -o /dev/null -w "%{http_code}" https://headscale.example.com/
# Expected: any HTTP response (200, 404, etc.) — confirms TLS + headscale are running
```

The DERP relay is working if:
- UDP 3478 is reachable
- HTTPS 443 on the control-plane hostname returns a headscale response

---

## Step 9: Mint a device auth key

This is the key the HappyRanch Mac client consumes via
`HAPPYRANCH_TSNET_AUTH_KEY`.

```bash
# Create a REUSABLE auth key (recommended for a small tailnet)
# The key is printed ONCE — save it immediately.
docker compose exec headscale headscale preauthkeys create \
  --reusable \
  --expiration 365d \
  --user admin \
  --output json
```

The output contains the auth key:

```json
{"key": "abc123def456..."}
```

**Save this key securely.** You will use it in the next step to configure
the HappyRanch Mac client.

Options:
- `--reusable` — the same key can register multiple nodes (useful for dev)
- `--expiration 365d` — key expires after one year
- `--user admin` — nodes register under the `admin` user in headscale
- Omit `--reusable` for one-time keys (more secure for production)

To verify the key was created:

```bash
docker compose exec headscale headscale preauthkeys list --user admin
```

---

## Step 10: Wire the HappyRanch Mac client back to this control plane

On the Mac running the HappyRanch app, set these environment variables:

```bash
# Enable the in-process tsnet WireGuard tunnel
export HAPPYRANCH_TSNET_TRANSPORT=1

# Point at YOUR self-hosted headscale (NOT Tailscale SaaS)
export HAPPYRANCH_TSNET_CONTROL_URL=https://headscale.example.com

# The auth key minted in Step 9
export HAPPYRANCH_TSNET_AUTH_KEY=abc123def456...

# Optional: custom hostname for the node
export HAPPYRANCH_TSNET_HOSTNAME=my-macbook
```

Then launch the app. The client's `ensureTsnetEngine()` will:
1. Read `HAPPYRANCH_TSNET_AUTH_KEY` and `HAPPYRANCH_TSNET_CONTROL_URL`
2. Call `tsnet_init(authKey, controlURL, hostname)` — this points at YOUR headscale
3. Call `tsnet_start()` — registers the node with your headscale

Verify the node appeared:

```bash
# On the server
docker compose exec headscale headscale nodes list
```

Expected: a new node with the hostname you set (or `happyranch-tsnet` if not set).

---

## Step 11: Verify end-to-end connectivity (optional)

Once a Mac client is registered, you can verify the tailnet is working:

```bash
# On the server, ping the Mac node's Tailscale IP
docker compose exec headscale headscale nodes list
# Copy the Tailscale IP of the Mac node (100.64.x.y)

# From the Mac node, verify it can reach the headscale server
# (tsnet local_addr returns the node's own Tailscale IP)
```

---

## Appendix A: Directory layout

```
deploy/remote-access/
├── docker-compose.yml       # Docker Compose stack (headscale + Caddy)
├── Caddyfile                # Caddy reverse proxy + TLS config
├── .env.example             # Environment variable placeholders
├── .env                     # REAL secrets (gitignored — founder creates from .example)
├── .gitignore               # Ignores .env
├── headscale/
│   └── config.yaml          # headscale server config (self-hosted, no Tailscale SaaS)
├── derp/
│   └── derp.yaml            # DERP region map (self-hosted relay ONLY)
└── RUNBOOK.md               # This file
```

---

## Appendix B: Troubleshooting

### Caddy can't obtain a TLS certificate
- Verify DNS A record is correct and propagated
- Verify port 80 is open (Let's Encrypt HTTP-01 challenge)
- Check Caddy logs: `docker compose logs caddy`
- Ensure `tls {$TLS_EMAIL}` is uncommented in Caddyfile

### headscale starts but isn't reachable
- Check `docker compose logs headscale` for errors
- Verify `.env` has `HEADSCALE_SERVER_URL` set
- The `server_url` in config.yaml references `${HEADSCALE_SERVER_URL}` — ensure the env var is set

### Node registration fails ("auth key invalid")
- Verify the auth key was copied correctly (no trailing whitespace)
- Check if the key expired: `headscale preauthkeys list --user admin`
- Create a new key if needed (Step 9)

### DERP STUN is unreachable
- Verify UDP 3478 is open on both the host firewall and cloud firewall
- Check headscale logs for DERP startup messages
- Try `nc -vzu <hostname> 3478` from an external machine

### DERP relay traffic doesn't flow
- DERP uses WebSocket over HTTPS on port 443. Caddy must forward WebSocket upgrades.
- The default Caddy `reverse_proxy` directive handles WebSocket automatically.
- If behind an additional CDN/proxy, ensure WebSocket support is enabled.

---

## Appendix C: Day-to-day operations

### View registered nodes
```bash
docker compose exec headscale headscale nodes list
```

### Expire (remove) a node
```bash
docker compose exec headscale headscale nodes expire --identifier <node-id>
```

### List preauth keys
```bash
docker compose exec headscale headscale preauthkeys list --user admin
```

### Revoke a preauth key
```bash
docker compose exec headscale headscale preauthkeys expire --user admin --key <key-prefix>
```

### View logs
```bash
docker compose logs -f headscale
docker compose logs -f caddy
```

### Update headscale
```bash
docker compose pull headscale
docker compose up -d headscale
```

### Backup
```bash
# The persistent state lives in the headscale-data Docker volume.
# Back it up:
docker run --rm -v happyranch_headscale-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/headscale-backup-$(date +%Y%m%d).tar.gz -C /data .

# Back up the .env file as well (contains secrets).
```
