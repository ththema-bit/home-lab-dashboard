#!/usr/bin/env python3
"""Landing page backend (run from cron every minute, no network access beyond your own hosts).
1. Checks every service in services.json (falls back to services.example.json) -> results.
2. Auto-discovery: lists listening TCP ports on the local host (ss) and, for remote hosts,
   over SSH if HOME_SSH_USER is set; probes unknown ones over HTTP and reports web UIs that
   are not yet in services.json -> 'discovered'.
Hosts come from the "hosts" object in services.json: {"Server": "127.0.0.1", ...}. The first
host is treated as local. Writes status.json atomically."""
import json, ssl, time, re, os, sys, subprocess, urllib.request, urllib.error, concurrent.futures as cf
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else "/var/www/home/status.json"
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))

_cfg = os.path.join(HERE, "services.json")
svc = json.load(open(_cfg if os.path.exists(_cfg) else os.path.join(HERE, "services.example.json")))
HOSTS = svc.get("hosts") or {"Server": "127.0.0.1"}
LOCAL = next(iter(HOSTS))
SSH_USER = os.environ.get("HOME_SSH_USER", "")
SSH_KEY = os.path.expanduser(os.environ.get("HOME_SSH_KEY", "~/.ssh/id_ed25519"))

def remote(host, command):
    return ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", f"{SSH_USER}@{HOSTS[host]}", command]

def fetch(url, body=False):
    t = time.time()
    try:
        r = opener.open(urllib.request.Request(url, headers={"User-Agent": "home-landing"}), timeout=4)
        code, data = r.status, (r.read(4096) if body else b"")
    except urllib.error.HTTPError as e: code, data = e.code, b""
    except Exception: return None
    return {"code": code, "ms": int((time.time() - t) * 1000), "body": data}

def check(url):
    r = fetch(url)
    if not r: return {"up": False, "ms": None}
    return {"up": r["code"] < 500, "code": r["code"], "ms": r["ms"]}

def listening(host):
    cmd = ["ss", "-tlnH"]
    if host != LOCAL:
        if not SSH_USER: return set()
        cmd = remote(host, "ss -tlnH")
    try: out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception: return set()
    ports = set()
    for line in out.splitlines():
        f = line.split()
        if len(f) < 4: continue
        addr, port = f[3].rsplit(":", 1)
        if addr.startswith("127.") or addr in ("[::1]",) or "%lo" in addr or addr.startswith("10.") : continue
        if port.isdigit() and int(port) < 32768: ports.add(int(port))
    return ports

def host_ips():
    """Current IPv4s per host, so the page can rewrite links to the address reachable from the browser."""
    out = {}
    cmds = {LOCAL: ["ip", "-4", "-o", "addr", "show", "scope", "global"]}
    if SSH_USER:
        for h in HOSTS:
            if h != LOCAL: cmds[h] = remote(h, "ip -4 -o addr show scope global")
    for h, cmd in cmds.items():
        try: txt = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except Exception: txt = ""
        ifs = [(m.group(1), m.group(2), "dynamic" in m.group(0)) for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+)/.*", txt, re.M)
               if not re.match(r"(docker|br-|veth|lxd|virbr|nordlynx|tun|wg)", m.group(1))]
        ifs.sort(key=lambda x: (not x[2], x[0].startswith("wl")))  # DHCP before static, wired before wifi
        out[HOSTS[h]] = list(dict.fromkeys([ip for _, ip, _ in ifs] + [HOSTS[h]]))
    return out

def lan_open_local():
    """Ports UFW lets in from the LAN (others only answer the local host itself)."""
    try: out = subprocess.run(["sudo", "-n", "ufw", "status"], capture_output=True, text=True, timeout=5).stdout
    except Exception: return None
    return {int(m) for m in re.findall(r"^(\d+)(?:/tcp)?\s+ALLOW", out, re.M)}

def probe(host, port):
    for scheme in ("http", "https"):
        r = fetch(f"{scheme}://{HOSTS[host]}:{port}/", body=True)
        if not r: continue
        b = r["body"].decode("utf-8", "replace")
        if r["code"] >= 500: return None
        if not (b.lstrip()[:1] == "<" or r["code"] in (301, 302, 303, 307, 308)): return None  # JSON APIs etc.
        m = re.search(r"<title>([^<]*)", b, re.I)
        return {"host": host, "port": port, "url": f"{scheme}://{HOSTS[host]}:{port}", "title": (m.group(1).strip() if m else "")[:60]}
    return None

items = [i for g in svc["groups"] for i in g["items"]]
ign = set(json.load(open(os.path.join(HERE, "ignore.json")))["ignore"])
known = set()
for i in items:
    for u in (i["url"], i.get("check", "")):
        m = re.match(r"https?://([\d.]+):(\d+)", u)
        if m:
            h = next((k for k, v in HOSTS.items() if v == m.group(1)), None)
            if h: known.add(f"{h}:{m.group(2)}")
with cf.ThreadPoolExecutor(16) as ex:
    res = dict(zip([i["url"] for i in items], ex.map(lambda i: check(i.get("check", i["url"])), items)))
    cands = [(h, p) for h in HOSTS for p in sorted(listening(h)) if f"{h}:{p}" not in known | ign]
    found = [d for d in ex.map(lambda hp: probe(*hp), cands) if d]
ufw = lan_open_local()
for d in found:
    d["lan"] = True if d["host"] != LOCAL or ufw is None else d["port"] in ufw

# ── Handshake + auto classification ─────────────────────────────────────────
# A tool can describe itself: GET /.well-known/hub.json ->
#   {"name": "...", "desc": "...", "group": "<group id or name>", "icon": "<symbol>"}
# Without a manifest: classification by page title/port (RULES). Unclear -> "Newly discovered".
GROUPS = {g["id"]: g["name"] for g in svc["groups"]}
RULES = [  # (regex on "title port", group id or name, icon); adjust to your groups
    (r"cloud|file ?browser|files|nas|samba|share|drive", "files", "folder"),
    (r"grafana|uptime|status|dashboard|monitor|netdata|prometheus", "monitoring", "chart"),
    (r"cockpit|portainer|router|vault|admin|desktop|vnc", "system", "gauge"),
]
def _gid(x):
    if not x: return None
    xl = str(x).lower()
    for gid, name in GROUPS.items():
        if xl == str(gid).lower() or xl in name.lower(): return gid
    return None
def manifest(d):
    r = fetch(d["url"] + "/.well-known/hub.json", body=True)
    if not r or r["code"] != 200: return None
    try: m = json.loads(r["body"].decode("utf-8", "replace"))
    except Exception: return None
    return m if isinstance(m, dict) else None
def classify(d):
    m = manifest(d)
    if m:
        d.update(name=str(m.get("name") or d["title"] or f"Port {d['port']}")[:60], desc=str(m.get("desc", ""))[:120],
                 icon=str(m.get("icon") or "spark"), group=_gid(m.get("group")), via="manifest")
        return d
    hay = f"{d['title']} {d['port']}".lower()
    for rx, g, icon in RULES:
        if d["title"] and re.search(rx, hay):
            d.update(name=d["title"], desc="auto classified", icon=icon, group=_gid(g), via="title")
            return d
    d.update(group=None, via=None)
    return d
with cf.ThreadPoolExecutor(8) as ex:
    found = list(ex.map(classify, found))
tmp = OUT + ".tmp"
try: ver = int(os.path.getmtime(os.path.join(os.path.dirname(OUT), "index.html")))
except OSError: ver = 0
json.dump({"checked": int(time.time()), "version": ver, "results": res, "discovered": found, "hostips": host_ips()}, open(tmp, "w"))
os.replace(tmp, OUT)
