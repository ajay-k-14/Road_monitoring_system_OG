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
  const distanceOutput = document.getElementById('navigationDistance');
  const durationOutput = document.getElementById('navigationDuration');

  if (!shell || !panel || !openButton || !fromInput || !toInput) return;

  const SEARCH_MIN_INTERVAL_MS = 1100;
  const NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search';
  const OSRM_URL = 'https://router.project-osrm.org/route/v1/driving';
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
    routeLayer = null;
    markerLayer = null;
    routeSummary.hidden = true;
    distanceOutput.textContent = '—';
    durationOutput.textContent = '—';
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

  async function showRoute() {
    if (isRouting) return;
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

    clearRouteDisplay();
    isRouting = true;
    showRouteButton.disabled = true;
    setStatus('Calculating driving route…', 'loading');
    const controller = new AbortController();
    routeController = controller;
    const timeoutId = setTimeout(() => controller.abort(), 20000);
    const coordinates = `${locations.from.lon},${locations.from.lat};${locations.to.lon},${locations.to.lat}`;
    try {
      const url = `${OSRM_URL}/${coordinates}?overview=full&geometries=geojson&steps=false`;
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`Route service returned HTTP ${response.status}.`);
      const data = await response.json();
      if (data.code !== 'Ok' || !data.routes || !data.routes.length) {
        throw new Error(data.message || 'No driving route is available for these locations.');
      }

      const route = data.routes[0];
      routeLayer = L.geoJSON(route.geometry, {
        style: { color: '#00c8ff', weight: 5, opacity: 0.92, className: 'navigation-route-halo' },
      }).addTo(map);
      const startIcon = L.divIcon({
        className: '',
        html: '<span class="navigation-marker" aria-hidden="true"></span>',
        iconSize: [18, 18],
        iconAnchor: [9, 18],
      });
      const endIcon = L.divIcon({
        className: '',
        html: '<span class="navigation-marker navigation-marker-end" aria-hidden="true"></span>',
        iconSize: [18, 18],
        iconAnchor: [9, 18],
      });
      markerLayer = L.layerGroup([
        L.marker([locations.from.lat, locations.from.lon], { icon: startIcon })
          .bindTooltip(`FROM: ${locations.from.name}`),
        L.marker([locations.to.lat, locations.to.lon], { icon: endIcon })
          .bindTooltip(`TO: ${locations.to.name}`),
      ]).addTo(map);
      map.fitBounds(routeLayer.getBounds(), { padding: [30, 30], maxZoom: 15 });
      distanceOutput.textContent = formatDistance(route.distance);
      durationOutput.textContent = formatDuration(route.duration);
      routeSummary.hidden = false;
      setStatus('Route calculated. Duration is an estimate and does not include live traffic.', 'success');
    } catch (error) {
      if (routeController !== controller) return;
      if (error.name === 'AbortError') {
        setStatus('Route request timed out or was cancelled. Please try again.', 'error');
      } else {
        setStatus(error.message || 'Could not calculate a route. Please try again.', 'error');
      }
    } finally {
      clearTimeout(timeoutId);
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
  clearRouteButton.addEventListener('click', () => {
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
