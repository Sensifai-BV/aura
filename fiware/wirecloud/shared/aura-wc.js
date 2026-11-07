/* AURA × WireCloud — shared NGSI-LD library packaged into every AURA widget.
 *
 *   current state   Orion-LD  GET /ngsi-ld/v1/entities…   via WireCloud proxy (MashupPlatform.http)
 *   history         Mintaka   GET /temporal/entities…     via WireCloud proxy
 *   live            wiring input "entities"  ← AURA NGSI-LD source operator ← ngsi-proxy ← Orion-LD subscription
 *
 * Robot / AirQualityObserved / HRIEvent / HRIModuleStatus attributes are whole ROS 2 messages written by
 * Orion-LD's DDS bridge (no observedAt: times come from modifiedAt). See fiware/docs/DATA_MODEL.md.
 *
 * Exposes window.AuraNGSI (fetch helpers + React hooks), window.AuraW (formatting helpers) and AuraWC.mount().
 */
(function () {
  var MP = window.MashupPlatform, React = window.React, h = React.createElement;
  var useState = React.useState, useEffect = React.useEffect, useRef = React.useRef;

  function pref(name, fallback) {
    try { var v = MP.prefs.get(name); return v === '' || v == null ? fallback : v; } catch (e) { return fallback; }
  }
  function base(name, fallback) { return String(pref(name, fallback)).replace(/\/+$/, ''); }
  function orion() { return base('ngsi_server', 'http://orion:1026') + '/ngsi-ld/v1'; }
  function mintaka() { return base('mintaka_server', 'http://mintaka:8080') + '/temporal'; }

  /* ---------- HTTP through the WireCloud proxy ---------- */
  function request(method, url, body) {
    return new Promise(function (resolve, reject) {
      var headers = { Accept: 'application/json' };
      var opts = {
        method: method, requestHeaders: headers,
        onSuccess: function (r) {
          var data = null;
          try { data = r.responseText ? JSON.parse(r.responseText) : null; } catch (e) { data = r.responseText; }
          resolve({ status: r.status, data: data, headers: r.getAllResponseHeaders ? r.getAllResponseHeaders() : '' });
        },
        onFailure: function (r) { reject(new Error((r && r.status) + ' ' + method + ' ' + url)); }
      };
      if (body !== undefined) { opts.contentType = 'application/json'; opts.postBody = JSON.stringify(body); }
      MP.http.makeRequest(url, opts);
    });
  }
  function getJSON(url) { return request('GET', url).then(function (r) { return r.data; }); }
  function qs(params) {
    return Object.keys(params).filter(function (k) { return params[k] != null; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
  }
  function isoAgo(minutes) { return new Date(Date.now() - minutes * 60000).toISOString().replace(/\.\d+Z$/, 'Z'); }
  function nowIso() { return new Date().toISOString(); }

  /* ---------- NGSI-LD helpers ---------- */
  // Orion-LD stores forbidden characters escaped (a " arrives as \"); show them as written.
  function clean(v) { return typeof v === 'string' && v.indexOf('\\"') >= 0 ? v.replace(/\\"/g, '"') : v; }
  function shortName(k) { var i = k.lastIndexOf('/'); return i >= 0 ? k.slice(i + 1) : k; }

  // Normalized entity → { id, type, <attr>: value, _observedAt, _at: {attr: observedAt} }
  function simplify(e) {
    var out = { id: e.id, type: shortName(e.type || ''), _at: {} }, latest = null;
    Object.keys(e).forEach(function (k) {
      if (k === 'id' || k === 'type' || k === '@context') return;
      var a = e[k], name = shortName(k);
      if (!a || typeof a !== 'object' || Array.isArray(a)) { out[name] = a; return; }
      var v = a.type === 'Relationship' ? a.object : a.value;
      if (v && typeof v === 'object' && '@value' in v) v = v['@value'];
      out[name] = clean(v);
      var when = a.observedAt || a.modifiedAt;  // DDS-bridged attributes carry only modifiedAt
      if (when) { out._at[name] = when; if (!latest || when > latest) latest = when; }
    });
    out._observedAt = latest;
    return out;
  }

  function query(type, opts) {
    opts = opts || {};
    return getJSON(orion() + '/entities?' + qs({ type: type, limit: opts.limit || 100, q: opts.q, options: 'sysAttrs' })).then(function (l) { return (l || []).map(simplify); });
  }
  function get(id) { return getJSON(orion() + '/entities/' + encodeURIComponent(id) + '?options=sysAttrs').then(simplify); }
  function upsert(entities) { return request('POST', orion() + '/entityOperations/upsert?options=update', entities); }
  function patchAttrs(id, attrs) { return request('PATCH', orion() + '/entities/' + encodeURIComponent(id) + '/attrs', attrs); }
  function prop(value) { return { type: 'Property', value: value, observedAt: nowIso() }; }

  // Mintaka temporalValues → { attr: [{t, v}] } (oldest first)
  function temporal(id, attrs, opts) {
    opts = opts || {};
    var url = mintaka() + '/entities/' + encodeURIComponent(id) + '?' + qs({
      attrs: attrs.join(','), timerel: 'after', timeAt: isoAgo(opts.minutes || 15), timeproperty: 'modifiedAt',
      options: 'temporalValues', lastN: opts.lastN
    });
    return getJSON(url).then(function (ent) {
      var out = {};
      attrs.forEach(function (a) {
        var x = ent && ent[a], vals = x && x.values ? x.values : [];
        out[a] = vals.map(function (p) { return { t: Date.parse(p[1]), v: clean(p[0]) }; }).sort(function (p, q) { return p.t - q.t; });
      });
      return out;
    });
  }
  // Rows of one entity's history: attributes written together are zipped by observedAt.
  function temporalRows(id, attrs, opts) {
    return temporal(id, attrs, opts).then(function (cols) {
      var byT = {};
      attrs.forEach(function (a) { cols[a].forEach(function (p) { (byT[p.t] = byT[p.t] || { _t: p.t })[a] = p.v; }); });
      return Object.keys(byT).map(function (k) { return byT[k]; }).sort(function (p, q) { return p._t - q._t; });
    });
  }

  /* ---------- live store, fed by wiring ---------- */
  var entities = {}, subs = [], linkSubs = [];
  var link = { status: 'waiting', last: 0, updates: [], lagMs: null, rate: 0 };

  function emit(ent) {
    var prev = entities[ent.id] || {};
    entities[ent.id] = Object.assign({}, prev, ent, { _at: Object.assign({}, prev._at, ent._at) });
    subs.forEach(function (s) { if (s.match(ent)) s.fn(entities[ent.id], ent); });
  }
  function onLink() { linkSubs.forEach(function (fn) { fn(Object.assign({}, link)); }); }

  function receive(data) {
    var list = typeof data === 'string' ? JSON.parse(data) : data;
    if (!Array.isArray(list)) list = [list];
    var now = Date.now();
    link.last = now; link.status = 'live';
    list.forEach(function (raw) {
      var ent = raw && raw._at ? raw : simplify(raw);
      link.updates.push(now);
      if (ent._observedAt) link.lagMs = Math.max(0, now - Date.parse(ent._observedAt));
      emit(ent);
    });
  }
  try { MP.wiring.registerCallback('entities', receive); } catch (e) { /* widget without the input */ }
  setInterval(function () {
    var now = Date.now();
    link.updates = link.updates.filter(function (t) { return now - t < 5000; });
    link.rate = Math.round(link.updates.length / 5 * 10) / 10;
    if (link.status === 'live' && now - link.last > 10000) link.status = 'stale';
    onLink();
  }, 1000);

  function subscribe(match, fn) {
    var s = { match: match, fn: fn };
    subs.push(s);
    return function () { subs = subs.filter(function (x) { return x !== s; }); };
  }

  /* ---------- React hooks ---------- */
  function useTick(ms) {
    var st = useState(0), set = st[1];
    useEffect(function () { var i = setInterval(function () { set(function (n) { return n + 1; }); }, ms || 1000); return function () { clearInterval(i); }; }, []);
  }
  function useLink() {
    var st = useState(Object.assign({}, link)), v = st[0], set = st[1];
    useEffect(function () { linkSubs.push(set); return function () { linkSubs = linkSubs.filter(function (f) { return f !== set; }); }; }, []);
    return v;
  }
  // One entity: initial GET from Orion-LD, then live updates from wiring (polls every `pollS` s when not wired).
  function useEntity(id, pollS) {
    var st = useState(entities[id] || null), v = st[0], set = st[1];
    useEffect(function () {
      var alive = true;
      function load() { get(id).then(function (e) { if (alive) emit(e); }).catch(function () {}); }
      load();
      var off = subscribe(function (e) { return e.id === id; }, function (e) { set(e); });
      var poll = setInterval(function () { if (link.status !== 'live') load(); }, (pollS || 10) * 1000);
      return function () { alive = false; off(); clearInterval(poll); };
    }, [id]);
    return v;
  }
  // All entities of a type (initial query + live upserts).
  function useEntities(type, opts) {
    var st = useState([]), v = st[0], set = st[1], map = useRef({});
    useEffect(function () {
      var alive = true;
      function push() { set(Object.keys(map.current).map(function (k) { return map.current[k]; })); }
      function load() {
        query(type, opts).then(function (list) {
          if (!alive) return;
          list.forEach(function (e) { map.current[e.id] = Object.assign({}, map.current[e.id], e); }); push();
        }).catch(function () {});
      }
      load();
      var off = subscribe(function (e) { return e.type === type; }, function (e) { map.current[e.id] = e; push(); });
      var poll = setInterval(function () { if (link.status !== 'live') load(); }, 15000);
      return function () { alive = false; off(); clearInterval(poll); };
    }, [type]);
    return v;
  }
  // One attribute over time: Mintaka backfill, then live points. Returns [{t, v}].
  function useSeries(id, attr, minutes, pick) {
    var st = useState([]), v = st[0], set = st[1];
    useEffect(function () {
      var alive = true, pts = [], keep = (minutes || 15) * 60000;
      function val(x) { return pick ? pick(x) : x; }
      function trim() { var cut = Date.now() - keep; while (pts.length && pts[0].t < cut) pts.shift(); }
      temporal(id, [attr], { minutes: minutes }).then(function (cols) {
        if (!alive) return;
        var back = cols[attr].map(function (p) { return { t: p.t, v: val(p.v) }; }).filter(function (p) { return typeof p.v === 'number'; });
        var firstLive = pts.length ? pts[0].t : Infinity;
        pts = back.filter(function (p) { return p.t < firstLive; }).concat(pts);
        trim(); set(pts.slice());
      }).catch(function () {});
      var off = subscribe(function (e) { return e.id === id && attr in e; }, function (all, e) {
        var t = Date.parse(e._at[attr] || e._observedAt) || Date.now(), x = val(e[attr]);
        if (typeof x !== 'number' || (pts.length && pts[pts.length - 1].t >= t)) return;
        pts.push({ t: t, v: x }); trim(); set(pts.slice());
      });
      return function () { alive = false; off(); };
    }, [id, attr, minutes]);
    return v;
  }
  // Event log over several (entity, attribute) streams — each DDS topic is one attribute that changes once
  // per message. sources: [{id, attr, row(value) → row fields | null}]. Mintaka backfill + live. Newest first.
  function useEventLog(sources, opts) {
    opts = opts || {};
    var st = useState([]), v = st[0], set = st[1];
    useEffect(function () {
      var alive = true, rows = [], max = opts.max || 200, seen = {}, last = {};
      function add(src, t, value) {
        var k = src.id + '|' + src.attr + '|' + t;
        if (seen[k] || value == null || value === 'uninitialized') return false;
        var r = src.row(value); if (!r) return false;
        seen[k] = 1; rows.push(Object.assign({ id: k, _t: t }, r)); return true;
      }
      function flush() { rows.sort(function (a, b) { return b._t - a._t; }); rows = rows.slice(0, max); set(rows.slice()); }
      function backfill() {
        return Promise.all(sources.map(function (src) {
          return temporal(src.id, [src.attr], { minutes: opts.minutes || 30 }).then(function (cols) {
            cols[src.attr].forEach(function (p) { add(src, p.t, p.v); });
          }).catch(function () {});
        })).then(function () { if (alive) flush(); });
      }
      backfill();
      var offs = sources.map(function (src) {
        return subscribe(function (e) { return e.id === src.id && src.attr in e; }, function (all, e) {
          var t = Date.parse(e._at[src.attr]) || Date.now(), key = src.id + '|' + src.attr;
          if (last[key] && t <= last[key]) return;   // notification for another attribute of the entity
          last[key] = t;
          if (add(src, t, e[src.attr])) flush();
        });
      });
      var poll = setInterval(function () { if (link.status !== 'live') backfill(); }, 10000);
      return function () { alive = false; offs.forEach(function (f) { f(); }); clearInterval(poll); };
    }, []);
    return v;
  }

  /* ---------- formatting helpers shared by the widgets ---------- */
  var IDS = {
    kpi: 'urn:ngsi-ld:HRISessionKPI:artaban-01',
    robot: 'urn:ngsi-ld:Robot:artaban-01',
    air: 'urn:ngsi-ld:AirQualityObserved:artaban-01',
    hriEvent: function (m) { return 'urn:ngsi-ld:HRIEvent:' + m; },
    moduleStatus: function (m) { return 'urn:ngsi-ld:HRIModuleStatus:' + m; }
  };
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function fmt(v, digits) { v = num(v); return v == null ? '—' : v.toFixed(digits == null ? 0 : digits); }
  function liveFlag(ent, staleAfterS) {
    if (!ent || !ent._observedAt) return false;
    return (Date.now() - Date.parse(ent._observedAt)) / 1000 > (staleAfterS || 15) ? 'stale' : true;
  }
  function deltaOver(points, minutes, digits) {
    if (!points || points.length < 2) return null;
    var last = points[points.length - 1], cut = last.t - minutes * 60000, b = null;
    for (var i = 0; i < points.length; i++) { if (points[i].t <= cut) b = points[i]; else break; }
    if (!b) b = points[0];
    var f = Math.pow(10, digits == null ? 1 : digits);
    return Math.round((last.v - b.v) * f) / f;
  }
  function sparkOf(points, n) {
    if (!points || points.length < 2) return null;
    var step = Math.max(1, Math.floor(points.length / (n || 30)));
    return points.filter(function (_, i) { return i % step === 0 || i === points.length - 1; }).map(function (p) { return p.v; });
  }
  function tone(v, rule) {
    v = num(v); if (v == null || !rule) return undefined;
    if (rule.danger && rule.danger(v)) return 'danger';
    if (rule.warn && rule.warn(v)) return 'warn';
    return undefined;
  }
  function itemsOf(obj, opts) {
    opts = opts || {};
    var items = Object.keys(obj || {}).filter(function (k) { return k !== 'none'; })
      .map(function (k) { return { label: k, value: Number(obj[k]) || 0, color: opts.color ? opts.color(k) : undefined }; });
    if (opts.order) items.sort(function (a, b) { return opts.order.indexOf(a.label) - opts.order.indexOf(b.label); });
    else items.sort(function (a, b) { return b.value - a.value; });
    return opts.limit ? items.slice(0, opts.limit) : items;
  }
  /* ---------- ROS 2 message helpers (values as the DDS bridge stores them) ---------- */
  function msg(v) { return v && typeof v === 'object' ? v : {}; }
  // std_msgs/* → its data field
  function data(v) { return v && typeof v === 'object' && 'data' in v ? v.data : v; }
  // aura_interfaces/ModuleStatus (keys[] + values[] string arrays) → {health, message, values{key: value}}
  // (also reads the older diagnostic_msgs/DiagnosticStatus list of {key, value})
  function diag(v) {
    var d = msg(v), values = {};
    (d.values || []).forEach(function (kv, i) {
      if (kv && typeof kv === 'object') values[kv.key] = kv.value;
      else if (d.keys && d.keys[i] !== undefined) values[d.keys[i]] = kv;
    });
    return { health: d.level === 0 ? 'ok' : d.level === 1 ? 'degraded' : d.level === 2 ? 'down' : 'degraded', message: d.message, values: values };
  }
  // Robot entity (BatteryState + Odometry + ArtabanStatus) → flat values for the widgets
  function robot(e) {
    if (!e) return null;
    var b = msg(e.battery), s = msg(e.status), o = msg(e.odometry), pos = msg(msg(msg(o.pose).pose).position);
    var temps = {};
    (s.joint_names || []).forEach(function (n, i) { temps[n] = (s.joint_temperatures || [])[i]; });
    return {
      batteryLevel: num(b.percentage) != null ? b.percentage * 100 : null, charging: b.power_supply_status === 1,
      speed: num(msg(msg(msg(o.twist).twist).linear).x), x: num(pos.x), y: num(pos.y),
      missionId: s.mission_id, missionState: s.mission_state, missionProgress: s.mission_progress,
      currentWaypoint: s.current_waypoint, operationMode: s.operation_mode, gait: s.gait, safetyState: s.safety_state,
      eStop: s.e_stop, odometry: s.odometry_m, jointTemperatures: temps, maxJointTemperature: s.max_joint_temperature,
      cpuLoad: s.cpu_load, gpuLoad: s.gpu_load, socTemperature: s.soc_temperature, linkLatency: s.link_latency_ms
    };
  }
  var DECISION = ['ACCEPT', 'CONFIRM', 'REJECT', 'IGNORE'];

  function Empty(p) { return h('div', { className: 'au-log-empty' }, p.text || 'Waiting for telemetry…'); }

  /* ---------- mounting ---------- */
  function applyTheme() { document.documentElement.dataset.theme = pref('theme', 'dark') === 'light' ? 'light' : 'dark'; }
  function mount(Component, props) {
    applyTheme();
    try { MP.prefs.registerCallback(function () { applyTheme(); }); } catch (e) {}
    useTick; // keep the helper exported for widgets that need a clock
    window.ReactDOM.createRoot(document.getElementById('root')).render(h(Component, props || {}));
  }

  window.AuraNGSI = {
    simplify: simplify, query: query, get: get, temporal: temporal, temporalRows: temporalRows,
    upsert: upsert, patchAttrs: patchAttrs, prop: prop, request: request, pref: pref, subscribe: subscribe,
    useLink: useLink, useEntity: useEntity, useEntities: useEntities, useSeries: useSeries, useEventLog: useEventLog, useTick: useTick
  };
  window.AuraW = { IDS: IDS, num: num, fmt: fmt, liveFlag: liveFlag, deltaOver: deltaOver, sparkOf: sparkOf, tone: tone, itemsOf: itemsOf, Empty: Empty,
    msg: msg, data: data, diag: diag, robot: robot, DECISION: DECISION };
  window.AuraWC = { mount: mount, pref: pref };
})();
