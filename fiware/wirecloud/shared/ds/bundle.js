/* @ds-bundle: {"format":4,"namespace":"Aura","components":[{"name":"Panel"},{"name":"ModuleTag"},{"name":"StateBadge"},{"name":"ConfidenceMeter"},{"name":"StatTile"},{"name":"LogTable"},{"name":"TimeSeriesChart"},{"name":"BarChart"},{"name":"PipelineMap"}]} */
(function () {
  var React = window.React, h = React.createElement, useState = React.useState, useRef = React.useRef, useEffect = React.useEffect;
  function cx() { return Array.prototype.filter.call(arguments, Boolean).join(' '); }

  var MODULES = ['stt', 'intent', 'gesture', 'speaker'];
  var STATES = {
    idle:         { label: 'Idle',         glyph: '○', tone: 'neutral' },
    listening:    { label: 'Listening',    glyph: '◉', tone: 'info' },
    transcribing: { label: 'Transcribing', glyph: '…', tone: 'info' },
    resolving:    { label: 'Resolving',    glyph: '…', tone: 'info' },
    confirming:   { label: 'Confirming',   glyph: '?',      tone: 'warn' },
    executing:    { label: 'Executing',    glyph: '▶', tone: 'accent' },
    accepted:     { label: 'Accepted',     glyph: '✓', tone: 'ok' },
    rejected:     { label: 'Rejected',     glyph: '✕', tone: 'danger' },
    safe_stop:    { label: 'Safe-stop',    glyph: '■', tone: 'stop' }
  };
  var HEALTH = { ok: { label: 'Healthy', glyph: '✓', c: 'ok' }, degraded: { label: 'Degraded', glyph: '!', c: 'warn' }, down: { label: 'Down', glyph: '✕', c: 'danger' } };
  var DEFAULT_THRESHOLDS = { reject: 0.5, accept: 0.8 };

  function band(v, th) { th = th || DEFAULT_THRESHOLDS; return v >= th.accept ? 'ok' : v >= th.reject ? 'warn' : 'danger'; }
  function pad(n, w) { n = String(n); while (n.length < w) n = '0' + n; return n; }
  function fmtTime(t, ms) {
    var d = t instanceof Date ? t : new Date(t);
    if (isNaN(d)) return String(t);
    var s = pad(d.getHours(), 2) + ':' + pad(d.getMinutes(), 2) + ':' + pad(d.getSeconds(), 2);
    return ms ? s + '.' + pad(d.getMilliseconds(), 3) : s;
  }
  function fmtNum(v, digits) { return v == null || isNaN(v) ? '—' : Number(v).toFixed(digits == null ? 2 : digits); }
  function colorVar(c) { return c ? (c.indexOf('#') === 0 ? c : 'var(--' + c + ')') : 'var(--ink-muted)'; }

  /* ---------------- Panel ---------------- */
  function Panel(p) {
    return h('section', { className: cx('au-panel', p.className), style: p.style },
      h('header', { className: 'au-panel-head' },
        h('h3', { className: 'au-panel-title' }, p.title),
        p.live === true ? h('span', { className: 'au-live' }, 'LIVE') : p.live === 'stale' ? h('span', { className: 'au-live au-stale' }, 'STALE') : null,
        p.source ? h('span', { className: 'au-panel-src', title: p.source }, p.source) : null),
      h('div', { className: cx('au-panel-body', p.flush && 'au-flush') }, p.children));
  }

  /* ---------------- ModuleTag ---------------- */
  function ModuleTag(p) {
    var m = MODULES.indexOf(p.module) >= 0 ? p.module : null;
    return h('span', { className: cx('au-tag', m && 'au-tag-' + m) }, p.module);
  }

  /* ---------------- StateBadge ---------------- */
  function StateBadge(p) {
    var s = STATES[p.state] || { label: p.state, glyph: '·', tone: 'neutral' };
    return h('span', { className: cx('au-badge', 'au-tone-' + s.tone) },
      h('span', { className: 'au-badge-g', 'aria-hidden': true }, s.glyph), p.label || s.label);
  }

  /* ---------------- ConfidenceMeter ---------------- */
  function ConfidenceMeter(p) {
    var th = p.thresholds || DEFAULT_THRESHOLDS, v = Math.max(0, Math.min(1, Number(p.value) || 0)), b = band(v, th);
    var word = { ok: 'accept', warn: 'confirm', danger: 'reject' }[b];
    return h('span', { className: 'au-meter', role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': 1, 'aria-valuenow': v, 'aria-label': 'Confidence ' + fmtNum(v) + ' (' + word + ')', title: word + ' band' },
      h('span', { className: 'au-meter-track' },
        h('span', { className: 'au-meter-fill au-fill-' + b, style: { width: v * 100 + '%' } }),
        h('span', { className: 'au-meter-tick', style: { left: th.reject * 100 + '%' } }),
        h('span', { className: 'au-meter-tick', style: { left: th.accept * 100 + '%' } })),
      h('span', { className: 'au-meter-val au-text-' + b }, fmtNum(v)));
  }

  /* ---------------- Sparkline (internal) ---------------- */
  function Sparkline(p) {
    var d = p.data || []; if (d.length < 2) return null;
    var min = Math.min.apply(null, d), max = Math.max.apply(null, d), span = max - min || 1;
    var pts = d.map(function (v, i) { return (i / (d.length - 1) * 96).toFixed(1) + ',' + (22 - (v - min) / span * 20).toFixed(1); });
    var last = pts[pts.length - 1].split(',');
    return h('svg', { className: 'au-spark', viewBox: '0 0 96 24', 'aria-hidden': true },
      h('polyline', { points: pts.join(' '), style: { fill: 'none', stroke: colorVar(p.color || 'ink-muted'), strokeWidth: 1.5, strokeLinejoin: 'round' } }),
      h('circle', { cx: last[0], cy: last[1], r: 2.5, style: { fill: colorVar(p.color || 'ink-muted') } }));
  }

  /* ---------------- StatTile ---------------- */
  function StatTile(p) {
    var delta = p.delta, dir = delta == null ? null : delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat';
    var good = dir === 'flat' || dir == null ? 'flat' : (p.higherIsBetter === false ? dir === 'down' : dir === 'up') ? 'good' : 'bad';
    return h('div', { className: cx('au-stat', p.className) },
      h('div', { className: 'au-stat-label' }, p.label),
      h('div', { className: 'au-stat-line' },
        h('span', { className: cx('au-stat-val', p.tone && 'au-text-' + p.tone) }, p.value),
        p.unit ? h('span', { className: 'au-stat-unit' }, p.unit) : null),
      h('div', { className: 'au-stat-foot' },
        h('span', null,
          dir ? h('span', { className: 'au-delta au-delta-' + good }, (dir === 'up' ? '▲ ' : dir === 'down' ? '▼ ' : '▬ ') + Math.abs(delta) + (p.deltaUnit || '')) : null,
          p.caption ? h('span', null, (dir ? '  ' : '') + p.caption) : null),
        p.spark ? h(Sparkline, { data: p.spark, color: p.sparkColor }) : null));
  }

  /* ---------------- LogTable ---------------- */
  function LogTable(p) {
    var rows = (p.rows || []).slice(0, p.maxRows || 200);
    var seen = useRef(null), fresh = {};
    if (seen.current) rows.forEach(function (r) { if (!seen.current[r.id]) fresh[r.id] = 1; });
    useEffect(function () { var s = {}; rows.forEach(function (r) { s[r.id] = 1; }); seen.current = s; });
    var cols = p.columns || ['time', 'module', 'event', 'detail', 'intent', 'confidence', 'state'];
    var head = { time: 'Time', module: 'Module', event: 'Event', detail: 'Transcript / detail', intent: 'Intent', confidence: 'Confidence', state: 'State', latency: 'Latency' };
    function cell(c, r) {
      switch (c) {
        case 'time': return h('td', { key: c, className: 'au-t' }, fmtTime(r.time, true));
        case 'module': return h('td', { key: c }, h(ModuleTag, { module: r.module }));
        case 'event': return h('td', { key: c, className: 'au-ev' }, r.event);
        case 'detail': return h('td', { key: c, className: 'au-tx', title: r.detail }, r.detail || h('span', { className: 'au-muted' }, '—'));
        case 'intent': return h('td', { key: c, className: 'au-in' }, r.intent || h('span', { className: 'au-muted' }, '—'));
        case 'confidence': return h('td', { key: c, className: 'au-cf' }, r.confidence == null ? h('span', { className: 'au-muted' }, '—') : h(ConfidenceMeter, { value: r.confidence, thresholds: p.thresholds }));
        case 'state': return h('td', { key: c }, r.state ? h(StateBadge, { state: r.state }) : null);
        case 'latency': return h('td', { key: c, className: 'au-t au-num' }, r.latencyMs == null ? '—' : r.latencyMs + ' ms');
      }
    }
    return h('div', { className: 'au-log', style: p.height ? { '--au-log-h': p.height + 'px' } : null },
      h('table', null,
        h('thead', null, h('tr', null, cols.map(function (c) { return h('th', { key: c, className: c === 'latency' ? 'au-num' : null }, head[c]); }))),
        h('tbody', null, rows.length ? rows.map(function (r) {
          return h('tr', { key: r.id, className: cx(r.state === 'safe_stop' && 'au-r-stop', fresh[r.id] && 'au-new') }, cols.map(function (c) { return cell(c, r); }));
        }) : h('tr', null, h('td', { colSpan: cols.length, className: 'au-log-empty' }, p.emptyText || 'Waiting for telemetry…')))));
  }

  /* ---------------- TimeSeriesChart ---------------- */
  function TimeSeriesChart(p) {
    var H = p.height || 180, W = 600, padL = 36, padB = 20, padT = 6;
    var series = p.series || [], all = [];
    series.forEach(function (s) { s.points.forEach(function (pt) { all.push(pt); }); });
    var hov = useState(null), hi = hov[0], setHi = hov[1], ref = useRef(null);
    if (!all.length) return h('div', { className: 'au-log-empty' }, 'No data');
    var t0 = Math.min.apply(null, all.map(function (q) { return +q.t; })), t1 = Math.max.apply(null, all.map(function (q) { return +q.t; }));
    var yMin = p.yMin != null ? p.yMin : 0, yMax = p.yMax != null ? p.yMax : Math.max.apply(null, all.map(function (q) { return q.v; })) * 1.1 || 1;
    var plotW = W - padL, plotH = H - padB - padT;
    function x(t) { return padL + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * plotW; }
    function y(v) { return padT + (1 - (v - yMin) / (yMax - yMin)) * plotH; }
    var ticks = [0, 0.5, 1].map(function (f) { return yMin + f * (yMax - yMin); });
    var digits = p.digits != null ? p.digits : yMax <= 2 ? 2 : 0;
    function onMove(e) {
      var b = ref.current.getBoundingClientRect(), fx = (e.clientX - b.left) / b.width * W;
      var t = t0 + Math.max(0, Math.min(1, (fx - padL) / plotW)) * (t1 - t0);
      setHi({ t: t, px: (x(t) / W) * 100 });
    }
    function nearest(s, t) { var best = null; s.points.forEach(function (q) { if (!best || Math.abs(q.t - t) < Math.abs(best.t - t)) best = q; }); return best; }
    return h('div', null,
      series.length > 1 || p.legend ? h('div', { className: 'au-legend' }, series.map(function (s) { return h('span', { key: s.name }, h('i', { style: { background: colorVar(s.color) } }), s.name); })) : null,
      h('div', { className: 'au-chart', style: { height: H + 'px' }, onMouseMove: onMove, onMouseLeave: function () { setHi(null); } },
        h('svg', { ref: ref, viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'none', style: { height: H + 'px' }, role: 'img', 'aria-label': p.ariaLabel || series.map(function (s) { return s.name; }).join(', ') },
          ticks.map(function (v, i) { return h('line', { key: 'g' + i, x1: padL, x2: W, y1: y(v), y2: y(v), style: { stroke: 'var(--chart-grid)', strokeWidth: 1 }, vectorEffect: 'non-scaling-stroke' }); }),
          h('line', { x1: padL, x2: W, y1: y(yMin), y2: y(yMin), style: { stroke: 'var(--line-strong)', strokeWidth: 1 }, vectorEffect: 'non-scaling-stroke' }),
          (p.thresholds || []).map(function (th, i) { return h('line', { key: 't' + i, x1: padL, x2: W, y1: y(th.value), y2: y(th.value), style: { stroke: colorVar(th.tone || 'warn'), strokeWidth: 1, strokeDasharray: '4 3' }, vectorEffect: 'non-scaling-stroke' }); }),
          series.map(function (s) {
            var d = s.points.map(function (q, i) { return (i ? 'L' : 'M') + x(q.t).toFixed(1) + ' ' + y(q.v).toFixed(1); }).join(' ');
            return h('path', { key: s.name, d: d, style: { fill: 'none', stroke: colorVar(s.color), strokeWidth: 2, strokeLinejoin: 'round' }, vectorEffect: 'non-scaling-stroke' });
          }),
          hi ? h('line', { x1: x(hi.t), x2: x(hi.t), y1: padT, y2: H - padB, style: { stroke: 'var(--chart-crosshair)', strokeWidth: 1 }, vectorEffect: 'non-scaling-stroke' }) : null),
        ticks.map(function (v, i) { return h('span', { key: 'yl' + i, className: 'au-chart-ylab', style: { top: y(v) + 'px' } }, fmtNum(v, digits)); }),
        (p.thresholds || []).map(function (th, i) { return h('span', { key: 'tl' + i, className: 'au-chart-thr', style: { top: y(th.value) + 'px', color: colorVar(th.tone || 'warn') } }, th.label); }),
        [t0, (t0 + t1) / 2, t1].map(function (t, i) { return h('span', { key: 'xl' + i, className: 'au-chart-xlab', style: { left: (x(t) / W * 100) + '%', transform: i === 2 ? 'translateX(-100%)' : i === 0 ? 'none' : null } }, fmtTime(t)); }),
        hi ? h('div', { className: 'au-tip', style: hi.px > 60 ? { right: (100 - hi.px) + '%', marginRight: 8 } : { left: hi.px + '%', marginLeft: 8 } },
          h('div', { className: 'au-muted au-mono' }, fmtTime(hi.t)),
          series.map(function (s) { var q = nearest(s, hi.t); return h('div', { key: s.name }, h('i', { style: { display: 'inline-block', width: 8, height: 3, marginRight: 6, verticalAlign: 'middle', background: colorVar(s.color) } }), s.name + ' ', h('b', null, fmtNum(q.v, digits) + (p.unit ? ' ' + p.unit : ''))); })) : null));
  }

  /* ---------------- BarChart ---------------- */
  function BarChart(p) {
    var items = p.items || [], max = p.max || Math.max.apply(null, items.map(function (i) { return i.value; }).concat([1]));
    return h('div', { className: 'au-bars', role: 'list' }, items.map(function (it) {
      return [
        h('span', { key: it.label + 'l', className: 'au-bar-l', role: 'listitem' }, it.label),
        h('span', { key: it.label + 't', className: 'au-bar-track', 'aria-hidden': true }, h('span', { className: 'au-bar-fill', style: { width: (it.value / max * 100) + '%', background: colorVar(it.color || p.color || 'accent') } })),
        h('span', { key: it.label + 'v', className: 'au-bar-v' }, (p.digits != null ? fmtNum(it.value, p.digits) : it.value) + (p.unit ? ' ' + p.unit : ''))
      ];
    }));
  }

  /* ---------------- PipelineMap ---------------- */
  function Node(p) {
    var st = p.status || {}, hl = HEALTH[st.health] || HEALTH.ok;
    return h('div', { className: cx('au-node', st.health === 'down' && 'au-down'), style: { '--node-c': 'var(--mod-' + p.module + ')', gridColumn: p.col, gridRow: p.row } },
      h('span', { className: 'au-node-n' }, p.module),
      h('span', { className: 'au-node-s au-text-' + hl.c }, h('span', { 'aria-hidden': true }, hl.glyph), hl.label),
      h('span', { className: 'au-node-r' }, (st.rate != null ? st.rate + ' ' + (st.rateUnit || 'Hz') : '—') + (st.latencyMs != null ? ' · ' + st.latencyMs + ' ms' : '')));
  }
  function Arrow(t, col, row) { return h('span', { className: 'au-arrow', 'aria-hidden': true, style: { gridColumn: col, gridRow: row } }, t); }
  function PipelineMap(p) {
    var m = p.modules || {}, sink = p.sink || {};
    return h('div', { className: 'au-pipe', role: 'group', 'aria-label': 'AURA module pipeline' },
      h(Node, { module: 'stt', status: m.stt, col: 1, row: 1 }), Arrow('→', 2, 1),
      h(Node, { module: 'intent', status: m.intent, col: 3, row: 1 }), Arrow('←', 4, 1),
      h(Node, { module: 'gesture', status: m.gesture, col: 5, row: 1 }),
      Arrow('↓', 3, 2),
      h(Node, { module: 'speaker', status: m.speaker, col: 3, row: 3 }),
      Arrow('↓', 3, 4),
      h('div', { className: 'au-sink', style: { gridRow: 5 } },
        h('span', null, h('b', null, sink.name || 'Orion-LD'), '  ROS 2 ↔ NGSI-LD bridge'),
        h('span', { className: 'au-mono' }, (sink.rate != null ? sink.rate + ' upd/s' : '') + (sink.lagMs != null ? ' · lag ' + sink.lagMs + ' ms' : ''))));
  }

  window.Aura = Object.assign(window.Aura || {}, {
    Panel: Panel, ModuleTag: ModuleTag, StateBadge: StateBadge, ConfidenceMeter: ConfidenceMeter, StatTile: StatTile,
    LogTable: LogTable, TimeSeriesChart: TimeSeriesChart, BarChart: BarChart, PipelineMap: PipelineMap,
    STATES: STATES, MODULES: MODULES, DEFAULT_THRESHOLDS: DEFAULT_THRESHOLDS, band: band
  });
})();
