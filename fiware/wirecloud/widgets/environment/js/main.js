/* Site environment — /artaban/environment (aura_interfaces/EnvironmentReading) on AirQualityObserved.reading,
 * against hazard limits. A reading with every field at 0 means the robot has no environment sensors; O₂ and
 * LAmax at 0 mean that one sensor is missing. Both show "no sensor", never a red zero. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  var ROWS = [
    { attr: 'h2s_ppm', label: 'H₂S', unit: 'ppm', digits: 1, max: 25, warn: 5, danger: 10 },
    { attr: 'ch4_lel', label: 'CH₄', unit: '%LEL', digits: 1, max: 25, warn: 5, danger: 10 },
    { attr: 'co_ppm', label: 'CO', unit: 'ppm', digits: 1, max: 50, warn: 25, danger: 35 },
    { attr: 'o2_vol', label: 'O₂', unit: '%vol', digits: 1, max: 25, low: true, warn: 20.5, danger: 19.5, zeroIsNoSensor: true },
    { attr: 'thermal_max', label: 'Thermal hotspot', unit: '°C', digits: 0, max: 150, warn: 80, danger: 110 },
    { attr: 'la_max', label: 'Noise LAmax', unit: 'dB', digits: 0, max: 120, warn: 85, danger: 95, zeroIsNoSensor: true }
  ];
  var ALL_FIELDS = ROWS.map(function (r) { return r.attr; }).concat(['temperature', 'relative_humidity', 'la_eq']);
  var GLYPH = { ok: '✓', warn: '!', danger: '✕' };

  function level(r, v) {
    if (r.low) return v < r.danger ? 'danger' : v < r.warn ? 'warn' : 'ok';
    return v > r.danger ? 'danger' : v > r.warn ? 'warn' : 'ok';
  }

  function Row(r, reading) {
    var v = r.zeroIsNoSensor ? W.sensed(reading[r.attr]) : W.num(reading[r.attr]);
    if (v == null) return [
      h('span', { key: r.attr + 'l', className: 'au-bar-l' }, r.label),
      h('span', { key: r.attr + 't', className: 'au-bar-track', 'aria-hidden': true }),
      h('span', { key: r.attr + 'v', className: 'au-bar-v wc-nosensor' }, W.NO_SENSOR)
    ];
    var lv = level(r, v);
    return [
      h('span', { key: r.attr + 'l', className: 'au-bar-l' }, r.label),
      h('span', { key: r.attr + 't', className: 'au-bar-track', 'aria-hidden': true },
        h('span', { className: 'au-bar-fill au-fill-' + lv, style: { width: Math.min(100, v / r.max * 100) + '%' } })),
      h('span', { key: r.attr + 'v', className: 'au-bar-v au-text-' + lv }, GLYPH[lv] + ' ' + W.fmt(v, r.digits) + ' ' + r.unit)
    ];
  }

  function Environment() {
    var e = N.useEntity(W.IDS.air), reading = e && W.msg(e.reading);
    var body = !(reading && reading.header) ? h(W.Empty)
      : W.allZero(reading, ALL_FIELDS) ? h(W.Empty, { text: 'No sensor: the robot publishes no gas, thermal or noise readings' })
      : h('div', { className: 'au-bars' }, ROWS.map(function (r) { return Row(r, reading); }));
    return h(A.Panel, { title: 'Site environment', source: 'ROS 2 /artaban/environment → NGSI-LD AirQualityObserved', live: W.liveFlag(e, 10) }, body);
  }

  window.AuraWC.mount(Environment);
})();
