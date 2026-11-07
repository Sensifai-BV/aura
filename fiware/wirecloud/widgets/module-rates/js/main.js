/* Event rate by module — BarChart over HRISessionKPI.moduleEventRates (HRIEvent notifications, last 5 min). */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  function ModuleRates() {
    var kpi = N.useEntity(W.IDS.kpi);
    var items = W.itemsOf(kpi && kpi.moduleEventRates, { order: A.MODULES, color: function (m) { return 'mod-' + m; } });
    return h(A.Panel, { title: 'Event rate by module', source: 'NGSI-LD · HRIEvent · last 5 min', live: W.liveFlag(kpi, 20) },
      items.length ? h(A.BarChart, { items: items, unit: '/min', digits: 1 }) : h(W.Empty));
  }

  window.AuraWC.mount(ModuleRates);
})();
