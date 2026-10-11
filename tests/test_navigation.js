'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const navigationScript = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'js', 'navigation.js'),
  'utf8',
);

class FakeElement {
  constructor(id) {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.classList = {
      add() {},
      remove() {},
    };
  }

  addEventListener(name, callback) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(callback);
    this.listeners.set(name, handlers);
  }

  async dispatch(name, event = {}) {
    const handlers = this.listeners.get(name) || [];
    for (const callback of handlers) await callback({ target: this, ...event });
  }

  async click() {
    return this.dispatch('click');
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  replaceChildren(...children) {
    this.children = children;
  }

  append(child) {
    this.children.push(child);
  }

  get childElementCount() {
    return this.children.length;
  }

  querySelectorAll() {
    return [];
  }

  focus() {}
}

function createDashboard(options = {}) {
  const ids = [
    'navigationShell', 'navigationPanel', 'mapOpenButton',
    'navigationCloseButton', 'navigationBackdrop', 'navigationFrom',
    'navigationTo', 'navigationFromSuggestions', 'navigationToSuggestions',
    'navigationSwapButton', 'navigationShowRoute', 'navigationClearRoute',
    'navigationStatus', 'navigationMap', 'navigationRouteSummary',
    'navigationRemainingDistance', 'navigationDuration',
    'navigationInstruction', 'navigationTurnDistance',
    'navigationGpsStatus', 'navigationUseGps', 'navigationStart',
    'navigationStop', 'navigationVoice',
  ];
  const elements = new Map(ids.map(id => [id, new FakeElement(id)]));
  elements.get('navigationShell').hidden = true;
  elements.get('navigationVoice').textContent = 'VOICE: ON';
  const speech = [];
  const watchers = new Map();
  const clearedWatchers = [];
  const requests = [];
  let nextWatcherId = 1;
  let clock = Date.now();

  class MockDate extends Date {
    static now() {
      return clock;
    }

    constructor(...args) {
      super(...(args.length ? args : [clock]));
    }
  }

  class MockUtterance {
    constructor(text) {
      this.text = text;
    }
  }

  function mapObject() {
    return {
      setView() { return this; },
      invalidateSize() {},
      fitBounds() {},
      removeLayer() {},
    };
  }

  const leaflet = {
    map: mapObject,
    tileLayer: () => ({ addTo() {} }),
    divIcon: options => options,
    marker: () => ({
      bindTooltip() { return this; },
      addTo() { return this; },
      setLatLng() {},
    }),
    layerGroup: () => ({ addTo() { return this; } }),
    geoJSON: () => ({
      addTo() { return this; },
      getBounds() { return {}; },
    }),
  };

  const routeData = (startLon, endLon) => ({
    code: 'Ok',
    routes: [{
      distance: Math.abs(endLon - startLon) * 111320,
      duration: 300,
      geometry: {
        type: 'LineString',
        coordinates: [
          [startLon, 0],
          [(startLon + endLon) / 2, 0],
          [endLon, 0],
        ],
      },
      legs: [{
        steps: [
          { distance: Math.abs(endLon - startLon) * 27830, name: '', maneuver: { type: 'depart' } },
          {
            distance: Math.abs(endLon - startLon) * 27830,
            name: 'Main Road',
            maneuver: { type: 'turn', modifier: 'right' },
          },
          {
            distance: Math.abs(endLon - startLon) * 27830,
            name: 'Main Road',
            maneuver: { type: 'continue' },
          },
          {
            distance: Math.abs(endLon - startLon) * 27830,
            name: 'Park Street',
            maneuver: { type: 'turn', modifier: 'left' },
          },
          { distance: 0, name: '', maneuver: { type: 'arrive' } },
        ],
      }],
    }],
  });

  const context = {
    AbortController,
    URL,
    URLSearchParams,
    Date: MockDate,
    Math,
    Number,
    Promise,
    Set,
    Array,
    Object,
    String,
    Error,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: callback => callback(),
    document: {
      activeElement: null,
      getElementById: id => elements.get(id) || null,
      addEventListener() {},
      createElement: () => new FakeElement('created'),
    },
    navigator: {
      geolocation: options.geolocation === false ? null : {
        getCurrentPosition(success, failure) {
          if (options.gpsError) {
            failure({ code: options.gpsError, message: 'mock GPS error' });
          } else {
            success({
              timestamp: clock,
              coords: {
                latitude: 0,
                longitude: 0,
                accuracy: options.gpsAccuracy || 8,
              },
            });
          }
        },
        watchPosition(success, failure, watchOptions) {
          const id = nextWatcherId++;
          watchers.set(id, { success, failure, watchOptions });
          return id;
        },
        clearWatch(id) {
          watchers.delete(id);
          clearedWatchers.push(id);
        },
      },
    },
    window: {
      isSecureContext: options.secureContext !== false,
      speechSynthesis: options.speechSupport === false
        ? null
        : { speak: utterance => speech.push(utterance.text), cancel() {} },
      SpeechSynthesisUtterance: options.speechSupport === false ? null : MockUtterance,
      VoiceAssistant: { isSafetyAlertActive: () => false },
    },
    SpeechSynthesisUtterance: options.speechSupport === false ? undefined : MockUtterance,
    L: leaflet,
    console,
    fetch: async url => {
      requests.push(String(url));
      if (String(url).startsWith('https://nominatim.openstreetmap.org/')) {
        return {
          ok: true,
          json: async () => [{
            lat: '0',
            lon: options.destinationLon || '0.01',
            display_name: 'Test destination',
          }],
        };
      }
      const routeRequestCount = requests.filter(request =>
        request.includes('/route/v1/')).length;
      if (options.routeError ||
          options.routeErrorOnRequest === routeRequestCount) {
        return { ok: false, status: 503, json: async () => ({ code: 'Error' }) };
      }
      const pathPart = String(url).split('/driving/')[1].split('?')[0];
      const endLon = Number(pathPart.split(';')[1].split(',')[0]);
      const startLon = Number(pathPart.split(';')[0].split(',')[0]);
      return { ok: true, json: async () => routeData(startLon, endLon) };
    },
  };
  vm.runInNewContext(navigationScript, context, { filename: 'navigation.js' });

  return {
    elements,
    speech,
    watchers,
    clearedWatchers,
    requests,
    setTime: value => { clock = value; },
    emitPosition(id, longitude, latitude = 0) {
      const watcher = watchers.get(id);
      assert.ok(watcher, `GPS watcher ${id} should be active`);
      watcher.success({
        timestamp: clock,
        coords: { latitude, longitude, accuracy: 8 },
      });
    },
    async chooseDestination() {
      const input = elements.get('navigationTo');
      input.value = 'Test destination';
      await input.dispatch('input');
      await new Promise(resolve => setTimeout(resolve, 600));
      const suggestion = elements.get('navigationToSuggestions').children[0];
      assert.ok(suggestion, 'destination suggestion should be displayed');
      await suggestion.children[0].click();
    },
  };
}

test('starts navigation with GPS, follows route steps, deduplicates guidance, and stops watcher', async () => {
  const dashboard = createDashboard();
  await dashboard.chooseDestination();
  await dashboard.elements.get('navigationStart').click();

  assert.match(dashboard.elements.get('navigationStatus').textContent, /Navigating/);
  assert.match(dashboard.requests.find(url => url.includes('/route/v1/')), /steps=true/);
  assert.equal(dashboard.watchers.size, 1);
  assert.match(dashboard.elements.get('navigationInstruction').textContent, /Turn right onto Main Road/);

  const watcherId = [...dashboard.watchers.keys()][0];
  dashboard.setTime(Date.now() + 1000);
  dashboard.emitPosition(watcherId, 0.0025);
  const spoken = dashboard.speech.filter(message => message.includes('Turn right'));
  assert.ok(spoken.length > 0, 'approaching turn should be announced');
  assert.match(dashboard.elements.get('navigationInstruction').textContent, /Turn right/);

  dashboard.setTime(Date.now() + 2000);
  dashboard.emitPosition(watcherId, 0.003);
  assert.match(dashboard.elements.get('navigationInstruction').textContent, /Continue on Main Road/);
  dashboard.setTime(Date.now() + 3000);
  dashboard.emitPosition(watcherId, 0.0031);
  assert.equal(dashboard.speech.filter(message => message.includes('Turn right')).length, spoken.length);

  await dashboard.elements.get('navigationStop').click();
  assert.equal(dashboard.watchers.size, 0);
  assert.deepEqual(dashboard.clearedWatchers, [watcherId]);
});

test('reports denied GPS and does not start navigation', async () => {
  const dashboard = createDashboard({ gpsError: 1 });
  await dashboard.chooseDestination();
  await dashboard.elements.get('navigationStart').click();
  assert.match(dashboard.elements.get('navigationStatus').textContent, /permission denied/i);
  assert.equal(dashboard.watchers.size, 0);
});

test('requires a destination and rejects inaccurate GPS fixes', async () => {
  const noDestination = createDashboard();
  await noDestination.elements.get('navigationStart').click();
  assert.match(noDestination.elements.get('navigationStatus').textContent, /select a destination/i);
  assert.equal(noDestination.watchers.size, 0);

  const poorGps = createDashboard({ gpsAccuracy: 150 });
  await poorGps.chooseDestination();
  await poorGps.elements.get('navigationStart').click();
  assert.match(poorGps.elements.get('navigationStatus').textContent, /inaccurate/i);
  assert.equal(poorGps.watchers.size, 0);
});

test('rejects an unselected or invalid route origin', async () => {
  const dashboard = createDashboard();
  await dashboard.chooseDestination();
  dashboard.elements.get('navigationFrom').value = 'Unknown origin';
  await dashboard.elements.get('navigationShowRoute').click();
  assert.match(dashboard.elements.get('navigationStatus').textContent, /select both/i);
  assert.equal(dashboard.requests.filter(url => url.includes('/route/v1/')).length, 0);
});

test('reports unsupported GPS and insecure-origin requirements', async () => {
  const unsupported = createDashboard({ geolocation: false });
  await unsupported.chooseDestination();
  await unsupported.elements.get('navigationStart').click();
  assert.match(unsupported.elements.get('navigationStatus').textContent, /does not support GPS/i);

  const insecure = createDashboard({ secureContext: false });
  await insecure.chooseDestination();
  await insecure.elements.get('navigationStart').click();
  assert.match(insecure.elements.get('navigationStatus').textContent, /HTTPS or localhost/i);
});

test('keeps visual navigation available when speech synthesis is unsupported', async () => {
  const dashboard = createDashboard({ speechSupport: false });
  await dashboard.chooseDestination();
  assert.equal(dashboard.elements.get('navigationVoice').textContent, 'VOICE: UNAVAILABLE');
  await dashboard.elements.get('navigationStart').click();
  assert.match(dashboard.elements.get('navigationStatus').textContent, /Navigating/);
  assert.equal(dashboard.watchers.size, 1);
  assert.deepEqual(dashboard.speech, []);
});

test('keeps route failure visible and does not start a watcher', async () => {
  const dashboard = createDashboard({ routeError: true });
  await dashboard.chooseDestination();
  await dashboard.elements.get('navigationStart').click();
  assert.match(dashboard.elements.get('navigationStatus').textContent, /HTTP 503/);
  assert.equal(dashboard.watchers.size, 0);
});

test('reroutes once after sustained off-route GPS and announces arrival', async () => {
  const dashboard = createDashboard();
  await dashboard.chooseDestination();
  await dashboard.elements.get('navigationStart').click();
  const watcherId = [...dashboard.watchers.keys()][0];
  const startTime = Date.now();
  for (let sample = 1; sample <= 3; sample += 1) {
    dashboard.setTime(startTime + sample * 4000);
    dashboard.emitPosition(watcherId, 0, 0.003);
  }
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(dashboard.requests.filter(url => url.includes('/route/v1/')).length, 2);

  dashboard.setTime(startTime + 16000);
  dashboard.emitPosition(watcherId, 0.01);
  assert.match(dashboard.elements.get('navigationStatus').textContent, /Arrived/);
  assert.equal(dashboard.watchers.size, 0);
  assert.ok(dashboard.speech.includes('You have arrived at your destination.'));
});

test('preserves the valid route and suppresses repeat reroutes when routing fails', async () => {
  const dashboard = createDashboard({ routeErrorOnRequest: 2 });
  await dashboard.chooseDestination();
  await dashboard.elements.get('navigationStart').click();
  const watcherId = [...dashboard.watchers.keys()][0];
  const startTime = Date.now();
  for (let sample = 1; sample <= 3; sample += 1) {
    dashboard.setTime(startTime + sample * 4000);
    dashboard.emitPosition(watcherId, 0, 0.003);
  }
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(dashboard.requests.filter(url => url.includes('/route/v1/')).length, 2);
  assert.match(dashboard.elements.get('navigationStatus').textContent, /Rerouting failed/i);

  for (let sample = 4; sample <= 6; sample += 1) {
    dashboard.setTime(startTime + sample * 4000);
    dashboard.emitPosition(watcherId, 0, 0.003);
  }
  assert.equal(dashboard.requests.filter(url => url.includes('/route/v1/')).length, 2);
});
