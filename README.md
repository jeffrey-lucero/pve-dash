# pve-dash

A tiny, dependency-free homelab dashboard for **Proxmox VE**. One page showing:

- Host hardware: uptime, CPU, RAM, root disk, temperature, load
- Storage pools
- Every LXC container and VM: running/stopped, **uptime**, CPU/RAM, IP
- Every running guest is a **clickable card** that opens its web UI

Runs directly on the Proxmox host using `pvesh` (no API tokens). Python 3 standard library only.

## Install (on the Proxmox host, as root)

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/jeffrey-lucero/pve-dash/main/install.sh)"
```

Then open `http://<proxmox-ip>:8088`.

Change the port: `PVEDASH_PORT=9000 bash -c "$(curl ...)"`

## Make cards clickable to the right port

Easiest: add **tags** to the container in the Proxmox UI (Summary → Tags):

| Tag         | Effect                               |
|-------------|--------------------------------------|
| `port-8080` | link goes to `http://<ip>:8080`      |
| `https`     | use `https://` instead of `http://`  |

No tag? The card links to `http://<ip>` (port 80).

Or edit `/etc/pve-dash/config.json` (then `systemctl restart pve-dash`):

```json
{
  "title": "My Homelab",
  "hide": [100],
  "default_ports": { "101": 8096 },
  "overrides": {
    "102": { "name": "Nextcloud", "url": "https://cloud.example.com" },
    "103": { "port": 3000, "scheme": "http" }
  }
}
```

## Notes

- LXC IPs are detected automatically. For VMs, install `qemu-guest-agent` in the guest to get the IP.
- There is **no authentication**. Keep it on your LAN, or put it behind a reverse proxy / VPN. Bind to localhost with `PVEDASH_BIND=127.0.0.1`.
- Logs: `journalctl -u pve-dash -f`

## Uninstall

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/jeffrey-lucero/pve-dash/main/install.sh)" -- --uninstall
```
