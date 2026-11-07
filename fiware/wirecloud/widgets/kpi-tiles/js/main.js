/* KPI row — StatTiles over HRISessionKPI (rolling 15 min window), sparklines + deltas from Mintaka. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  var TILES = [
    { attr: 'avgConfidence', label: 'Avg confidence', digits: 2, color: 'mod-intent', rule: { danger: function (v) { return v < 0.5; }, warn: function (v) { return v < 0.7; } } },
    { attr: 'commandSuccessRate', label: 'Command success', unit: '%', digits: 1, color: 'ok', rule: { danger: function (v) { return v < 80; }, warn: function (v) { return v < 88; } } },
    { attr: 'e2eLatencyMs', label: 'Avg e2e latency', unit: 'ms', digits: 0, lowerBetter: true, color: 'mod-stt', rule: { danger: function (v) { return v > 600; }, warn: function (v) { return v > 400; } } },
    { attr: 'falseActivationsPerHour', label: 'False activations', unit: '/h', digits: 1, lowerBetter: true, color: 'danger', rule: { danger: function (v) { return v > 6; }, warn: function (v) { return v > 2; } } },
    { attr: 'meanTimeToSafeStop', label: 'Time-to-safe-stop', unit: 'ms', digits: 0, lowerBetter: true, color: 'mod-speaker', rule: { danger: function (v) { return v > 1500; }, warn: function (v) { return v > 1000; } } },
    { attr: 'eventsPerMin', label: 'Events', unit: '/min', digits: 1, color: 'accent' }
  ];

  function Tile(p) {
    var t = p.tile, kpi = p.kpi;
    var series = N.useSeries(W.IDS.kpi, t.attr, 60);
    var v = kpi ? kpi[t.attr] : null;
    var d = W.deltaOver(series, 15, t.digits);
    return h(A.StatTile, {
      label: t.label, value: W.fmt(v, t.digits), unit: t.unit,
      delta: d === 0 ? 0 : d, deltaUnit: t.unit && t.unit !== '%' ? '' : '', higherIsBetter: !t.lowerBetter,
      caption: 'vs 15 min ago', spark: W.sparkOf(series), sparkColor: t.color, tone: W.tone(v, t.rule)
    });
  }

  function KpiTiles() {
    var kpi = N.useEntity(W.IDS.kpi);
    return h('div', { className: 'wc-kpis', 'aria-label': 'HRI KPIs, last 15 minutes' },
      TILES.map(function (t) { return h(Tile, { key: t.attr, tile: t, kpi: kpi }); }));
  }

  window.AuraWC.mount(KpiTiles);
})();
