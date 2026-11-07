"""Write the DDS Router configuration from environment variables and run it.

ROLE            server (listens on TCP, bridges to the local DDS domain, e.g. Orion-LD) or client (robot: connects out)
DDS_DOMAIN      local DDS domain (0 = real robot, 1 = Simulator)               [0]
DDS_CONFIG      Orion-LD mapping the allow-list is taken from                  [/etc/aura/config-dds.json]
WAN_PORT        TCP port (server: listening; client: the server's port)        [11800]
SERVER_ADDRESS  client only: the server's IP address or DNS name
LOCAL_PEERS     optional, comma-separated IPs of local DDS participants to reach by unicast (no multicast needed)
TLS_CERT_B64, TLS_KEY_B64, TLS_DH_B64, TLS_KEY_PASSWORD   server: base64 PEM files (TLS is on when the cert is set)
TLS_CA_B64      base64 CA certificate: required by a client that verifies the server, optional on the server
"""
import base64
import os
import socket
import subprocess
import sys

import yaml

role = os.environ.get("ROLE", "server")
domain = int(os.environ.get("DDS_DOMAIN", "0"))
port = int(os.environ.get("WAN_PORT", "11800"))
mapping = yaml.safe_load(open(os.environ.get("DDS_CONFIG", "/etc/aura/config-dds.json")))
topics = [t["name"] for t in mapping["dds"]["ddsmodule"]["dds"]["allowlist"]]

def write_pem(var, name):
    value = os.environ.get(var, "")
    if not value:
        return None
    path = f"/run/tls/{name}"
    os.makedirs("/run/tls", exist_ok=True)
    with open(path, "wb") as f:
        f.write(base64.b64decode(value))
    os.chmod(path, 0o600)
    return path

local = {"name": "Local", "kind": "local", "domain": domain}
wan = {"name": "WAN", "kind": "wan", "domain": domain}
tls = {}
if role == "server":
    wan["listening-addresses"] = [{"ip": "0.0.0.0", "port": port, "transport": "tcp"}]
    cert, key, dh = write_pem("TLS_CERT_B64", "server.crt"), write_pem("TLS_KEY_B64", "server.key"), write_pem("TLS_DH_B64", "dh.pem")
    if cert:
        tls = {"cert": cert, "private_key": key, "dh_params": dh}
        if os.environ.get("TLS_KEY_PASSWORD"):
            tls["password"] = os.environ["TLS_KEY_PASSWORD"]
elif role == "client":
    server = os.environ["SERVER_ADDRESS"]
    wan["connection-addresses"] = [{"ip": socket.gethostbyname(server), "port": port, "transport": "tcp"}]
else:
    sys.exit(f"ROLE must be server or client, not {role!r}")
ca = write_pem("TLS_CA_B64", "ca.crt")
if ca:
    tls["ca"] = ca
if tls:
    wan["tls"] = tls

config = {
    "version": "v5.0",
    "allowlist": [{"name": name, "type": "*"} for name in topics],
    "participants": [local, wan],
}
with open("/tmp/ddsrouter.yaml", "w") as f:
    yaml.safe_dump(config, f, sort_keys=False)
print(f"ddsrouter role={role} domain={domain} port={port} tls={'on' if tls else 'OFF'} topics={len(topics)}", flush=True)

env = dict(os.environ)
peers = [p.strip() for p in os.environ.get("LOCAL_PEERS", "").split(",") if p.strip()]
if peers:
    env["ROS_STATIC_PEERS"] = ";".join(peers)
sys.stdout.flush()
os.execvpe("bash", ["bash", "-c", "source /opt/vulcanexus/jazzy/setup.bash && exec ddsrouter -c /tmp/ddsrouter.yaml"], env)
