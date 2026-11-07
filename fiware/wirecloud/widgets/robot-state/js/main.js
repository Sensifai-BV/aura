/* Artaban state — mission, waypoint, mode, gait and safety from /artaban/status (aura_interfaces/ArtabanStatus),
 * position from /odom (nav_msgs/Odometry), both mirrored on the Robot entity by Orion-LD's DDS bridge. */
(function () {
  var React = window.React, h = React.createElement, A = window.Aura, N = window.AuraNGSI, W = window.AuraW;

  var MISSION_TONE = { navigating: 'accent', inspecting: 'info', returning: 'info', paused: 'warn', charging: 'neutral', idle: 'neutral', 'safe-stop': 'stop' };
  var SAFETY = { nominal: ['ok', '✓'], warning: ['warn', '!'], 'protective-stop': ['stop', '■'] };

  function Badge(p) {
    return h('span', { className: 'au-badge au-tone-' + (p.tone || 'neutral') },
      p.glyph ? h('span', { className: 'au-badge-g', 'aria-hidden': true }, p.glyph) : null, p.text);
  }
  function Fact(p) {
    return h('div', { className: 'wc-fact' }, h('span', { className: 'wc-fact-k' }, p.k), h('span', { className: 'wc-fact-v' }, p.children));
  }

  function RobotState() {
    var e = N.useEntity(W.IDS.robot), r = W.robot(e);
    if (!r || !r.missionId) return h(A.Panel, { title: 'Artaban state', source: 'ROS 2 /artaban/status · /odom' }, h(W.Empty));
    var safety = SAFETY[r.safetyState] || ['neutral', '·'];
    return h(A.Panel, { title: 'Artaban state', source: 'ROS 2 /artaban/status · /odom → NGSI-LD Robot', live: W.liveFlag(e, 10) },
      h('div', { className: 'wc-facts' },
        h(Fact, { k: 'MISSION' }, h('span', { className: 'au-mono' }, r.missionId), ' ', h(Badge, { tone: MISSION_TONE[r.missionState], text: r.missionState || '—' })),
        h(Fact, { k: 'WAYPOINT' }, r.currentWaypoint || '—'),
        h(Fact, { k: 'MODE' }, h('span', { className: 'au-mono' }, r.operationMode || '—')),
        h(Fact, { k: 'GAIT' }, h('span', { className: 'au-mono' }, r.gait || '—')),
        h(Fact, { k: 'SAFETY' }, h(Badge, { tone: safety[0], glyph: safety[1], text: r.safetyState || '—' })),
        h(Fact, { k: 'E-STOP' }, r.eStop ? h(Badge, { tone: 'stop', glyph: '■', text: 'engaged' }) : h(Badge, { tone: 'ok', glyph: '✓', text: 'released' })),
        h(Fact, { k: 'POSITION' }, h('span', { className: 'au-mono' }, W.fmt(r.x, 1) + ', ' + W.fmt(r.y, 1) + ' m')),
        h(Fact, { k: 'ODOMETRY' }, h('span', { className: 'au-mono' }, W.fmt(r.odometry, 0) + ' m')),
        h(Fact, { k: 'EDGE CPU / GPU' }, h('span', { className: 'au-mono' }, W.fmt(r.cpuLoad, 0) + ' / ' + W.fmt(r.gpuLoad, 0) + ' %')),
        h(Fact, { k: 'SOC TEMP' }, h('span', { className: 'au-mono' }, W.fmt(r.socTemperature, 0) + ' °C'))));
  }

  window.AuraWC.mount(RobotState);
})();
