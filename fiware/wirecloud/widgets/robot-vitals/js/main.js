/* Artaban vitals — StatTiles (a sensor published as 0 where 0 cannot be real shows "no sensor") over the Robot entity (/battery_state, /odom, /artaban/status) and
 * AirQualityObserved.reading (/artaban/environment), sparklines from Mintaka. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;
  var M = W.msg;

  var TILES = [
    { id: 'robot', attr: 'battery', label: 'Battery', unit: '%', digits: 0, color: 'ok',
      pick: function (b) { var p = W.num(M(b).percentage); return p == null ? null : p * 100; },
      rule: { danger: function (v) { return v < 12; }, warn: function (v) { return v < 25; } } },
    { id: 'robot', attr: 'odometry', label: 'Speed', unit: 'm/s', digits: 2, color: 'mod-voice',
      pick: function (o) { return W.num(M(M(M(M(o).twist).twist).linear).x); } },
    { id: 'robot', attr: 'status', label: 'Mission progress', unit: '%', digits: 0, color: 'accent',
      pick: function (s) { return W.num(M(s).mission_progress); } },
    { id: 'robot', attr: 'status', key: 'maxJoint', label: 'Max joint temp', unit: '°C', digits: 0, lowerBetter: true, color: 'mod-gesture', zeroIsNoSensor: true,
      pick: function (s) { return W.num(M(s).max_joint_temperature); },
      rule: { danger: function (v) { return v > 70; }, warn: function (v) { return v > 60; } } },
    { id: 'robot', attr: 'status', key: 'link', label: 'Link latency', unit: 'ms', digits: 0, lowerBetter: true, color: 'mod-stt', zeroIsNoSensor: true,
      pick: function (s) { return W.num(M(s).link_latency_ms); },
      rule: { danger: function (v) { return v > 250; }, warn: function (v) { return v > 120; } } },
    { id: 'air', attr: 'reading', label: 'Noise LAeq', unit: 'dB', digits: 0, lowerBetter: true, color: 'mod-speaker', zeroIsNoSensor: true,
      pick: function (r) { return W.num(M(r).la_eq); },
      rule: { danger: function (v) { return v > 85; }, warn: function (v) { return v > 75; } } }
  ];

  function Tile(p) {
    var t = p.tile, ent = p.ents[t.id];
    var series = N.useSeries(W.IDS[t.id], t.attr, 10, t.pick);
    var has = !!(ent && ent[t.attr] && ent[t.attr] !== 'uninitialized');
    var v = has ? t.pick(ent[t.attr]) : null;
    // Robot publishes 0 for a sensor it does not have (zeroIsNoSensor): "no sensor", no delta, spark or colour.
    if (t.zeroIsNoSensor && has && W.sensed(v) == null)
      return h(A.StatTile, { label: t.label, value: '—', unit: t.unit, caption: W.NO_SENSOR });
    var d = W.deltaOver(series, 5, t.digits);
    return h(A.StatTile, {
      label: t.label, value: W.fmt(v, t.digits), unit: t.unit, delta: d, higherIsBetter: !t.lowerBetter,
      caption: 'vs 5 min ago', spark: W.sparkOf(series), sparkColor: t.color, tone: W.tone(v, t.rule)
    });
  }

  function RobotVitals() {
    var ents = { robot: N.useEntity(W.IDS.robot), air: N.useEntity(W.IDS.air) };
    return h('div', { className: 'wc-kpis', 'aria-label': 'Artaban vitals' },
      TILES.map(function (t) { return h(Tile, { key: t.key || t.attr + t.label, tile: t, ents: ents }); }));
  }

  window.AuraWC.mount(RobotVitals);
})();
