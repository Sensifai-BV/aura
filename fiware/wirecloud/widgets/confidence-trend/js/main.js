/* Intent confidence over time — TimeSeriesChart of IntentEvent.confidence (/aura/intent/event) and
 * GestureIntent.confidence (/aura/gesture/intent), with the intent node's thresholds from its DiagnosticStatus. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  function confidence(m) { return W.num(W.msg(m).confidence); }

  function ConfidenceTrend(p) {
    var minutes = p.minutes || 15;
    var conf = N.useSeries(W.IDS.hriEvent('intent'), 'intentEvent', minutes, confidence);
    var gconf = N.useSeries(W.IDS.hriEvent('gesture'), 'gestureIntent', minutes, confidence);
    var node = N.useEntity(W.IDS.moduleStatus('intent'));
    var v = node && node.status ? W.diag(node.status).values : {};
    var th = W.num(Number(v.accept_threshold)) != null
      ? { accept: Number(v.accept_threshold), reject: Number(v.reject_threshold) } : A.DEFAULT_THRESHOLDS;
    var series = [{ name: 'intent', color: 'mod-intent', points: conf }];
    if (gconf.length > 1) series.push({ name: 'gesture', color: 'mod-gesture', points: gconf });
    return h(A.Panel, { title: 'Intent confidence', source: 'ROS 2 /aura/intent/event · last ' + minutes + ' min', live: W.liveFlag(node, 10) },
      conf.length ? h(A.TimeSeriesChart, {
        series: series, yMin: 0, yMax: 1, digits: 2, height: 200, legend: true,
        thresholds: [{ value: th.accept, label: 'accept ' + th.accept.toFixed(2), tone: 'ok' }, { value: th.reject, label: 'reject ' + th.reject.toFixed(2), tone: 'danger' }],
        ariaLabel: 'Intent confidence over the last ' + minutes + ' minutes'
      }) : h(W.Empty));
  }

  window.AuraWC.mount(ConfidenceTrend);
})();
