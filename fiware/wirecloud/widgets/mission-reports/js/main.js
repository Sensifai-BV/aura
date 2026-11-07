/* Mission reports — MissionReport entities (one per mission), newest first, with links to the printable report. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  var VERDICT = { pass: ['ok', '✓'], watch: ['warn', '!'], fail: ['danger', '✕'] };

  function MissionReports(p) {
    var reports = N.useEntities('MissionReport', { limit: 1000 });
    var msg = React.useState(''), note = msg[0], setNote = msg[1];
    var rows = reports.slice().sort(function (a, b) { return String(b.missionId).localeCompare(String(a.missionId)); }).slice(0, p.max || 8);
    var reportBase = String(N.pref('report_base_url', 'http://localhost:8090')).replace(/\/+$/, '');
    // Request a report through the context broker: aura-monitor is subscribed to HRISessionKPI.
    function generate() {
      setNote('Requested…');
      N.request('POST', N.pref('ngsi_server', 'http://orion:1026').replace(/\/+$/, '') + '/ngsi-ld/v1/entities/' + W.IDS.kpi + '/attrs',
        { reportRequestedAt: N.prop(new Date().toISOString()) })
        .then(function () { setNote('Report requested via Orion-LD'); }).catch(function () { setNote('Request failed'); });
    }
    return h(A.Panel, { title: 'Mission reports', source: 'NGSI-LD · MissionReport · ' + reports.length + ' missions' },
      h('div', { className: 'au-log', style: { '--au-log-h': '300px' } },
        h('table', null,
          h('thead', null, h('tr', null, ['Mission', 'Ended', 'Success', 'p95 latency', 'Alerts', 'Verdict', ''].map(function (c, i) {
            return h('th', { key: i, className: i >= 2 && i <= 4 ? 'au-num' : null }, c);
          }))),
          h('tbody', null, rows.length ? rows.map(function (r) {
            var v = VERDICT[r.verdict] || ['neutral', '·'];
            return h('tr', { key: r.id },
              h('td', { className: 'au-in' }, r.missionId),
              h('td', { className: 'au-t' }, String(r.endedAt || '').slice(5, 16).replace('T', ' ')),
              h('td', { className: 'au-t au-num' }, W.fmt(r.commandSuccessRate, 1) + ' %'),
              h('td', { className: 'au-t au-num' }, W.fmt(r.p95Latency, 0) + ' ms'),
              h('td', { className: 'au-t au-num' }, W.fmt(r.alertsCount, 0)),
              h('td', null, h('span', { className: 'au-badge au-tone-' + v[0] }, h('span', { className: 'au-badge-g', 'aria-hidden': true }, v[1]), r.verdict + (r.status === 'interim' ? ' · interim' : ''))),
              h('td', null, h('a', { className: 'wc-link', href: reportBase + '/reports/' + encodeURIComponent(r.missionId), target: '_blank', rel: 'noopener' }, 'Open')));
          }) : h('tr', null, h('td', { colSpan: 7, className: 'au-log-empty' }, 'No reports yet'))))),
      h('div', { className: 'wc-row-end' },
        h('span', { className: 'au-muted', role: 'status' }, note),
        h('button', { type: 'button', className: 'wc-btn', onClick: generate }, 'Generate report for current mission')));
  }

  window.AuraWC.mount(MissionReports);
})();
