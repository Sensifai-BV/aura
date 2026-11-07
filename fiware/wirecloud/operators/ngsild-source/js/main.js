/* AURA NGSI-LD source operator.
 * 1. opens an EventSource on ngsi-proxy (browser side),
 * 2. creates a callback on it (through WireCloud's proxy, so the URL is reachable from Orion-LD),
 * 3. creates an Orion-LD subscription for the configured entity types with that callback as endpoint,
 * 4. pushes every notification's entities to the "entities" output endpoint.
 * The subscription expires after 2 h and is renewed every hour; it is deleted when the operator unloads. */
(function () {
  'use strict';
  var MP = window.MashupPlatform;
  var es = null, subscriptionUrl = null, renewTimer = null;

  function pref(n) { return String(MP.prefs.get(n) || '').replace(/\/+$/, ''); }
  function log(msg, level) { try { MP.operator.log(msg, level || MP.log.INFO); } catch (e) { console.log(msg); } }

  function request(method, url, body) {
    return new Promise(function (resolve, reject) {
      var opts = {
        method: method, requestHeaders: { Accept: 'application/json' },
        onSuccess: function (r) { resolve(r); },
        onFailure: function (r) { reject(new Error(method + ' ' + url + ' → ' + (r && r.status) + ' ' + (r && r.responseText || '').slice(0, 200))); }
      };
      if (body !== undefined) { opts.contentType = 'application/json'; opts.postBody = JSON.stringify(body); }
      MP.http.makeRequest(url, opts);
    });
  }

  function openEventSource() {
    return fetch(pref('ngsi_proxy_public') + '/eventsource', { method: 'POST' })
      .then(function (r) { if (!r.ok) throw new Error('ngsi-proxy eventsource ' + r.status); return r.json(); })
      .then(function (conn) {
        return new Promise(function (resolve) {
          es = new EventSource(conn.url);
          es.addEventListener('init', function () { resolve(conn.connection_id); });
          es.addEventListener('notification', function (ev) {
            var msg = JSON.parse(ev.data), body;
            try { body = JSON.parse(msg.payload); } catch (e) { return; }
            if (body && body.data && body.data.length) MP.wiring.pushEvent('entities', JSON.stringify(body.data));
          });
        });
      });
  }

  function createCallback(connectionId) {
    // Through WireCloud's proxy: ngsi-proxy builds the callback URL from the host it was reached on.
    return request('POST', pref('ngsi_proxy_internal') + '/callbacks', { connection_id: connectionId })
      .then(function (r) { return JSON.parse(r.responseText).url; });
  }

  function expiresAt() { return new Date(Date.now() + 2 * 3600 * 1000).toISOString(); }

  function subscribe(callbackUrl) {
    var types = pref('entity_types').split(',').map(function (t) { return t.trim(); }).filter(Boolean);
    var body = {
      type: 'Subscription',
      description: 'AURA WireCloud dashboard (ngsi-proxy)',
      entities: types.map(function (t) { return { type: t }; }),
      expiresAt: expiresAt(),
      notification: { format: 'normalized', sysAttrs: true, endpoint: { uri: callbackUrl, accept: 'application/json' } }
    };
    return request('POST', pref('ngsi_server') + '/ngsi-ld/v1/subscriptions', body).then(function (r) {
      var loc = r.getResponseHeader && r.getResponseHeader('Location');
      subscriptionUrl = loc ? (loc.indexOf('http') === 0 ? loc : pref('ngsi_server') + loc) : null;
      log('Subscribed to ' + types.length + ' NGSI-LD types (' + (subscriptionUrl || 'no location') + ')');
      renewTimer = setInterval(function () {
        if (subscriptionUrl) request('PATCH', subscriptionUrl, { expiresAt: expiresAt() }).catch(function (e) { log(e.message, MP.log.WARN); });
      }, 3600 * 1000);
    });
  }

  function start() {
    openEventSource().then(createCallback).then(subscribe).catch(function (e) {
      log('AURA NGSI-LD source failed: ' + e.message, MP.log.ERROR);
      setTimeout(restart, 10000);
    });
  }
  function stop() {
    if (renewTimer) clearInterval(renewTimer);
    renewTimer = null;
    if (es) es.close();
    es = null;
    if (subscriptionUrl) request('DELETE', subscriptionUrl).catch(function () {});
    subscriptionUrl = null;
  }
  function restart() { stop(); start(); }

  MP.prefs.registerCallback(restart);
  window.addEventListener('unload', stop);
  start();
})();
