#!/bin/bash
# One-shot WireCloud provisioning for the AURA dashboard (runs in the wirecloud-init service).
set -e
# Build the .wgt packages from the mounted sources (widgets, operator, mashup).
python3 /aura/src/build.py /aura/dist
cd /opt/wirecloud_instance

# Local admin (admin / admin) — same convention as the image's `createdefaultsuperuser`.
echo "from django.contrib.auth.models import User
User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin', 'admin@example.com', 'admin')" | python manage.py shell

# AURA widgets + operator, then the mashup, into the catalogue (public). One file per call: a batch
# that hits an already-installed component rolls back as a whole. `--redeploy` does not persist with
# the SQLite backend, so new code ships as a new VERSION in build.py.
installed=$(python manage.py shell -c "from wirecloud.catalogue.models import CatalogueResource as C
print(' '.join('%s_%s_%s.wgt' % (c.vendor, c.short_name, c.version) for c in C.objects.filter(vendor='Sensifai')))")
for f in $(ls /aura/dist/*.wgt | grep -v -e aura-dashboard -e aura-simulator) /aura/dist/Sensifai_aura-dashboard_*.wgt; do
    case " $installed " in
        *" $(basename "$f") "*) echo "already installed: $(basename "$f")" ;;
        *) python manage.py addtocatalogue --public "$f" ;;
    esac
done

# The AURA workspace for admin, created from the mashup through WireCloud's REST API.
python /dev/stdin <<'PY'
import http.cookiejar, json, re, urllib.request
base = "http://wirecloud:8000"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
page = op.open(base + "/login").read().decode()
token = re.search(r"name=[\'\"]csrfmiddlewaretoken[\'\"] value=[\'\"]([^\'\"]+)", page).group(1)
form = urllib.parse.urlencode({"csrfmiddlewaretoken": token, "username": "admin", "password": "admin", "next": "/"}).encode()
op.open(urllib.request.Request(base + "/login", form, {"Referer": base + "/login"}))
csrf = next(c.value for c in jar if c.name == "csrftoken")
headers = {"Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": csrf, "Referer": base + "/"}
workspaces = json.load(op.open(urllib.request.Request(base + "/api/workspaces", headers=headers)))
import os
existing = [w for w in workspaces if w["name"] == "aura"]
if existing and os.environ.get("AURA_RECREATE_WORKSPACE") == "1":
    for w in existing:
        op.open(urllib.request.Request(base + "/api/workspace/%s" % w["id"], headers=headers, method="DELETE"))
        print("deleted workspace admin/aura (%s)" % w["id"])
    existing = []
if existing:
    print("workspace admin/aura already exists (set AURA_RECREATE_WORKSPACE=1 to rebuild it from the mashup)")
else:
    body = json.dumps({"name": "aura", "title": "AURA · Artaban", "mashup": "Sensifai/aura-dashboard/%s" % os.environ.get("AURA_VERSION", "1.2.0")}).encode()
    try:
        r = op.open(urllib.request.Request(base + "/api/workspaces", body, headers))
        print("created workspace admin/aura:", r.status)
    except urllib.error.HTTPError as e:
        raise SystemExit("workspace creation failed: %s %s" % (e.code, e.read().decode()[:1500]))
PY
echo "AURA dashboard: http://localhost:8000/admin/aura"
