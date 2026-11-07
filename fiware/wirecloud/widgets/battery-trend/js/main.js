/* Battery over time — TimeSeriesChart of /battery_state (sensor_msgs/BatteryState.percentage) on Robot.battery. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  function percent(b) { var p = W.num(W.msg(b).percentage); return p == null ? null : p * 100; }

  function BatteryTrend(p) {
    var minutes = p.minutes || 30;
    var robot = N.useEntity(W.IDS.robot);
    var pts = N.useSeries(W.IDS.robot, 'battery', minutes, percent);
    return h(A.Panel, { title: 'Battery', source: 'ROS 2 /battery_state · last ' + minutes + ' min', live: W.liveFlag(robot, 10) },
      pts.length ? h(A.TimeSeriesChart, {
        series: [{ name: 'battery', color: 'ok', points: pts }], yMin: 0, yMax: 100, unit: '%', digits: 0, height: 200,
        thresholds: [{ value: 25, label: 'low 25 %', tone: 'warn' }, { value: 12, label: 'critical 12 %', tone: 'danger' }],
        ariaLabel: 'Battery level over the last ' + minutes + ' minutes'
      }) : h(W.Empty));
  }

  window.AuraWC.mount(BatteryTrend);
})();
