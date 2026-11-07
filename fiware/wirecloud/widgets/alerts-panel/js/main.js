/* Active alerts — NGSI-LD Alert entities (FIWARE smart data model) raised by aura-monitor.
 * Acknowledge / resolve PATCH the Alert's status in Orion-LD; aura-monitor follows the change. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  var SEV = { critical: ['stop', '■', 0], high: ['danger', '✕', 1], medium: ['warn', '!', 2], low: ['info', '?', 3], informational: ['neutral', '·', 4] };

  function AlertRow(p) {
    var a = p.alert, s = SEV[a.severity] || SEV.informational;
    var busy = React.useState(false), isBusy = busy[0], setBusy = busy[1];
    function act(action) {
      setBusy(true);
      var attrs = { status: N.prop(action === 'ack' ? 'acknowledged' : 'resolved') };
      if (action === 'resolve') attrs.validTo = N.prop(new Date().toISOString());
      N.patchAttrs(a.id, attrs).catch(function () {}).then(function () { setBusy(false); });
    }
    return h('li', { className: 'wc-alert' + (a.status === 'acknowledged' ? ' is-acked' : '') },
      h('span', { className: 'au-badge au-tone-' + s[0] }, h('span', { className: 'au-badge-g', 'aria-hidden': true }, s[1]), a.severity),
      h('div', { className: 'wc-alert-body' },
        h('div', { className: 'wc-alert-head' },
          h('span', { className: 'au-mono wc-alert-rule' }, a.subCategory),
          h('span', { className: 'au-muted au-mono' }, a.category + ' · ' + A_time(a.dateIssued) + (a.status === 'acknowledged' ? ' · acknowledged' : ''))),
        h('div', null, a.description)),
      h('div', { className: 'wc-alert-act' },
        a.status === 'active' ? h('button', { type: 'button', className: 'wc-btn', disabled: isBusy, onClick: function () { act('ack'); } }, 'Acknowledge') : null,
        h('button', { type: 'button', className: 'wc-btn', disabled: isBusy, onClick: function () { act('resolve'); } }, 'Resolve')));
  }

  function A_time(iso) {
    var d = new Date(iso); if (isNaN(d)) return '—';
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function AlertsPanel() {
    var all = N.useEntities('Alert', { limit: 1000 });
    var open = all.filter(function (a) { return a.status === 'active' || a.status === 'acknowledged'; })
      .sort(function (x, y) { return (SEV[x.severity] || SEV.informational)[2] - (SEV[y.severity] || SEV.informational)[2] || String(y.dateIssued).localeCompare(String(x.dateIssued)); });
    return h(A.Panel, { title: 'Active alerts', source: 'NGSI-LD · Alert · ' + open.length + ' open', live: true },
      open.length ? h('ul', { className: 'wc-alerts' }, open.map(function (a) { return h(AlertRow, { key: a.id, alert: a }); }))
        : h('div', { className: 'au-log-empty' }, h('span', { className: 'au-text-ok' }, '✓ '), 'No open alerts'));
  }

  window.AuraWC.mount(AlertsPanel);
})();
