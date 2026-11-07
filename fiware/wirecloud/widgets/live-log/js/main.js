/* Live interaction log — LogTable over the AURA module topics as Orion-LD's DDS bridge mirrors them:
 *   stt     /aura_transcript           → HRIEvent:stt.transcript        (std_msgs/String)
 *   gesture /aura/gesture/intent       → HRIEvent:gesture.gestureIntent (aura_interfaces/GestureIntent)
 *   intent  /aura/intent/event         → HRIEvent:intent.intentEvent    (aura_interfaces/IntentEvent)
 *   speaker /aura/speaker/say          → HRIEvent:speaker.say           (std_msgs/String)
 * Backfill: Mintaka temporal API; live: Orion-LD notifications. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;
  var GESTURE_STATE = ['idle', 'resolving', 'accepted'];

  function intentState(m) {
    var d = W.DECISION[m.decision];
    if (d === 'ACCEPT') return m.intent === 'stop' ? 'safe_stop' : 'accepted';
    return { CONFIRM: 'confirming', REJECT: 'rejected', IGNORE: 'idle' }[d] || 'idle';
  }

  var SOURCES = [
    { id: W.IDS.hriEvent('stt'), attr: 'transcript', row: function (v) {
      var t = W.data(v); if (typeof t !== 'string') return null;
      return { module: 'stt', event: 'final_transcript', detail: '"' + t + '"', state: 'transcribing' };
    } },
    { id: W.IDS.hriEvent('gesture'), attr: 'gestureIntent', row: function (v) {
      var m = W.msg(v); if (!m.gesture) return null;
      return { module: 'gesture', event: 'gesture_intent', detail: m.gesture + ' · votes ' + m.votes + '/' + m.window, intent: m.intent,
        confidence: W.num(m.confidence), state: GESTURE_STATE[m.state] || 'resolving', latencyMs: Math.round(m.latency_ms || 0) };
    } },
    { id: W.IDS.hriEvent('intent'), attr: 'intentEvent', row: function (v) {
      var m = W.msg(v); if (!m.event_id) return null;
      var detail = W.DECISION[m.decision] === 'IGNORE' ? 'not addressed to robot (engagement gate)'
        : m.modality + ' · ' + m.acoustic_condition + ' · operator ' + m.operator_id;
      return { module: 'intent', event: 'intent_resolved', detail: detail, intent: m.intent, confidence: W.num(m.confidence),
        state: intentState(m), latencyMs: Math.round(m.latency_ms || 0) };
    } },
    { id: W.IDS.hriEvent('speaker'), attr: 'say', row: function (v) {
      var t = W.data(v); if (typeof t !== 'string') return null;
      var state = /^Did you say/.test(t) ? 'confirming' : /^Sorry/.test(t) ? 'rejected' : 'executing';
      return { module: 'speaker', event: 'feedback', detail: 'TTS "' + t + '"', state: state };
    } }
  ];

  function LiveLog(p) {
    var rows = N.useEventLog(SOURCES, { max: p.maxRows || 200, minutes: 30 });
    var node = N.useEntity(W.IDS.moduleStatus('intent'));
    var link = N.useLink();
    var filter = React.useState('all'), mod = filter[0], setMod = filter[1];
    var v = node && node.status ? W.diag(node.status).values : {};
    var th = W.num(Number(v.accept_threshold)) != null ? { accept: Number(v.accept_threshold), reject: Number(v.reject_threshold) } : undefined;
    var shown = rows.filter(function (r) { return mod === 'all' || r.module === mod; }).map(function (r) {
      return { id: r.id, time: r._t, module: r.module, event: r.event, detail: r.detail, intent: r.intent,
        confidence: r.confidence, state: r.state, latencyMs: r.latencyMs };
    });
    var live = link.status === 'live' ? true : link.status === 'waiting' ? false : 'stale';
    return h(A.Panel, { title: 'Live interaction log', source: 'ROS 2 /aura/* → NGSI-LD HRIEvent · ' + rows.length + ' events', live: live },
      h('div', { className: 'wc-filter', role: 'group', 'aria-label': 'Filter by module' },
        ['all'].concat(A.MODULES).map(function (m) {
          return h('button', { key: m, type: 'button', className: 'wc-chip' + (mod === m ? ' is-on' : ''), 'aria-pressed': mod === m, onClick: function () { setMod(m); } },
            m === 'all' ? 'all modules' : h(A.ModuleTag, { module: m }));
        })),
      h(A.LogTable, { rows: shown, thresholds: th, columns: ['time', 'module', 'event', 'detail', 'intent', 'confidence', 'state', 'latency'] }));
  }

  window.AuraWC.mount(LiveLog);
})();
