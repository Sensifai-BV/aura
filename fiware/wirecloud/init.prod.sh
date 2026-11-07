#!/bin/bash
# Production provisioning of WireCloud (wirecloud-init service, idempotent):
#   * builds the .wgt packages (widgets, operator, the Aura and Simulator mashups)
#   * creates / updates ONE admin account from the environment (no default admin/admin, ever)
#   * creates two dashboards for that admin: "aura" (real robot, DDS domain 0) and "simulator" (domain 1)
set -e
: "${WIRECLOUD_ADMIN_USER:?}" "${WIRECLOUD_ADMIN_PASSWORD:?}"
python3 /aura/src/build.py /aura/dist
cd /opt/wirecloud_instance

python manage.py shell <<'PY'
import os
from django.contrib.auth.models import User
name, password = os.environ["WIRECLOUD_ADMIN_USER"], os.environ["WIRECLOUD_ADMIN_PASSWORD"]
u, _ = User.objects.get_or_create(username=name, defaults={"email": "admin@sensifai.com"})
u.is_staff = u.is_superuser = u.is_active = True
u.set_password(password)  # the environment is the source of truth: a redeploy re-applies it
u.save()
if name != "admin":
    User.objects.filter(username="admin").delete()  # never leave the well-known default account behind
print("admin account ready:", name)
PY

installed=$(python manage.py shell -c "from wirecloud.catalogue.models import CatalogueResource as C
print(' '.join('%s_%s_%s.wgt' % (c.vendor, c.short_name, c.version) for c in C.objects.filter(vendor='Sensifai')))")
for f in $(ls /aura/dist/*.wgt | grep -v -e aura-dashboard -e aura-simulator) /aura/dist/Sensifai_aura-dashboard_*.wgt /aura/dist/Sensifai_aura-simulator_*.wgt; do
    case " $installed " in
        *" $(basename "$f") "*) echo "already installed: $(basename "$f")" ;;
        *) python manage.py addtocatalogue --public "$f" ;;
    esac
done

python /dev/stdin <<'PY'
import http.cookiejar, json, os, re, urllib.error, urllib.parse, urllib.request
base = "http://wirecloud:8000"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
page = op.open(base + "/login").read().decode()
token = re.search(r"name=[\'\"]csrfmiddlewaretoken[\'\"] value=[\'\"]([^\'\"]+)", page).group(1)
form = urllib.parse.urlencode({"csrfmiddlewaretoken": token, "username": os.environ["WIRECLOUD_ADMIN_USER"],
                               "password": os.environ["WIRECLOUD_ADMIN_PASSWORD"], "next": "/"}).encode()
op.open(urllib.request.Request(base + "/login", form, {"Referer": base + "/login"}))
csrf = next(c.value for c in jar if c.name == "csrftoken")
headers = {"Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": csrf, "Referer": base + "/"}
version = os.environ.get("AURA_VERSION", "1.2.3")
dashboards = [("aura", "Aura", "Sensifai/aura-dashboard/%s" % version),
              ("simulator", "Simulator", "Sensifai/aura-simulator/%s" % version)]
workspaces = json.load(op.open(urllib.request.Request(base + "/api/workspaces", headers=headers)))
for name, title, mashup in dashboards:
    existing = [w for w in workspaces if w["name"] == name]
    if existing and os.environ.get("AURA_RECREATE_WORKSPACE") == "1":
        for w in existing:
            op.open(urllib.request.Request(base + "/api/workspace/%s" % w["id"], headers=headers, method="DELETE"))
            print("deleted workspace %s (%s)" % (name, w["id"]))
        existing = []
    if existing:
        print("workspace %s already exists (AURA_RECREATE_WORKSPACE=1 rebuilds it from the mashup)" % name)
        continue
    body = json.dumps({"name": name, "title": title, "mashup": mashup}).encode()
    try:
        r = op.open(urllib.request.Request(base + "/api/workspaces", body, headers))
        print("created workspace %s -> %s" % (name, r.status))
    except urllib.error.HTTPError as e:
        raise SystemExit("workspace %s creation failed: %s %s" % (name, e.code, e.read().decode()[:1500]))
PY
echo "dashboards: ${ARISE_PUBLIC_URL:-http://localhost:8000}/${WIRECLOUD_ADMIN_USER}/aura  and  /${WIRECLOUD_ADMIN_USER}/simulator"
