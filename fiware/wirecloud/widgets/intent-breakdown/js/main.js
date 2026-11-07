/* Intents this shift — BarChart over HRISessionKPI.intentCounts (last 8 h). */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  function IntentBreakdown() {
    var kpi = N.useEntity(W.IDS.kpi), items = W.itemsOf(kpi && kpi.intentCounts, { limit: 9 });
    return h(A.Panel, { title: 'Intents this shift', source: 'NGSI-LD · HRISessionKPI · last 8 h', live: W.liveFlag(kpi, 20) },
      items.length ? h(A.BarChart, { items: items, color: 'mod-intent' }) : h(W.Empty, { text: 'No commands yet' }));
  }

  window.AuraWC.mount(IntentBreakdown);
})();
