#!/usr/bin/env python3
"""
pve-dash - a tiny single-file homelab dashboard for Proxmox VE.

Runs ON the Proxmox host (as root) and uses `pvesh`, so no API tokens needed.
Shows: host hardware (CPU, RAM, disk, temp, load, uptime), every LXC/VM with
status + uptime + usage, and makes each running guest a clickable link.

Python 3 standard library only.
"""
import glob
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG_PATH = os.environ.get("PVEDASH_CONFIG", "/etc/pve-dash/config.json")
BIND = os.environ.get("PVEDASH_BIND", "0.0.0.0")
PORT = int(os.environ.get("PVEDASH_PORT", "8088"))

_cache = {"t": 0, "data": None}
_ip_cache = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- helpers
def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def pvesh(path):
    r = subprocess.run(
        ["pvesh", "get", path, "--output-format", "json"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout)


def guest_ip(node, vmid, gtype):
    """Best-effort IPv4 lookup, cached for 60s."""
    key = (node, vmid)
    hit = _ip_cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    ip = None
    try:
        if gtype == "lxc":
            for nic in pvesh(f"/nodes/{node}/lxc/{vmid}/interfaces"):
                if nic.get("name") == "lo":
                    continue
                inet = nic.get("inet")
                if inet:
                    ip = inet.split("/")[0]
                    break
        else:  # qemu - needs qemu-guest-agent inside the VM
            res = pvesh(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
            for nic in res.get("result", []):
                if nic.get("name") == "lo":
                    continue
                for a in nic.get("ip-addresses", []):
                    if a.get("ip-address-type") == "ipv4":
                        ip = a["ip-address"]
                        break
                if ip:
                    break
    except Exception:
        ip = None
    _ip_cache[key] = (time.time(), ip)
    return ip


def read_temp():
    best = None
    for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            v = int(open(p).read().strip()) / 1000.0
            if 0 < v < 150:
                best = v if best is None else max(best, v)
        except Exception:
            pass
    return best


def default_host():
    # Host the browser used is injected client-side; this is only a fallback.
    return None


# ---------------------------------------------------------------- data
def build_status():
    cfg = load_config()
    overrides = {str(k): v for k, v in cfg.get("overrides", {}).items()}
    hidden = {str(x) for x in cfg.get("hide", [])}
    default_ports = {str(k): v for k, v in cfg.get("default_ports", {}).items()}

    resources = pvesh("/cluster/resources")
    nodes = [r for r in resources if r.get("type") == "node"]
    node_name = nodes[0]["node"] if nodes else os.uname().nodename

    host = {"node": node_name}
    try:
        st = pvesh(f"/nodes/{node_name}/status")
        host.update({
            "uptime": st.get("uptime"),
            "cpu": st.get("cpu"),
            "cpu_model": st.get("cpuinfo", {}).get("model"),
            "cpu_cores": st.get("cpuinfo", {}).get("cpus"),
            "mem_used": st.get("memory", {}).get("used"),
            "mem_total": st.get("memory", {}).get("total"),
            "swap_used": st.get("swap", {}).get("used"),
            "swap_total": st.get("swap", {}).get("total"),
            "disk_used": st.get("rootfs", {}).get("used"),
            "disk_total": st.get("rootfs", {}).get("total"),
            "load": st.get("loadavg"),
            "pve": st.get("pveversion"),
            "kernel": st.get("current-kernel", {}).get("release"),
        })
    except Exception as e:
        host["error"] = str(e)
    host["temp"] = read_temp()

    storages = []
    for r in resources:
        if r.get("type") == "storage":
            storages.append({
                "name": r.get("storage"),
                "used": r.get("disk"),
                "total": r.get("maxdisk"),
                "status": r.get("status"),
            })

    guests = []
    for r in resources:
        if r.get("type") not in ("lxc", "qemu"):
            continue
        vmid = str(r.get("vmid"))
        if vmid in hidden:
            continue
        running = r.get("status") == "running"
        tags = [t for t in (r.get("tags") or "").split(";") if t]
        ov = overrides.get(vmid, {})

        ip = None
        if running:
            ip = guest_ip(r.get("node", node_name), vmid, r["type"])

        # Port/scheme: override > tag (port-8080, https) > default_ports > none
        port = ov.get("port") or default_ports.get(vmid)
        scheme = ov.get("scheme") or "http"
        for t in tags:
            if t.startswith("port-") and t[5:].isdigit() and not ov.get("port"):
                port = int(t[5:])
            if t == "https":
                scheme = "https"

        guests.append({
            "vmid": int(vmid),
            "name": ov.get("name") or r.get("name"),
            "type": r["type"],
            "status": r.get("status"),
            "uptime": r.get("uptime") if running else 0,
            "cpu": r.get("cpu") if running else 0,
            "cpus": r.get("maxcpu"),
            "mem": r.get("mem") if running else 0,
            "maxmem": r.get("maxmem"),
            "ip": ip,
            "scheme": scheme,
            "port": port,
            "url": ov.get("url"),  # full custom URL wins on the client
            "tags": tags,
        })
    guests.sort(key=lambda g: g["vmid"])

    return {
        "title": cfg.get("title", "Homelab"),
        "time": int(time.time()),
        "host": host,
        "storages": storages,
        "guests": guests,
    }


def get_status():
    with _lock:
        if time.time() - _cache["t"] > 3 or _cache["data"] is None:
            try:
                _cache["data"] = build_status()
            except Exception as e:
                _cache["data"] = {"error": str(e)}
            _cache["t"] = time.time()
        return _cache["data"]


# ---------------------------------------------------------------- web
HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Homelab Dashboard</title>
<style>
:root{--bg:#0f1218;--card:#181d27;--bd:#252c3a;--tx:#e6e9ef;--mut:#8a93a6;--ok:#34d399;--bad:#f87171;--warn:#fbbf24;--ac:#60a5fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
header{display:flex;justify-content:space-between;align-items:center;padding:18px 24px;border-bottom:1px solid var(--bd)}
h1{margin:0;font-size:20px}.sub{color:var(--mut);font-size:12px}
main{padding:24px;max-width:1300px;margin:0 auto}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin:26px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px;display:block;color:inherit;text-decoration:none}
a.card:hover{border-color:var(--ac);transform:translateY(-1px)}
.card.off{opacity:.5}.row{display:flex;justify-content:space-between;align-items:center;gap:8px}
.name{font-weight:600;font-size:15px}.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:8px;background:var(--bad)}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.meta{color:var(--mut);font-size:12px;margin-top:4px}.badge{font-size:10px;padding:1px 6px;border-radius:6px;border:1px solid var(--bd);color:var(--mut)}
.bar{height:6px;background:#0d1016;border-radius:4px;overflow:hidden;margin-top:6px}.bar>i{display:block;height:100%;background:var(--ac)}
.bar>i.hi{background:var(--warn)}.bar>i.crit{background:var(--bad)}
.lbl{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin-top:10px}
.big{font-size:22px;font-weight:700}.link{color:var(--ac);font-size:12px;margin-top:8px;word-break:break-all}
.err{background:#3b1d1d;border:1px solid var(--bad);padding:12px;border-radius:8px}
</style></head><body>
<header><div><h1 id="title">Homelab</h1><div class="sub" id="sub">loading...</div></div><div class="sub" id="upd"></div></header>
<main><div id="err"></div>
<h2>Host hardware</h2><div class="grid" id="host"></div>
<h2>Storage</h2><div class="grid" id="stor"></div>
<h2 id="gh">Containers &amp; VMs</h2><div class="grid" id="guests"></div></main>
<script>
const $=id=>document.getElementById(id);
const gb=b=>b==null?'-':(b/1073741824).toFixed(1)+' GB';
const pct=(a,b)=>b?Math.round(a/b*100):0;
function up(s){if(!s)return '-';const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
 return (d?d+'d ':'')+(h||d?h+'h ':'')+m+'m';}
function bar(p){return `<div class="bar"><i class="${p>90?'crit':p>75?'hi':''}" style="width:${p}%"></i></div>`}
function stat(label,val,p){return `<div class="card"><div class="meta">${label}</div><div class="big">${val}</div>${p==null?'':bar(p)}</div>`}
function url(g){
  if(g.url)return g.url;
  if(!g.ip||!g.port)return g.ip?`${g.scheme}://${g.ip}`:null;
  return `${g.scheme}://${g.ip}:${g.port}`;
}
async function tick(){
  try{
    const d=await (await fetch('api/status')).json();
    if(d.error){$('err').innerHTML='<div class="err">'+d.error+'</div>';return}
    $('err').innerHTML='';$('title').textContent=d.title;document.title=d.title+' Dashboard';
    const h=d.host;
    $('sub').textContent=`${h.node} · PVE ${h.pve||''} · kernel ${h.kernel||''}`;
    const cpu=Math.round((h.cpu||0)*100);
    $('host').innerHTML=
      stat('Uptime',up(h.uptime))+
      stat(`CPU (${h.cpu_cores||'?'} cores)`,cpu+'%',cpu)+
      stat('Memory',`${gb(h.mem_used)} / ${gb(h.mem_total)}`,pct(h.mem_used,h.mem_total))+
      stat('Root disk',`${gb(h.disk_used)} / ${gb(h.disk_total)}`,pct(h.disk_used,h.disk_total))+
      stat('Temperature',h.temp==null?'n/a':h.temp.toFixed(0)+' °C',h.temp==null?null:Math.min(100,h.temp))+
      stat('Load avg',(h.load||[]).join(' / ')||'-',null)+
      (h.cpu_model?`<div class="card"><div class="meta">CPU model</div><div>${h.cpu_model}</div></div>`:'');
    $('stor').innerHTML=d.storages.map(s=>stat(s.name+' ('+s.status+')',`${gb(s.used)} / ${gb(s.total)}`,pct(s.used,s.total))).join('');
    const run=d.guests.filter(g=>g.status==='running').length;
    $('gh').textContent=`Containers & VMs — ${run} running / ${d.guests.length} total`;
    $('guests').innerHTML=d.guests.map(g=>{
      const on=g.status==='running',u=on?url(g):null;
      const cp=Math.round((g.cpu||0)*100),mp=pct(g.mem,g.maxmem);
      const inner=`<div class="row"><div class="name"><span class="dot ${on?'on':''}"></span>${g.name}</div>
        <span class="badge">${g.type==='lxc'?'LXC':'VM'} ${g.vmid}</span></div>
        <div class="meta">${on?'Up '+up(g.uptime):'Stopped'}${g.ip?' · '+g.ip:''}</div>
        ${on?`<div class="lbl"><span>CPU</span><span>${cp}%</span></div>${bar(cp)}
        <div class="lbl"><span>RAM</span><span>${gb(g.mem)} / ${gb(g.maxmem)}</span></div>${bar(mp)}`:''}
        ${u?`<div class="link">↗ ${u}</div>`:''}`;
      return u?`<a class="card" href="${u}" target="_blank" rel="noopener">${inner}</a>`:`<div class="card ${on?'':'off'}">${inner}</div>`;
    }).join('');
    $('upd').textContent='Updated '+new Date(d.time*1000).toLocaleTimeString();
  }catch(e){$('err').innerHTML='<div class="err">Cannot reach dashboard API: '+e+'</div>'}
}
tick();setInterval(tick,5000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, HTML.encode(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._send(200, json.dumps(get_status()).encode(), "application/json")
        elif path == "/health":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"pve-dash listening on http://{BIND}:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
