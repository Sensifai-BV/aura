"""Write the DDS Router configuration from environment variables and run it.

ROLE            server (listens on TCP, bridges to the local DDS domain, e.g. Orion-LD) or client (robot: connects out)
DDS_DOMAIN      local DDS domain (0 = real robot, 1 = Simulator)               [0]
DDS_CONFIG      Orion-LD mapping the allow-list is taken from                  [/etc/aura/config-dds.json]
WAN_PORT        TCP port (server: listening; client: the server's port)        [11800]
SERVER_ADDRESS  client only: the server's IP address or DNS name
PUBLIC_ADDRESS  server only: the public IP or DNS name the robot connects to (required behind Docker / NAT);
PUBLIC_PORT     the public TCP port if it differs from WAN_PORT
LOCAL_PEERS     optional, comma-separated host names or IPs of local DDS participants (Orion-LD) to reach by unicast;
                use it where multicast does not work (a shared Docker network). Without it the local side uses multicast.
LOCAL_IP        with LOCAL_PEERS: this container's address as the peers must reach it (default: resolved from hostname)
TLS_CERT_B64, TLS_KEY_B64, TLS_DH_B64, TLS_KEY_PASSWORD   server: base64 PEM files (TLS is on when the cert is set)
RATE_LIMIT_HZ   max samples per second forwarded on RATE_LIMIT_TOPICS (0 = off)  [2]
RATE_LIMIT_TOPICS  comma-separated DDS topics to limit  [rt/odom,rt/imu/data,rt/battery_state]
ROUTER_CONFIG_PATCH  YAML merged into the configuration (advanced, e.g. specs/qos)
ANNOUNCE_TYPES  true|false: with LOCAL_PEERS, announce the message types to Orion-LD [true]
TLS_AUTO        true: a server without TLS_CERT_B64 creates its own CA and certificate in TLS_DIR [/data/tls, a volume] and
                prints the CA (TLS_CA_B64=...) for the robot;  TLS_SAN  certificate names [DNS:localhost],
                e.g. "DNS:aura-dds.sensifai.com,IP:95.217.135.234"
TLS_CA_B64      base64 CA certificate: required by a client that verifies the server, optional on the server
"""
import base64
import os
import socket
import subprocess
import sys
import time

import yaml

role = os.environ.get("ROLE", "server")
domain = int(os.environ.get("DDS_DOMAIN", "0"))
port = int(os.environ.get("WAN_PORT", "11800"))
mapping = yaml.safe_load(open(os.environ.get("DDS_CONFIG", "/etc/aura/config-dds.json")))
topics = [t["name"] for t in mapping["dds"]["ddsmodule"]["dds"]["allowlist"]]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ddsrouter_topics import TYPES  # noqa: E402  (DDS type of every bridged topic)

missing = [t for t in topics if t not in TYPES]
if missing:
    sys.exit(f"no DDS type known for {missing}: add them to TYPES in ddsrouter_topics.py")

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

peers = [p.strip() for p in os.environ.get("LOCAL_PEERS", "").split(",") if p.strip()]
if peers:
    # Fast DDS metatraffic unicast port of the first participant of a domain: 7410 + 250 * domain
    meta = 7410 + 250 * domain
    own = os.environ.get("LOCAL_IP") or socket.gethostbyname(socket.gethostname())
    local = {"name": "Local", "kind": "initial-peers", "domain": domain,
             "listening-addresses": [{"ip": own, "port": meta, "transport": "udp"}],
             "connection-addresses": [{"ip": socket.gethostbyname(p), "port": meta, "transport": "udp"} for p in peers]}
else:
    local = {"name": "Local", "kind": "local", "domain": domain}
wan = {"name": "WAN", "kind": "wan", "domain": domain}
tls = {}
def generate_tls(directory):
    """First start of a server without TLS_* variables: create a private CA and a server certificate, keep them in the
    data volume (so they survive redeploys) and print the CA for the robot."""
    san = os.environ.get("TLS_SAN", "DNS:localhost")
    os.makedirs(directory, exist_ok=True)
    cert = f"{directory}/server.crt"
    if not os.path.exists(cert):
        def run(*args):
            subprocess.run(args, check=True, capture_output=True)
        run("openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "3650", "-subj", "/CN=aura-ddsrouter-ca",
            "-keyout", f"{directory}/ca.key", "-out", f"{directory}/ca.crt")
        run("openssl", "req", "-newkey", "rsa:3072", "-nodes", "-subj", "/CN=aura-ddsrouter",
            "-keyout", f"{directory}/server.key", "-out", f"{directory}/server.csr")
        with open(f"{directory}/san.ext", "w") as f:
            f.write(f"subjectAltName={san}\n")
        run("openssl", "x509", "-req", "-in", f"{directory}/server.csr", "-CA", f"{directory}/ca.crt",
            "-CAkey", f"{directory}/ca.key", "-CAcreateserial", "-days", "3650", "-extfile", f"{directory}/san.ext",
            "-out", cert)
        run("openssl", "genpkey", "-genparam", "-algorithm", "DH", "-pkeyopt", "group:ffdhe2048", "-out", f"{directory}/dh.pem")
        os.chmod(f"{directory}/ca.key", 0o600)
        os.chmod(f"{directory}/server.key", 0o600)
    with open(f"{directory}/ca.crt", "rb") as f:
        ca_b64 = base64.b64encode(f.read()).decode()
    print("TLS: server certificate in", directory, "- give the robot this CA as TLS_CA_B64:", flush=True)
    print("TLS_CA_B64=" + ca_b64, flush=True)
    return cert, f"{directory}/server.key", f"{directory}/dh.pem"


if role == "server":
    # Behind Docker's port mapping (or any NAT) the router must announce the address the robot actually connects to;
    # with 0.0.0.0 it announces its private container addresses, the robot cannot reach them and no data flows.
    public = os.environ.get("PUBLIC_ADDRESS", "")
    if public:
        listen = {"ip": socket.gethostbyname(public), "port": port, "transport": "tcp",
                  "external-port": int(os.environ.get("PUBLIC_PORT", port))}
    else:
        listen = {"ip": "0.0.0.0", "port": port, "transport": "tcp"}
    wan["listening-addresses"] = [listen]
    cert, key, dh = write_pem("TLS_CERT_B64", "server.crt"), write_pem("TLS_KEY_B64", "server.key"), write_pem("TLS_DH_B64", "dh.pem")
    if not cert and os.environ.get("TLS_AUTO", "true") == "true":
        cert, key, dh = generate_tls(os.environ.get("TLS_DIR", "/data/tls"))
    if cert:
        tls = {"cert": cert, "private_key": key, "dh_params": dh}
        if os.environ.get("TLS_KEY_PASSWORD"):
            tls["password"] = os.environ["TLS_KEY_PASSWORD"]
elif role == "client":
    server = os.environ["SERVER_ADDRESS"]
    while True:   # the robot may boot before its network or DNS is up
        try:
            server_ip = socket.gethostbyname(server)
            break
        except OSError as e:
            print(f"cannot resolve {server}: {e}; retrying", flush=True)
            time.sleep(5)
    wan["connection-addresses"] = [{"ip": server_ip, "port": port, "transport": "tcp"}]
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
# A robot publishes odometry and IMU at tens to hundreds of Hz; Orion-LD writes every sample to Mongo and TimescaleDB
# and fell over under that load. The dashboards need about one sample per second, so the router forwards at most
# RATE_LIMIT_HZ per listed topic (newer samples win). Event topics (transcripts, intents) are never limited.
rate = float(os.environ.get("RATE_LIMIT_HZ", "2"))
limited = [t for t in os.environ.get("RATE_LIMIT_TOPICS", "rt/odom,rt/imu/data,rt/battery_state").split(",") if t]
if rate > 0 and limited:
    config["topics"] = [{"name": t, "qos": {"max-tx-rate": rate}} for t in limited]
config.update(yaml.safe_load(os.environ.get("ROUTER_CONFIG_PATCH") or "{}"))   # advanced: extra top-level YAML keys
with open("/tmp/ddsrouter.yaml", "w") as f:
    yaml.safe_dump(config, f, sort_keys=False)
print(f"ddsrouter role={role} domain={domain} port={port} tls={'on' if tls else 'OFF'} topics={len(topics)}", flush=True)

if peers and os.environ.get("ANNOUNCE_TYPES", "true") == "true":
    # broker side: declare the message types on the local domain so Orion-LD subscribes (see ddsrouter-announcer.py)
    announcer_env = {**os.environ, "ROS_DOMAIN_ID": str(domain), "ROS_STATIC_PEERS": ";".join(peers)}
    subprocess.Popen(["bash", "-c", "sleep 5; source /opt/vulcanexus/jazzy/setup.bash && source /ws/install/setup.bash && "
                      "exec python3 /usr/local/bin/ddsrouter-announcer.py"], env=announcer_env)
sys.stdout.flush()
os.execvpe("bash", ["bash", "-c", "source /opt/vulcanexus/jazzy/setup.bash && exec ddsrouter -c /tmp/ddsrouter.yaml"], dict(os.environ))
