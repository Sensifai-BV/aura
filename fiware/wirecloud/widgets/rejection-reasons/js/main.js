/* Rejection reasons — BarChart over HRISessionKPI.rejectionReasons (last 8 h). */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  function color(reason) {
    if (reason === 'not_addressed') return 'line-strong';      // engagement gate working as intended
    return reason.indexOf('low_confidence') === 0 ? 'warn' : 'danger';
  }

  function RejectionReasons() {
    var kpi = N.useEntity(W.IDS.kpi), items = W.itemsOf(kpi && kpi.rejectionReasons, { color: color });
    return h(A.Panel, { title: 'Rejection reasons', source: 'NGSI-LD · HRISessionKPI · last 8 h', live: W.liveFlag(kpi, 20) },
      items.length ? h(A.BarChart, { items: items }) : h(W.Empty, { text: 'No rejections this shift' }));
  }

  window.AuraWC.mount(RejectionReasons);
})();
