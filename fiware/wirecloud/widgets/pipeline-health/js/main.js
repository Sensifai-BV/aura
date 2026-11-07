/* Module health — PipelineMap over HRIModuleStatus:<module>.status (diagnostic_msgs/DiagnosticStatus from
 * /aura/<module>/status); the sink shows the live Orion-LD notification rate and lag. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  function PipelineHealth() {
    var list = N.useEntities('HRIModuleStatus'), link = N.useLink();
    var modules = {}, newest = null;
    list.forEach(function (e) {
      if (!e.status || e.status === 'uninitialized') return;
      var d = W.diag(e.status), m = e.id.split(':').pop();
      modules[m] = { health: d.health, rate: W.num(Number(d.values.rate)), rateUnit: d.values.rate_unit,
        latencyMs: W.num(Number(d.values.latency_ms)) };
      if (!newest || (e._observedAt || '') > (newest._observedAt || '')) newest = e;
    });
    return h(A.Panel, { title: 'Module health', source: 'ROS 2 /aura/*/status → NGSI-LD HRIModuleStatus', live: W.liveFlag(newest, 10) },
      Object.keys(modules).length ? h(A.PipelineMap, { modules: modules, sink: { name: 'Orion-LD', rate: link.rate, lagMs: link.lagMs } }) : h(W.Empty));
  }

  window.AuraWC.mount(PipelineHealth);
})();
