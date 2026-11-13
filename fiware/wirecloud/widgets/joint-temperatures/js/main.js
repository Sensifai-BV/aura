/* Joint temperatures — BarChart of ArtabanStatus.joint_names × joint_temperatures (/artaban/status), coloured by limit.
 * A robot that publishes /artaban/status without joint temperatures (empty or all 0) shows "no sensor". */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  function JointTemperatures() {
    var e = N.useEntity(W.IDS.robot), r = W.robot(e), temps = (r && r.jointTemperatures) || {};
    var hasStatus = !!(e && e.status && typeof e.status === 'object');
    var items = Object.keys(temps).filter(function (j) { return W.sensed(temps[j]) != null; }).map(function (j) {
      var v = temps[j];
      return { label: j, value: v, color: v > 70 ? 'danger' : v > 60 ? 'warn' : 'line-strong' };
    });
    return h(A.Panel, { title: 'Joint temperatures', source: 'ROS 2 /artaban/status · limit 70 °C', live: W.liveFlag(e, 10) },
      items.length ? h(A.BarChart, { items: items, unit: '°C', digits: 0, max: 90 })
        : h(W.Empty, hasStatus ? { text: 'No sensor: joint temperatures are not published in /artaban/status' } : {}));
  }

  window.AuraWC.mount(JointTemperatures);
})();
