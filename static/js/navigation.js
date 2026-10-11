'use strict';

(() => {
  const shell = document.getElementById('navigationShell');
  const panel = document.getElementById('navigationPanel');
  const openButton = document.getElementById('mapOpenButton');
  const closeButton = document.getElementById('navigationCloseButton');
  const backdrop = document.getElementById('navigationBackdrop');
  const fromInput = document.getElementById('navigationFrom');
  const toInput = document.getElementById('navigationTo');
  const fromSuggestions = document.getElementById('navigationFromSuggestions');
  const toSuggestions = document.getElementById('navigationToSuggestions');
  const swapButton = document.getElementById('navigationSwapButton');
  const showRouteButton = document.getElementById('navigationShowRoute');
  const clearRouteButton = document.getElementById('navigationClearRoute');
  const status = document.getElementById('navigationStatus');
  const mapElement = document.getElementById('navigationMap');
  const routeSummary = document.getElementById('navigationRouteSummary');
  const distanceOutput = document.getElementById('navigationRemainingDistance');
  const durationOutput = document.getElementById('navigationDuration');
  const instructionOutput = document.getElementById('navigationInstruction');
  const turnDistanceOutput = document.getElementById('navigationTurnDistance');
  const gpsStatus = document.getElementById('navigationGpsStatus');
  const useGpsButton = document.getElementById('navigationUseGps');
  const startButton = document.getElementById('navigationStart');
  const stopButton = document.getElementById('navigationStop');
  const voiceButton = document.getElementById('navigationVoice');

  if (!shell || !panel || !openButton || !fromInput || !toInput) return;

  const SEARCH_MIN_INTERVAL_MS = 1100;
  const NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search';
  const OSRM_URL = 'https://router.project-osrm.org/route/v1/driving';
  const GPS_MAX_ACCURACY_METRES = 100;
  const GPS_MAX_AGE_MS = 30000;
  const ARRIVAL_DISTANCE_METRES = 35;
  const OFF_ROUTE_DISTANCE_METRES = 60;
  const OFF_ROUTE_CONFIRMATION_MS = 8000;
  const OFF_ROUTE_CONFIRMATION_SAMPLES = 3;
  const REROUTE_RESET_DISTANCE_METRES = 30;
  const MANEUVER_PASS_DISTANCE_METRES = 25;
  const ANNOUNCEMENT_DISTANCE_METRES = 500;
  const fields = [
    { input: fromInput, list: fromSuggestions, key: 'from' },
    { input: toInput, list: toSuggestions, key: 'to' },
  ];
  const locations = { from: null, to: null };
  const pendingSearches = { from: null, to: null };
  const debounceTimers = { from: null, to: null };
  let map = null;
  let routeLayer = null;
  let markerLayer = null;
  let routeController = null;
  let searchQueue = Promise.resolve();
  let lastSearchAt = 0;
  let closeTimer = null;
  let previousFocus = null;
  let isRouting = false;
  let isNavigating = false;
  let navigationWatchId = null;
  let routeSteps = [];
  let currentStepIndex = 0;
  let announcedSteps = new Set();
  let routeGeometry = [];
  let routeCumulativeDistances = [];
  let routeDistance = 0;
  let routeDuration = 0;
  let routeProgress = 0;
  let destinationMarker = null;
  let vehicleMarker = null;
  let latestPosition = null;
  let lastGpsTimestamp = 0;
  let offRouteSince = null;
  let offRouteSamples = 0;
  let rerouteAttempted = false;
  let navigationVoiceEnabled = true;
  let voice = null;
  let safetyAlertPauseUntil = 0;

  function setStatus(message, state = '') {
    status.textContent = message;
    if (state) status.dataset.state = state;
    else delete status.dataset.state;
  }

  function closeSuggestions(list, input) {
    list.replaceChildren();
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
  }

  function cancelSearch(field) {
    if (pendingSearches[field.key]) pendingSearches[field.key].abort();
    pendingSearches[field.key] = null;
    clearTimeout(debounceTimers[field.key]);
    debounceTimers[field.key] = null;
  }

  function clearRouteDisplay() {
    if (routeController) routeController.abort();
    routeController = null;
    isRouting = false;
    showRouteButton.disabled = false;
    if (map && routeLayer) map.removeLayer(routeLayer);
    if (map && markerLayer) map.removeLayer(markerLayer);
    if (map && vehicleMarker) map.removeLayer(vehicleMarker);
    routeLayer = null;
    markerLayer = null;
    vehicleMarker = null;
    routeSteps = [];
    routeGeometry = [];
    routeCumulativeDistances = [];
    routeDistance = 0;
    routeDuration = 0;
    routeProgress = 0;
    destinationMarker = null;
    routeSummary.hidden = true;
    distanceOutput.textContent = '—';
    durationOutput.textContent = '—';
    instructionOutput.textContent = 'Plan a route to begin.';
    turnDistanceOutput.textContent = '—';
  }

  function showSuggestionMessage(field, message) {
    field.list.replaceChildren();
    const item = document.createElement('li');
    item.className = 'navigation-suggestions-message';
    item.setAttribute('role', 'option');
    item.textContent = message;
    field.list.append(item);
    field.list.hidden = false;
    field.input.setAttribute('aria-expanded', 'true');
  }

  function scheduleSearch(field) {
    const { input, list, key } = field;
    const query = input.value.trim();
    stopNavigation(false);
    locations[key] = null;
    clearRouteDisplay();
    setStatus('Select a matching location from the suggestions.');
    closeSuggestions(list, input);
    cancelSearch(field);

    if (query.length < 3) {
      setStatus(query ? 'Enter at least 3 characters to search.' : 'Search and select both locations to plan a route.');
      return;
    }

    debounceTimers[key] = setTimeout(() => {
      const controller = new AbortController();
      pendingSearches[key] = controller;
      showSuggestionMessage(field, 'Searching locations…');
      const request = async () => {
        const wait = SEARCH_MIN_INTERVAL_MS - (Date.now() - lastSearchAt);
        if (wait > 0) await new Promise(resolve => setTimeout(resolve, wait));
        if (controller.signal.aborted) return;
        lastSearchAt = Date.now();
        const url = new URL(NOMINATIM_URL);
        url.search = new URLSearchParams({
          q: query,
          format: 'jsonv2',
          addressdetails: '1',
          limit: '5',
        }).toString();
        const timeoutId = setTimeout(() => controller.abort(), 12000);
        try {
          const response = await fetch(url, {
            signal: controller.signal,
            headers: { Accept: 'application/json' },
          });
          if (!response.ok) throw new Error(`Location search returned HTTP ${response.status}.`);
          return await response.json();
        } finally {
          clearTimeout(timeoutId);
        }
      };

      const result = searchQueue.then(request, request);
      searchQueue = result.catch(() => {});
      result.then(results => {
        if (controller.signal.aborted || input.value.trim() !== query) return;
        if (!Array.isArray(results) || results.length === 0) {
          showSuggestionMessage(field, 'No matching locations found.');
          return;
        }
        renderSuggestions(field, results);
      }).catch(error => {
        if (error.name === 'AbortError' || controller.signal.aborted) return;
        showSuggestionMessage(field, 'Search is unavailable. Check your connection and try again.');
        setStatus('Location search failed. Please try again shortly.', 'error');
      });
    }, 550);
  }

  function renderSuggestions(field, results) {
    field.list.replaceChildren();
    results.forEach(result => {
      const lat = Number(result.lat);
      const lon = Number(result.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const item = document.createElement('li');
      item.setAttribute('role', 'option');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'navigation-suggestion';
      button.textContent = result.display_name;
      button.addEventListener('click', () => {
        stopNavigation(false);
        locations[field.key] = {
          name: result.display_name,
          lat,
          lon,
        };
        field.input.value = result.display_name;
        closeSuggestions(field.list, field.input);
        clearRouteDisplay();
        setStatus(`${field.key === 'from' ? 'Starting location' : 'Destination'} selected.`);
        if (map) map.setView([lat, lon], Math.max(map.getZoom(), 12));
      });
      item.append(button);
      field.list.append(item);
    });
    if (!field.list.childElementCount) {
      showSuggestionMessage(field, 'No usable locations found.');
      return;
    }
    field.list.hidden = false;
    field.input.setAttribute('aria-expanded', 'true');
  }

  function ensureMap() {
    if (map) return;
    if (typeof L === 'undefined') {
      setStatus('The map library could not be loaded. Check your connection and reload.', 'error');
      return;
    }
    map = L.map(mapElement, { zoomControl: true, scrollWheelZoom: true }).setView([20, 0], 2);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    }).addTo(map);
  }

  function openPanel() {
    clearTimeout(closeTimer);
    previousFocus = document.activeElement;
    shell.hidden = false;
    openButton.setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => {
      shell.classList.add('is-open');
      ensureMap();
      if (map) {
        map.invalidateSize();
        setTimeout(() => map && map.invalidateSize(), 280);
      }
      closeButton.focus();
    });
  }

  function closePanel() {
    if (shell.hidden) return;
    shell.classList.remove('is-open');
    openButton.setAttribute('aria-expanded', 'false');
    closeTimer = setTimeout(() => {
      shell.hidden = true;
      if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
    }, 290);
  }

  function formatDistance(metres) {
    return metres >= 1000 ? `${(metres / 1000).toFixed(1)} km` : `${Math.round(metres)} m`;
  }

  function formatDuration(seconds) {
    const totalMinutes = Math.round(seconds / 60);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    if (hours && minutes) return `${hours} hr ${minutes} min`;
    if (hours) return `${hours} hr`;
    return `${Math.max(1, minutes)} min`;
  }

  function haversineDistance(a, b) {
    const radians = degrees => degrees * Math.PI / 180;
    const latitudeDelta = radians(b.lat - a.lat);
    const longitudeDelta = radians(b.lon - a.lon);
    const latitude1 = radians(a.lat);
    const latitude2 = radians(b.lat);
    const haversine = Math.sin(latitudeDelta / 2) ** 2 +
      Math.cos(latitude1) * Math.cos(latitude2) *
      Math.sin(longitudeDelta / 2) ** 2;
    return 6371000 * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
  }

  function maneuverInstruction(maneuver, roadName) {
    const type = maneuver.type || '';
    const modifier = (maneuver.modifier || '').replace(/_/g, ' ');
    const road = roadName ? ` onto ${roadName}` : '';
    if (type === 'arrive') return 'You have arrived at your destination.';
    if (type === 'depart') return roadName ? `Start on ${roadName}.` : 'Start driving.';
    if (type === 'roundabout' || type === 'rotary') {
      return maneuver.exit
        ? `Take the roundabout exit ${maneuver.exit}${road}.`
        : `Enter the roundabout${road}.`;
    }
    if (type === 'turn' || type === 'on ramp' || type === 'off ramp' ||
        type === 'fork' || type === 'end of road') {
      return `${modifier ? `Turn ${modifier}` : 'Continue'}${road}.`;
    }
    if (type === 'merge') return `Merge${modifier ? ` ${modifier}` : ''}${road}.`;
    if (type === 'new name' || type === 'continue' || type === 'notification') {
      return roadName ? `Continue on ${roadName}.` : 'Continue straight.';
    }
    return roadName ? `Continue on ${roadName}.` : 'Continue straight.';
  }

  async function requestRoute(origin, destination, controller) {
    const coordinates = `${origin.lon},${origin.lat};${destination.lon},${destination.lat}`;
    const url = `${OSRM_URL}/${coordinates}?overview=full&geometries=geojson&steps=true`;
    const timeoutId = setTimeout(() => {
      if (!controller.signal.aborted) controller.abort();
    }, 20000);
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`Route service returned HTTP ${response.status}.`);
      const data = await response.json();
      if (data.code !== 'Ok' || !Array.isArray(data.routes) || !data.routes.length) {
        throw new Error(data.message || 'No driving route is available for these locations.');
      }
      const route = data.routes[0];
      if (!route.geometry || !Array.isArray(route.geometry.coordinates) ||
          !route.geometry.coordinates.length || !Array.isArray(route.legs)) {
        throw new Error('The routing service returned an incomplete route.');
      }
      return route;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  function buildRouteSteps(route) {
    let distanceFromStart = 0;
    const steps = [];
    (route.legs || []).forEach(leg => {
      (leg.steps || []).forEach(step => {
        const distance = Number(step.distance) || 0;
        const maneuver = step.maneuver || {};
        if (maneuver.type !== 'depart' && maneuver.type !== 'arrive') {
          steps.push({
            progress: distanceFromStart,
            instruction: maneuverInstruction(maneuver, step.name || ''),
          });
        }
        distanceFromStart += distance;
      });
    });
    return steps;
  }

  function createMapIcons() {
    return {
      start: L.divIcon({
        className: '',
        html: '<span class="navigation-marker" aria-hidden="true"></span>',
        iconSize: [18, 18],
        iconAnchor: [9, 18],
      }),
      end: L.divIcon({
        className: '',
        html: '<span class="navigation-marker navigation-marker-end" aria-hidden="true"></span>',
        iconSize: [18, 18],
        iconAnchor: [9, 18],
      }),
      vehicle: L.divIcon({
        className: '',
        html: '<span class="navigation-vehicle-marker" aria-hidden="true"></span>',
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      }),
    };
  }

  function applyRoute(route, origin, destination, fitMap = true) {
    const nextRouteLayer = L.geoJSON(route.geometry, {
      style: { color: '#00c8ff', weight: 5, opacity: 0.92, className: 'navigation-route-halo' },
    }).addTo(map);
    const icons = createMapIcons();
    const nextMarkerLayer = L.layerGroup([
      L.marker([origin.lat, origin.lon], { icon: icons.start })
        .bindTooltip(`FROM: ${origin.name}`),
      L.marker([destination.lat, destination.lon], { icon: icons.end })
        .bindTooltip(`TO: ${destination.name}`),
    ]).addTo(map);
    if (routeLayer) map.removeLayer(routeLayer);
    if (markerLayer) map.removeLayer(markerLayer);
    routeLayer = nextRouteLayer;
    markerLayer = nextMarkerLayer;
    routeGeometry = route.geometry.coordinates.map(([lon, lat]) => ({ lat, lon }));
    routeCumulativeDistances = [0];
    for (let index = 1; index < routeGeometry.length; index += 1) {
      routeCumulativeDistances.push(
        routeCumulativeDistances[index - 1] +
        haversineDistance(routeGeometry[index - 1], routeGeometry[index])
      );
    }
    routeDistance = Number(route.distance) || routeCumulativeDistances[routeCumulativeDistances.length - 1] || 0;
    routeDuration = Number(route.duration) || 0;
    routeSteps = buildRouteSteps(route);
    currentStepIndex = 0;
    announcedSteps = new Set();
    routeProgress = 0;
    offRouteSince = null;
    offRouteSamples = 0;
    rerouteAttempted = false;
    destinationMarker = destination;
    if (fitMap) map.fitBounds(routeLayer.getBounds(), { padding: [30, 30], maxZoom: 15 });
    routeSummary.hidden = false;
    updateNavigationSummary();
    if (latestPosition) updateVehicleMarker(latestPosition);
  }

  function formatArrival(seconds) {
    const arrival = new Date(Date.now() + Math.max(0, seconds) * 1000);
    return `${arrival.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (${formatDuration(seconds)})`;
  }

  function updateNavigationSummary() {
    const remaining = Math.max(0, routeDistance - routeProgress);
    const remainingSeconds = routeDistance > 0
      ? routeDuration * remaining / routeDistance
      : routeDuration;
    distanceOutput.textContent = formatDistance(remaining);
    durationOutput.textContent = formatArrival(remainingSeconds);
    const nextStep = routeSteps[currentStepIndex];
    instructionOutput.textContent = nextStep
      ? nextStep.instruction
      : 'Continue to your destination.';
    turnDistanceOutput.textContent = nextStep
      ? formatDistance(Math.max(0, nextStep.progress - routeProgress))
      : formatDistance(remaining);
  }

  function projectPositionOnRoute(position) {
    let closestDistance = Number.POSITIVE_INFINITY;
    let closestProgress = 0;
    const latitudeScale = 111320;
    for (let index = 0; index < routeGeometry.length - 1; index += 1) {
      const start = routeGeometry[index];
      const end = routeGeometry[index + 1];
      const longitudeScale = latitudeScale * Math.cos(position.lat * Math.PI / 180);
      const ax = (start.lon - position.lon) * longitudeScale;
      const ay = (start.lat - position.lat) * latitudeScale;
      const bx = (end.lon - position.lon) * longitudeScale;
      const by = (end.lat - position.lat) * latitudeScale;
      const dx = bx - ax;
      const dy = by - ay;
      const lengthSquared = dx * dx + dy * dy;
      const fraction = lengthSquared
        ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / lengthSquared))
        : 0;
      const distance = Math.hypot(ax + fraction * dx, ay + fraction * dy);
      if (distance < closestDistance) {
        closestDistance = distance;
        closestProgress = routeCumulativeDistances[index] +
          (routeCumulativeDistances[index + 1] - routeCumulativeDistances[index]) * fraction;
      }
    }
    return { distance: closestDistance, progress: closestProgress };
  }

  function updateVehicleMarker(position) {
    if (!map) return;
    if (!vehicleMarker) {
      vehicleMarker = L.marker([position.lat, position.lon], {
        icon: createMapIcons().vehicle,
        zIndexOffset: 1000,
      }).bindTooltip('CURRENT GPS POSITION').addTo(map);
    } else {
      vehicleMarker.setLatLng([position.lat, position.lon]);
    }
  }

  function speakGuidance(message) {
    if (!navigationVoiceEnabled || !window.speechSynthesis ||
        !window.SpeechSynthesisUtterance) return;
    if (Date.now() < safetyAlertPauseUntil) return false;
    const safetyAssistant = window.VoiceAssistant;
    if (safetyAssistant && typeof safetyAssistant.isSafetyAlertActive === 'function' &&
        safetyAssistant.isSafetyAlertActive()) return false;
    try {
      const utterance = new SpeechSynthesisUtterance(message);
      utterance.lang = 'en-IN';
      voice = utterance;
      utterance.onerror = () => {
        if (voice === utterance) {
          voice = null;
          setStatus('Voice guidance failed; visual turn instructions remain available.', 'error');
        }
      };
      utterance.onend = () => { if (voice === utterance) voice = null; };
      window.speechSynthesis.speak(utterance);
      return true;
    } catch (error) {
      setStatus('Voice guidance is unavailable; visual turn instructions remain active.', 'error');
      return false;
    }
  }

  function announceUpcomingStep(position) {
    if (currentStepIndex < routeSteps.length) {
      const step = routeSteps[currentStepIndex];
      const distanceToStep = step.progress - routeProgress;
      if (distanceToStep <= ANNOUNCEMENT_DISTANCE_METRES &&
          distanceToStep >= -MANEUVER_PASS_DISTANCE_METRES &&
          !announcedSteps.has(currentStepIndex)) {
        const announced = speakGuidance(distanceToStep > 0
          ? `In ${formatDistance(distanceToStep)}, ${step.instruction}`
          : step.instruction);
        if (announced) announcedSteps.add(currentStepIndex);
      }
    }
    if (destinationMarker &&
        haversineDistance(position, destinationMarker) <= 500 &&
        !announcedSteps.has('destination')) {
      if (speakGuidance('You are approaching your destination.')) {
        announcedSteps.add('destination');
      }
    }
  }

  function rerouteFromPosition(position) {
    if (!isNavigating || isRouting || !locations.to) return;
    isRouting = true;
    setStatus('Off route. Calculating a new route…', 'loading');
    speakGuidance('Rerouting.');
    const controller = new AbortController();
    routeController = controller;
    const origin = { lat: position.lat, lon: position.lon, name: 'Current GPS position' };
    requestRoute(origin, locations.to, controller).then(route => {
      if (routeController !== controller || !isNavigating) return;
      locations.from = origin;
      fromInput.value = origin.name;
      applyRoute(route, origin, locations.to, true);
      setStatus('Route updated. Continue to your destination.', 'success');
      if (latestPosition) advanceNavigation(latestPosition);
    }).catch(error => {
      if (routeController === controller) {
        setStatus(error.name === 'AbortError'
          ? 'Rerouting timed out. Keeping the previous route; check your connection.'
          : 'Rerouting failed. Keeping the previous route; check your connection.', 'error');
      }
    }).finally(() => {
      if (routeController === controller) {
        routeController = null;
        isRouting = false;
      }
    });
  }

  function updateOffRouteState(distance, position) {
    const resetDistance = Math.max(REROUTE_RESET_DISTANCE_METRES, position.accuracy);
    if (distance <= resetDistance) {
      offRouteSince = null;
      offRouteSamples = 0;
      rerouteAttempted = false;
      return;
    }
    const offRouteThreshold = Math.max(
      OFF_ROUTE_DISTANCE_METRES,
      position.accuracy * 1.5,
    );
    if (distance <= offRouteThreshold || rerouteAttempted) return;
    if (offRouteSince === null) offRouteSince = Date.now();
    offRouteSamples += 1;
    if (offRouteSamples >= OFF_ROUTE_CONFIRMATION_SAMPLES &&
        Date.now() - offRouteSince >= OFF_ROUTE_CONFIRMATION_MS) {
      rerouteAttempted = true;
      rerouteFromPosition(position);
    }
  }

  function advanceNavigation(position) {
    const projection = projectPositionOnRoute(position);
    routeProgress = Math.max(routeProgress, projection.progress);
    while (currentStepIndex < routeSteps.length &&
        routeProgress > routeSteps[currentStepIndex].progress + MANEUVER_PASS_DISTANCE_METRES) {
      currentStepIndex += 1;
    }
    updateNavigationSummary();
    announceUpcomingStep(position);
    if (destinationMarker &&
        haversineDistance(position, destinationMarker) <=
        Math.max(ARRIVAL_DISTANCE_METRES, position.accuracy)) {
      isNavigating = false;
      releaseLocationWatcher();
      stopButton.disabled = true;
      setStatus('Arrived at your destination.', 'success');
      gpsStatus.textContent = 'GPS: ARRIVED';
      speakGuidance('You have arrived at your destination.');
      return true;
    }
    updateOffRouteState(projection.distance, position);
    return false;
  }

  function parsePosition(position, allowUnchanged = false) {
    const coords = position && position.coords;
    const timestamp = Number(position && position.timestamp);
    if (!coords || !Number.isFinite(timestamp) ||
        timestamp < lastGpsTimestamp ||
        (!allowUnchanged && timestamp === lastGpsTimestamp) ||
        timestamp > Date.now() + 5000 ||
        Date.now() - timestamp > GPS_MAX_AGE_MS) return null;
    const lat = Number(coords.latitude);
    const lon = Number(coords.longitude);
    const accuracy = Number(coords.accuracy);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) ||
        lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    if (!Number.isFinite(accuracy) || accuracy > GPS_MAX_ACCURACY_METRES) {
      gpsStatus.textContent = Number.isFinite(accuracy)
        ? `GPS: POOR ACCURACY (±${Math.round(accuracy)} m) — waiting for a better fix`
        : 'GPS: INVALID ACCURACY — waiting for a better fix';
      return null;
    }
    lastGpsTimestamp = timestamp;
    return { lat, lon, accuracy, timestamp };
  }

  function handleGpsPosition(position) {
    const parsed = parsePosition(position);
    if (!parsed) return;
    latestPosition = parsed;
    updateVehicleMarker(parsed);
    gpsStatus.textContent =
      `GPS: ACTIVE · ${parsed.lat.toFixed(5)}, ${parsed.lon.toFixed(5)} · ±${Math.round(parsed.accuracy)} m · ${new Date(parsed.timestamp).toLocaleTimeString()}`;
    if (isNavigating && routeGeometry.length) advanceNavigation(parsed);
  }

  function handleGpsError(error) {
    const messages = {
      1: 'GPS permission denied. Allow location access in browser settings to navigate.',
      2: 'GPS position is unavailable. Check device location services and try again.',
      3: 'GPS request timed out. Waiting for another position update.',
    };
    gpsStatus.textContent = `GPS: ${messages[error.code] || 'Location error.'}`;
    if (error.code === 1 || error.code === 2) {
      stopNavigation(false);
      gpsStatus.textContent = `GPS: ${messages[error.code]}`;
      setStatus(messages[error.code], 'error');
    }
  }

  function releaseLocationWatcher() {
    if (navigationWatchId === null || !navigator.geolocation) return;
    navigator.geolocation.clearWatch(navigationWatchId);
    navigationWatchId = null;
  }

  function stopNavigation(showMessage = true) {
    const wasNavigating = isNavigating;
    isNavigating = false;
    releaseLocationWatcher();
    if (wasNavigating && isRouting && routeController) {
      routeController.abort();
      routeController = null;
      isRouting = false;
      showRouteButton.disabled = false;
    }
    stopButton.disabled = true;
    gpsStatus.textContent = latestPosition
      ? `GPS: LAST FIX ${latestPosition.lat.toFixed(5)}, ${latestPosition.lon.toFixed(5)} · ±${Math.round(latestPosition.accuracy)} m · ${new Date(latestPosition.timestamp).toLocaleTimeString()}`
      : 'GPS: OFF';
    if (showMessage) {
      setStatus('Navigation stopped. Your planned route remains on the map.');
    }
  }

  function getCurrentPosition() {
    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: true,
        maximumAge: 5000,
        timeout: 15000,
      });
    });
  }

  async function useCurrentGps() {
    if (!navigator.geolocation) {
      gpsStatus.textContent = 'GPS: UNAVAILABLE';
      setStatus('This browser does not support GPS location.', 'error');
      return;
    }
    if (!window.isSecureContext) {
      gpsStatus.textContent = 'GPS: REQUIRES HTTPS OR LOCALHOST';
      setStatus('GPS requires HTTPS or localhost. Open the dashboard in a secure context.', 'error');
      return;
    }
    setStatus('Requesting current GPS position. Allow location access when prompted.', 'loading');
    try {
      const coords = await getCurrentPosition();
      const parsed = parsePosition(coords, true);
      if (!parsed) {
        setStatus('GPS fix is too old or inaccurate. Try again with a clearer signal.', 'error');
        return;
      }
      latestPosition = parsed;
      stopNavigation(false);
      clearRouteDisplay();
      locations.from = { lat: parsed.lat, lon: parsed.lon, name: 'Current GPS position' };
      fromInput.value = locations.from.name;
      ensureMap();
      updateVehicleMarker(parsed);
      if (map) map.setView([parsed.lat, parsed.lon], 15);
      gpsStatus.textContent =
        `GPS: READY · ${parsed.lat.toFixed(5)}, ${parsed.lon.toFixed(5)} · ±${Math.round(parsed.accuracy)} m`;
      setStatus('Current GPS position selected as the route origin.', 'success');
    } catch (error) {
      handleGpsError(error);
    }
  }

  async function startNavigation() {
    if (isNavigating || isRouting) return;
    if (!navigator.geolocation) {
      gpsStatus.textContent = 'GPS: UNAVAILABLE';
      setStatus('This browser does not support GPS location.', 'error');
      return;
    }
    if (!window.isSecureContext) {
      gpsStatus.textContent = 'GPS: REQUIRES HTTPS OR LOCALHOST';
      setStatus('GPS requires HTTPS or localhost. Open the dashboard in a secure context.', 'error');
      return;
    }
    if (!locations.to) {
      setStatus('Select a destination before starting navigation.', 'error');
      return;
    }
    ensureMap();
    if (!map) return;
    gpsStatus.textContent = 'GPS: REQUESTING PERMISSION';
    setStatus('Requesting location permission and a current GPS fix…', 'loading');
    startButton.disabled = true;
    let controller = null;
    try {
      const parsed = parsePosition(await getCurrentPosition(), true);
      if (!parsed) throw new Error('GPS fix is too old or inaccurate. Try again with a clearer signal.');
      latestPosition = parsed;
      const origin = { lat: parsed.lat, lon: parsed.lon, name: 'Current GPS position' };
      locations.from = origin;
      fromInput.value = origin.name;
      setStatus('Calculating a driving route from your current location…', 'loading');
      controller = new AbortController();
      routeController = controller;
      isRouting = true;
      const route = await requestRoute(origin, locations.to, controller);
      if (routeController !== controller) return;
      applyRoute(route, origin, locations.to);
      routeController = null;
      isRouting = false;
      isNavigating = true;
      stopButton.disabled = false;
      latestPosition = parsed;
      updateVehicleMarker(parsed);
      gpsStatus.textContent =
        `GPS: ACTIVE · ${parsed.lat.toFixed(5)}, ${parsed.lon.toFixed(5)} · ±${Math.round(parsed.accuracy)} m · ${new Date(parsed.timestamp).toLocaleTimeString()}`;
      speakGuidance('Navigation started.');
      const arrived = advanceNavigation(parsed);
      if (!arrived) {
        navigationWatchId = navigator.geolocation.watchPosition(
          handleGpsPosition,
          handleGpsError,
          { enableHighAccuracy: true, maximumAge: 3000, timeout: 15000 },
        );
        setStatus('Navigating. Duration is an estimate and does not include live traffic.', 'success');
      }
    } catch (error) {
      const routeRequestWasActive = controller && routeController === controller;
      if (routeRequestWasActive) routeController = null;
      isRouting = false;
      if (error.code) {
        handleGpsError(error);
      } else if (error.name === 'AbortError' && routeRequestWasActive) {
        setStatus('Route request timed out. Check your connection and try again.', 'error');
      } else if (error.message) {
        setStatus(error.message, 'error');
      }
    } finally {
      startButton.disabled = false;
    }
  }

  async function showRoute() {
    if (isRouting) return;
    if (isNavigating) {
      setStatus('Stop navigation before calculating a different planned route.', 'error');
      return;
    }
    if (!locations.from || !locations.to) {
      setStatus('Select both a starting location and a destination first.', 'error');
      return;
    }
    if (locations.from.lat === locations.to.lat && locations.from.lon === locations.to.lon) {
      setStatus('Choose two different locations to calculate a route.', 'error');
      return;
    }
    ensureMap();
    if (!map) return;

    isRouting = true;
    showRouteButton.disabled = true;
    setStatus('Calculating driving route…', 'loading');
    const controller = new AbortController();
    routeController = controller;
    try {
      const route = await requestRoute(locations.from, locations.to, controller);
      if (routeController !== controller) return;
      applyRoute(route, locations.from, locations.to);
      setStatus('Route calculated. Duration is an estimate and does not include live traffic.', 'success');
    } catch (error) {
      if (routeController === controller) {
        setStatus(error.name === 'AbortError'
          ? 'Route request timed out. Please try again.'
          : error.message || 'Could not calculate a route. Please try again.', 'error');
      }
    } finally {
      if (routeController === controller) {
        routeController = null;
        isRouting = false;
        showRouteButton.disabled = false;
      }
    }
  }

  fields.forEach(field => field.input.addEventListener('input', () => scheduleSearch(field)));
  openButton.addEventListener('click', openPanel);
  closeButton.addEventListener('click', closePanel);
  backdrop.addEventListener('click', closePanel);
  showRouteButton.addEventListener('click', showRoute);
  useGpsButton.addEventListener('click', useCurrentGps);
  startButton.addEventListener('click', startNavigation);
  stopButton.addEventListener('click', () => stopNavigation(true));
  voiceButton.addEventListener('click', () => {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
      voiceButton.textContent = 'VOICE: UNAVAILABLE';
      voiceButton.disabled = true;
      setStatus('Speech synthesis is unavailable in this browser. Visual navigation remains active.', 'error');
      return;
    }
    navigationVoiceEnabled = !navigationVoiceEnabled;
    voiceButton.textContent = navigationVoiceEnabled ? 'VOICE: ON' : 'VOICE: OFF';
    voiceButton.setAttribute('aria-pressed', String(navigationVoiceEnabled));
    voiceButton.title = navigationVoiceEnabled
      ? 'Mute turn-by-turn voice guidance.'
      : 'Unmute turn-by-turn voice guidance.';
    if (!isNavigating) {
      setStatus(navigationVoiceEnabled ? 'Voice guidance enabled.' : 'Voice guidance muted.');
    }
  });
  clearRouteButton.addEventListener('click', () => {
    stopNavigation(false);
    fields.forEach(field => {
      cancelSearch(field);
      field.input.value = '';
      closeSuggestions(field.list, field.input);
    });
    locations.from = null;
    locations.to = null;
    clearRouteDisplay();
    setStatus('Search and select both locations to plan a route.');
  });
  swapButton.addEventListener('click', () => {
    stopNavigation(false);
    const fromLocation = locations.from;
    locations.from = locations.to;
    locations.to = fromLocation;
    const fromValue = fromInput.value;
    fromInput.value = toInput.value;
    toInput.value = fromValue;
    fields.forEach(field => {
      cancelSearch(field);
      closeSuggestions(field.list, field.input);
    });
    clearRouteDisplay();
    setStatus('Locations swapped. Select SHOW ROUTE to calculate the route.');
  });
  if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
    navigationVoiceEnabled = false;
    voiceButton.textContent = 'VOICE: UNAVAILABLE';
    voiceButton.disabled = true;
    voiceButton.setAttribute('aria-pressed', 'false');
  }
  window.NavigationAssistant = {
    onSafetyAlert() {
      safetyAlertPauseUntil = Date.now() + 5000;
      if (window.speechSynthesis) {
        window.speechSynthesis.cancel();
        voice = null;
      }
    },
  };
  document.addEventListener('keydown', event => {
    if (shell.hidden) return;
    if (event.key === 'Escape') {
      closePanel();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = panel.querySelectorAll('button:not(:disabled), input:not(:disabled), a[href]');
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
