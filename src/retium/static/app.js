import {connectivityState} from "./connectivity.js?v=20260905-1";
import {displayPositionSeries, distanceMeters, MAX_AUTOMATIC_ACCURACY_METERS} from "./location-filter.js?v=20260903-1";
import {nextWaypointLabel, withDisplayWaypointLabels} from "./map-labels.js?v=20260903-2";
import {formatMapBytes, packProgress, tileCountForBounds} from "./map-offline.js?v=20260903-1";
import {mapMessageReadStorageKey, mapOverlayMessages, parseReadMessageIds, shouldDismissMapMessage} from "./map-message-read.js?v=20260903-2";
import {captureMapCamera, hasAddedMapMarker, mapMarkerIds, restoreMapCamera, shouldInitiallyFrameMap} from "./map-view.js?v=20260903-3";
import {latestOperatorMapTarget, mapTargetCapabilities} from "./map-target.js?v=20260903-1";
import {compassCardinal, compassTargetIndicator, compassTicks, normalizeHeading} from "./compass-tape.js?v=20260903-2";
import {bearingRayEndPixel, bearingRayFeature} from "./bearing-ray.js?v=20260903-1";
import {
  calculatedRouteUrl,
  formatRouteDuration,
  parseCalculatedRoute,
  routeInstruction,
  routeNeedsRefresh,
} from "./route-navigation.js?v=20260903-1";
import {
  AUTOMATIC_REPORT_POSITION_MAX_AGE_SECONDS,
  activeAutomaticReports,
  buildAutomaticReportFeatures,
  parseAutomaticReport,
} from "./automatic-reports.js?v=20260903-4";
import {
  TACTICAL_MARKER_TYPES,
  tacticalMarker,
  tacticalMarkerGroups,
} from "./marker-catalog.js?v=20260903-1";

const $ = (id) => document.getElementById(id);
let role = null;
let qrStream = null;
let qrScanTimer = null;
let operationalMap = null;
let mapReady = null;
let lastFieldMapSignature = "";
let commandMapHasFramedData = false;
let fieldMapHasRendered = false;
let fieldMapMarkerIds = new Set();
let mapStyleMode = readMapStylePreference();
let mapStyleSwitching = false;
let mapGridEnabled = readMapGridPreference();
let offlineMapPollTimer = null;
let offlineMapMaxTiles = 2500;
let mgrsLibraryPromise = null;
let currentTeamModules = [];
let currentEveryoneAdmin = false;
let currentTeamAdmin = false;
let currentTeamHosted = false;
let currentTeamJoinCode = "";
let tasksLoading = false;
let currentUser = {callsign: "FIELD-1", icon: "dot", color: "moss"};
let selectedUserIcon = "dot";
let selectedUserColor = "moss";
let fieldMap = null;
let fieldMapReady = null;
let fieldMapMarkerClass = null;
let fieldMapPopupClass = null;
const fieldWaypointMarkers = new Map();
let fieldMapOnline = false;
let fieldFeedOnline = false;
let fieldTeamDestination = "";
let commandEvents = [];
let fieldEvents = [];
let fieldOperators = new Map();
let privateMessages = [];
let activePrivatePeer = null;
let privateChatLoading = false;
let readMapMessageIds = new Set();
let mapMessageSwipe = null;
let suppressMapMessageClickUntil = 0;
let commandMapEditor = null;
let fieldMapEditor = null;
let movementWatchId = null;
let movementLastSent = null;
let movementPendingRelocation = null;
let movementSendPending = false;
let nativeMovementTracking = false;
let automaticLocationEnabled = readAutomaticLocationPreference();
let waypointNavigation = null;
let waypointNavigationWatchId = null;
let waypointArrivalReportPending = false;
let lastCompassHeading = null;
let nativeCompassUpdatedAt = 0;
let fieldBearingOrigin = null;
let fieldBearingFrame = null;
let fieldBearingHasData = false;
let fieldCompassMapTargets = [];
let feedLoading = false;
let pttRecorder = null;
let pttStream = null;
let pttStarting = false;
let pttChunks = [];
let pttStartedAt = 0;
let pttButton = null;
let pttHoldActive = false;
let pttStopTimer = null;
let pttShouldSend = true;
let pttGesture = null;
let pttCancelArmed = false;
let pttMode = "recorded";
let pttLiveStreamId = null;
let pttLiveFailed = false;
let pttLiveSendChain = Promise.resolve();
let pttPrivateRecipient = null;
let leaveArmed = false;
let localIdentityHash = "";
let teamOperational = false;
let fieldQueuedEvents = 0;
let localMapWatchId = null;
let localMapPosition = null;
let localMapPositionHasCentered = false;
let liveVoiceSocket = null;
let liveVoiceReady = false;
let liveVoiceReconnectTimer = null;
let incomingLiveVoice = null;
let incomingFeedInitialized = false;
let incomingAudioReady = false;
let activeIncomingAudio = null;
let activeIncomingCue = null;
let incomingCuePlaying = false;
let incomingTtsSpeaking = false;
let incomingAlertQueue = Promise.resolve();
let activeVoicePlayer = null;
let nativeTtsSequence = 0;
let ttsVoicesLoaded = false;
const seenIncomingEventIds = new Set();
const nativeTtsWaiters = new Map();
const voicePlayerStates = new WeakMap();
const voiceAssetCache = new Map();
const voiceTranscriptCache = new Map();
const voiceTranscriptRequests = new Set();
let incomingAlertsEnabled = readIncomingAlertPreference();
const INCOMING_MESSAGE_CUE_URL = "/audio/incoming-transmission-start.ogg";
const INCOMING_MESSAGE_END_CUE_URL = "/audio/incomming-transmission-end.ogg";
const SPEECH_READY_TIMEOUT_MS = 2500;
const VOICE_READY_TIMEOUT_MS = 15000;
const VOICE_WAVEFORM_BARS = 36;
const LIVE_VOICE_HEADER_BYTES = 20;
const incomingCuePreloads = [INCOMING_MESSAGE_CUE_URL, INCOMING_MESSAGE_END_CUE_URL].map((source) => {
  const audio = new Audio(source);
  audio.preload = "auto";
  return audio;
});
const MAPLIBRE_URL = "/vendor/maplibre-gl/maplibre-gl.mjs?v=6.6.0";
const MAP_START_TIMEOUT_MS = 8000;
const MGRS_URL = "/vendor/geographiclib-mgrs/index.mjs?v=1.0.5";
const OFFLINE_VECTOR_TILE_URL = `${location.origin}/api/map/tiles/{z}/{x}/{y}.pbf`;
const OFFLINE_GLYPH_URL = `${location.origin}/api/map/fonts/{fontstack}/{range}.pbf`;
const SATELLITE_TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const OPERATOR_GLYPHS = {
  dot: "●", diamond: "◆", triangle: "▲", square: "■", cross: "✚", hex: "⬢",
  star: "★", target: "◎", arrow: "➤", chevron: "⟫", bolt: "ϟ", beacon: "◉",
};
const OPERATOR_COLORS = {
  moss: "#82936f", amber: "#a48d61", rust: "#9a6552",
  steel: "#66818c", teal: "#5f887e", plum: "#806d84",
};
const UNIT_MARKER_TYPES = ["car", "tank", "helicopter", "airplane"];
const SYMBOL_MARKER_TYPES = ["text", ...UNIT_MARKER_TYPES, ...TACTICAL_MARKER_TYPES];
const ACTIVE_TRACK_MAX_AGE_SECONDS = 10 * 60;
const TRACK_HISTORY_SECONDS = 30 * 60;
const TRACK_MAX_POINTS = 48;
const AUTOMATIC_REPORT_TRANSCRIPT_LOOKBACK_SECONDS = 12 * 3600;
const WAYPOINT_ARRIVAL_RADIUS_METERS = 35;
const WAYPOINT_MAX_ARRIVAL_ACCURACY_METERS = 75;

function operatorGlyph(icon) {
  return OPERATOR_GLYPHS[icon] || OPERATOR_GLYPHS.dot;
}

function operatorColor(color) {
  return OPERATOR_COLORS[color] || OPERATOR_COLORS.moss;
}

function bearingDegrees(origin, destination) {
  const lat1 = origin[1] * Math.PI / 180;
  const lat2 = destination[1] * Math.PI / 180;
  const deltaLon = (destination[0] - origin[0]) * Math.PI / 180;
  const y = Math.sin(deltaLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(deltaLon);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function cardinalBearing(degrees) {
  const points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return points[Math.round(degrees / 45) % points.length];
}

function formatNavigationDistance(metres) {
  return metres >= 1000 ? `${(metres / 1000).toFixed(metres >= 10000 ? 0 : 1)} KM` : `${Math.max(0, Math.round(metres))} M`;
}

function readIncomingAlertPreference() {
  try {
    return localStorage.getItem("retium.incomingAlerts") !== "off";
  } catch {
    return true;
  }
}

function saveIncomingAlertPreference(enabled) {
  try {
    localStorage.setItem("retium.incomingAlerts", enabled ? "on" : "off");
  } catch {
    // Private browsing can disable storage; the current session still works.
  }
  window.RetiumAndroid?.setIncomingAlertsEnabled?.(enabled);
}

function loadReadMapMessages(destination) {
  try {
    const stored = JSON.parse(localStorage.getItem(mapMessageReadStorageKey(destination)) || "[]");
    readMapMessageIds = new Set(parseReadMessageIds(stored));
  } catch {
    readMapMessageIds = new Set();
  }
}

function saveReadMapMessages() {
  try {
    localStorage.setItem(mapMessageReadStorageKey(fieldTeamDestination), JSON.stringify(parseReadMessageIds([...readMapMessageIds])));
  } catch {
    // The current session still behaves correctly when persistent storage is unavailable.
  }
}

function readMapStylePreference() {
  try {
    if (!navigator.onLine) return "dark";
    return localStorage.getItem("retium.mapStyle") === "satellite" ? "satellite" : "dark";
  } catch {
    return "dark";
  }
}

function saveMapStylePreference(mode) {
  try {
    localStorage.setItem("retium.mapStyle", mode);
  } catch {
    // The selected map still remains active for this session.
  }
}

function readMapGridPreference() {
  try {
    return localStorage.getItem("retium.mapGrid") !== "off";
  } catch {
    return true;
  }
}

function saveMapGridPreference(enabled) {
  mapGridEnabled = enabled;
  try {
    localStorage.setItem("retium.mapGrid", enabled ? "on" : "off");
  } catch {
    // The selected grid state still remains active for this session.
  }
}

function readAutomaticLocationPreference() {
  try {
    if (window.RetiumAndroid?.isAutomaticLocationEnabled) {
      return Boolean(window.RetiumAndroid.isAutomaticLocationEnabled());
    }
    return localStorage.getItem("retium.automaticLocation") !== "off";
  } catch {
    return true;
  }
}

function saveAutomaticLocationPreference(enabled) {
  automaticLocationEnabled = enabled;
  try {
    localStorage.setItem("retium.automaticLocation", enabled ? "on" : "off");
  } catch {
    // The current session can still track without persistent browser storage.
  }
  window.RetiumAndroid?.setAutomaticLocationEnabled?.(enabled);
}

function readFieldEventCache(destination) {
  if (!destination) return [];
  try {
    const events = JSON.parse(localStorage.getItem(`retium.fieldEvents.${destination}`) || "[]");
    return withDisplayWaypointLabels(Array.isArray(events) ? events : []);
  } catch {
    return [];
  }
}

function saveFieldEventCache(destination, events) {
  if (!destination) return;
  try {
    localStorage.setItem(`retium.fieldEvents.${destination}`, JSON.stringify(events.slice(0, 60)));
  } catch {
    // Live Reticulum data still works when browser storage is unavailable.
  }
}

function mapStyleDefinition(mode = mapStyleMode) {
  if (mode === "dark") {
    return {
      version: 8,
      glyphs: OFFLINE_GLYPH_URL,
      sources: {
        offline: {
          type: "vector",
          tiles: [OFFLINE_VECTOR_TILE_URL],
          minzoom: 0,
          maxzoom: 14,
          attribution: "OpenFreeMap © OpenMapTiles · Data © OpenStreetMap contributors",
        },
      },
      layers: [
        {id: "offline-background", type: "background", paint: {"background-color": "#0b0f0c"}},
        {id: "offline-landcover", type: "fill", source: "offline", "source-layer": "landcover", paint: {"fill-color": "#151b15", "fill-opacity": 0.86}},
        {id: "offline-landuse", type: "fill", source: "offline", "source-layer": "landuse", paint: {"fill-color": "#121712", "fill-opacity": 0.72}},
        {id: "offline-park", type: "fill", source: "offline", "source-layer": "park", paint: {"fill-color": "#182018", "fill-opacity": 0.9}},
        {id: "offline-water", type: "fill", source: "offline", "source-layer": "water", paint: {"fill-color": "#0c1719"}},
        {id: "offline-waterway", type: "line", source: "offline", "source-layer": "waterway", paint: {"line-color": "#1b3134", "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.4, 14, 1.5]}},
        {id: "offline-buildings", type: "fill", source: "offline", "source-layer": "building", minzoom: 12, paint: {"fill-color": "#222620", "fill-outline-color": "#30342c", "fill-opacity": 0.9}},
        {id: "offline-road-casing", type: "line", source: "offline", "source-layer": "transportation", paint: {"line-color": "#080a08", "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.7, 10, 2.2, 14, 5]}},
        {id: "offline-roads", type: "line", source: "offline", "source-layer": "transportation", paint: {"line-color": "#45483f", "line-opacity": 0.82, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.25, 10, 1, 14, 2.6]}},
        {id: "offline-boundaries", type: "line", source: "offline", "source-layer": "boundary", paint: {"line-color": "#5f6257", "line-opacity": 0.52, "line-dasharray": [3, 2], "line-width": 0.8}},
        {
          id: "offline-road-labels", type: "symbol", source: "offline", "source-layer": "transportation_name", minzoom: 12,
          layout: {"symbol-placement": "line", "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 10, "text-letter-spacing": 0.03},
          paint: {"text-color": "#858a7e", "text-halo-color": "#0b0f0c", "text-halo-width": 1.25},
        },
        {
          id: "offline-place-labels", type: "symbol", source: "offline", "source-layer": "place",
          layout: {"text-field": ["coalesce", ["get", "name:latin"], ["get", "name"]], "text-font": ["Noto Sans Regular"], "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 12, 13], "text-letter-spacing": 0.04, "text-max-width": 8},
          paint: {"text-color": "#9b9f91", "text-halo-color": "#0b0f0c", "text-halo-width": 1.4},
        },
      ],
    };
  }
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: {
      satellite: {
        type: "raster",
        tiles: [SATELLITE_TILE_URL],
        tileSize: 256,
        maxzoom: 19,
        attribution: "Imagery © Esri",
      },
    },
    layers: [{
      id: "satellite-base",
      type: "raster",
      source: "satellite",
      paint: {
        "raster-brightness-max": 0.68,
        "raster-contrast": 0.14,
        "raster-saturation": -0.16,
        "raster-fade-duration": 0,
      },
    }],
  };
}

function updateMapStyleUi() {
  const satellite = mapStyleMode === "satellite";
  const commandToggle = $("commandMapStyleToggle");
  const fieldToggle = $("fieldMapStyleToggle");
  if (commandToggle) {
    commandToggle.textContent = satellite ? "DARK MAP" : "SATELLITE";
    commandToggle.setAttribute("aria-pressed", String(satellite));
    commandToggle.setAttribute("aria-label", satellite ? "Switch to dark map" : "Switch to satellite map");
  }
  if (fieldToggle) {
    fieldToggle.textContent = satellite ? "MAP" : "SAT";
    fieldToggle.setAttribute("aria-pressed", String(satellite));
    fieldToggle.setAttribute("aria-label", satellite ? "Switch to dark map" : "Switch to satellite map");
  }
  const commandCredit = $("commandMapCredit");
  const fieldCredit = $("fieldMapCredit");
  if (satellite) {
    if (commandCredit) commandCredit.textContent = "IMAGERY © ESRI";
    if (fieldCredit) fieldCredit.textContent = "IMAGERY © ESRI";
  } else {
    if (commandCredit) commandCredit.innerHTML = '<a href="https://openfreemap.org" target="_blank" rel="noreferrer">OPENFREEMAP</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OSM</a>';
    if (fieldCredit) fieldCredit.innerHTML = '<a href="https://openfreemap.org" target="_blank" rel="noreferrer">OFM</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OSM</a>';
  }
}

function updateMapGridUi() {
  const button = $("mapGridSettingsToggle");
  const state = $("mapGridSettingsState");
  if (button) {
    button.textContent = mapGridEnabled ? "TURN OFF" : "TURN ON";
    button.setAttribute("aria-pressed", String(mapGridEnabled));
    button.setAttribute("aria-label", mapGridEnabled ? "Hide MGRS grid" : "Show MGRS grid");
  }
  if (state) state.textContent = mapGridEnabled ? "GRID ON" : "GRID OFF";
  $("commandGridReference")?.classList.toggle("hidden", !mapGridEnabled);
  $("fieldGridReference")?.classList.remove("hidden");
}

function updateIncomingAlertsUi() {
  const button = $("incomingAlertsToggle");
  if (!button) return;
  const ready = incomingAlertsEnabled && incomingAudioReady;
  const playingVoice = ready && activeIncomingAudio && !activeIncomingAudio.paused;
  const playingCue = ready && incomingCuePlaying;
  const readingMessage = ready && incomingTtsSpeaking;
  button.classList.toggle("ready", ready);
  button.classList.toggle("muted", !incomingAlertsEnabled);
  button.setAttribute("aria-pressed", String(ready));
  $("incomingAlertsLabel").textContent = !incomingAlertsEnabled ? "ALERTS OFF" : playingVoice ? "VOICE PLAYING" : playingCue ? "MESSAGE ALERT" : readingMessage ? "READING MESSAGE" : ready ? "ALERTS ON" : "ARM ALERTS";
  const description = !incomingAlertsEnabled
    ? "Incoming text to speech and voice autoplay are muted"
    : playingVoice
      ? "Playing incoming Reticulum voice broadcast"
    : playingCue
      ? "Playing incoming message radio cue"
    : readingMessage
      ? "Reading incoming Reticulum text message"
    : ready
      ? "Incoming text to speech and voice autoplay are active"
      : "Arm incoming text to speech and voice autoplay";
  button.setAttribute("aria-label", description);
  button.title = description;
}

async function armIncomingAlerts() {
  incomingAlertsEnabled = true;
  saveIncomingAlertPreference(true);
  try {
    const silent = new Audio("data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAAAAgICA");
    silent.volume = 0;
    await silent.play();
    incomingAudioReady = true;
    toast("Incoming TTS and voice autoplay enabled");
  } catch {
    incomingAudioReady = false;
    toast("Playback is still blocked · tap ARM ALERTS again", true);
  }
  updateIncomingAlertsUi();
}

function muteIncomingAlerts() {
  incomingAlertsEnabled = false;
  incomingAudioReady = false;
  saveIncomingAlertPreference(false);
  window.speechSynthesis?.cancel();
  window.RetiumAndroid?.stopTts?.();
  incomingTtsSpeaking = false;
  if (activeIncomingCue) {
    activeIncomingCue.pause();
    activeIncomingCue = null;
  }
  incomingCuePlaying = false;
  if (activeIncomingAudio) {
    activeIncomingAudio.pause();
    activeIncomingAudio = null;
  }
  updateIncomingAlertsUi();
  toast("Incoming audio muted");
}

function hasNativeTtsBridge() {
  return typeof window.RetiumAndroid?.isTtsReady === "function"
    && typeof window.RetiumAndroid?.speakText === "function";
}

function hasNativeTtsVoiceSettings() {
  return hasNativeTtsBridge()
    && typeof window.RetiumAndroid?.getTtsVoices === "function"
    && typeof window.RetiumAndroid?.setTtsVoice === "function"
    && typeof window.RetiumAndroid?.previewTtsVoice === "function";
}

function ttsVoiceLabel(voice) {
  const source = voice.network ? "NETWORK" : "ON DEVICE";
  const variant = String(voice.id || "").split("-").slice(-2).join("-").toUpperCase();
  return `${voice.language || voice.locale || "VOICE"} · ${source}${variant ? ` · ${variant}` : ""}`;
}

async function loadTtsVoiceSettings(force = false) {
  const section = $("ttsVoiceSettings");
  if (role !== "field" || !hasNativeTtsVoiceSettings()) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  if (ttsVoicesLoaded && !force) return;
  const select = $("ttsVoiceSelect");
  const preview = $("previewTtsVoice");
  const status = $("ttsVoiceStatus");
  select.disabled = true;
  preview.disabled = true;
  status.textContent = "Reading voices installed on this phone…";
  if (!await waitForNativeTtsReady()) {
    status.textContent = "Android TTS is not ready. Close settings and try again.";
    return;
  }
  try {
    const payload = JSON.parse(window.RetiumAndroid.getTtsVoices());
    const voices = Array.isArray(payload.voices) ? payload.voices : [];
    if (!voices.length) {
      select.innerHTML = "<option>No installed voices</option>";
      status.textContent = "Install a voice in Android text-to-speech settings.";
      return;
    }
    select.innerHTML = voices.map((voice) => `<option value="${escapeHtml(voice.id)}">${escapeHtml(ttsVoiceLabel(voice))}</option>`).join("");
    if (voices.some((voice) => voice.id === payload.selected)) select.value = payload.selected;
    select.disabled = false;
    preview.disabled = false;
    status.textContent = `${voices.length} installed voice${voices.length === 1 ? "" : "s"} · selection stays on this phone`;
    ttsVoicesLoaded = true;
  } catch {
    status.textContent = "Could not read Android TTS voices.";
  }
}

async function waitForNativeTtsReady(timeoutMs = SPEECH_READY_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (window.RetiumAndroid.isTtsReady()) return true;
    } catch {
      return false;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return false;
}

window.retiumAndroidTtsEvent = (utteranceId, state) => {
  const waiter = nativeTtsWaiters.get(utteranceId);
  if (!waiter) return;
  if (state === "started") {
    waiter.started();
  } else if (state === "done" || state === "error" || state === "stopped") {
    waiter.finished(state);
  }
};

function runNativeSpeech(text, showReadingState = false) {
  return new Promise((resolve) => {
    if (!incomingAlertsEnabled || !text || !hasNativeTtsBridge()) {
      resolve();
      return;
    }
    const utteranceId = `retium-${Date.now()}-${++nativeTtsSequence}`;
    let settled = false;
    const timer = setTimeout(() => finish("timeout"), 60000);
    const finish = (state) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      nativeTtsWaiters.delete(utteranceId);
      if (showReadingState) incomingTtsSpeaking = false;
      updateIncomingAlertsUi();
      if (state === "error" || state === "timeout") {
        toast("Android TTS could not play", true);
      }
      resolve();
    };
    nativeTtsWaiters.set(utteranceId, {
      started: () => {
        if (showReadingState) incomingTtsSpeaking = true;
        updateIncomingAlertsUi();
      },
      finished: finish,
    });
    try {
      if (!window.RetiumAndroid.speakText(utteranceId, text)) finish("error");
    } catch {
      finish("error");
    }
  });
}

async function playIncomingMessageCue(source, force = false) {
  if (!force && !incomingAlertsEnabled) return;
  const audio = new Audio(source);
  audio.preload = "auto";
  audio.volume = 0.82;
  activeIncomingCue = audio;
  incomingCuePlaying = true;
  updateIncomingAlertsUi();
  const finished = new Promise((resolve) => {
    audio.addEventListener("ended", resolve, {once: true});
    audio.addEventListener("error", resolve, {once: true});
    audio.addEventListener("pause", resolve, {once: true});
  });
  try {
    await audio.play();
    incomingAudioReady = true;
    await finished;
  } catch (error) {
    if (error?.name === "NotAllowedError") {
      incomingAudioReady = false;
      toast("Message cue blocked · tap ARM ALERTS", true);
    }
  } finally {
    if (activeIncomingCue === audio) activeIncomingCue = null;
    incomingCuePlaying = false;
    updateIncomingAlertsUi();
  }
}

function waitForSpeechVoices() {
  if (!("speechSynthesis" in window)) return Promise.resolve([]);
  const available = window.speechSynthesis.getVoices();
  if (available.length) return Promise.resolve(available);
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.speechSynthesis.removeEventListener("voiceschanged", finish);
      resolve(window.speechSynthesis.getVoices());
    };
    window.speechSynthesis.addEventListener("voiceschanged", finish);
    setTimeout(finish, SPEECH_READY_TIMEOUT_MS);
  });
}

function runSpeechUtterance(utterance, showReadingState = false, timeoutMs = 0) {
  return new Promise((resolve) => {
    if (!incomingAlertsEnabled || !utterance || !("speechSynthesis" in window)) {
      resolve();
      return;
    }
    let settled = false;
    let timer = null;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (showReadingState) incomingTtsSpeaking = false;
      updateIncomingAlertsUi();
      resolve();
    };
    utterance.addEventListener("end", finish, {once: true});
    utterance.addEventListener("error", finish, {once: true});
    if (showReadingState) incomingTtsSpeaking = true;
    updateIncomingAlertsUi();
    try {
      if (timeoutMs) {
        timer = setTimeout(() => {
          finish();
          window.speechSynthesis.cancel();
        }, timeoutMs);
      }
      window.speechSynthesis.resume();
      window.speechSynthesis.speak(utterance);
    } catch {
      finish();
    }
  });
}

function incomingSpeechText(event) {
  if (event.type === "private.message") {
    return `${event.callsign} to ${currentUser.callsign}: ${event.message}`;
  }
  if (event.type === "task.created") {
    const assignment = event.assignee ? `Assigned to ${event.assignee}.` : "Open to the team.";
    return `New assignment from ${event.callsign}. ${event.title}. ${assignment}`;
  }
  if (event.type === "waypoint.arrived") {
    return `${event.operator_callsign} reached ${event.waypoint_label}.`;
  }
  return `Message from ${event.callsign}. ${event.message}`;
}

async function prepareIncomingSpeech(event) {
  const text = incomingSpeechText(event);
  if (incomingAlertsEnabled && hasNativeTtsBridge()) {
    return await waitForNativeTtsReady() ? {nativeText: text} : null;
  }
  if (!incomingAlertsEnabled || !("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") return null;
  await waitForSpeechVoices();
  if (!incomingAlertsEnabled) return null;
  const primer = new SpeechSynthesisUtterance("ready");
  primer.volume = 0;
  primer.rate = 2;
  await runSpeechUtterance(primer, false, SPEECH_READY_TIMEOUT_MS);
  if (!incomingAlertsEnabled) return null;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.96;
  utterance.pitch = 0.92;
  utterance.volume = 0.9;
  return utterance;
}

async function playIncomingTextAlert(event) {
  const utterance = await prepareIncomingSpeech(event);
  if (!incomingAlertsEnabled) return;
  await playIncomingMessageCue(INCOMING_MESSAGE_CUE_URL);
  if (incomingAlertsEnabled && utterance?.nativeText) {
    await runNativeSpeech(utterance.nativeText, true);
  } else if (incomingAlertsEnabled) {
    await runSpeechUtterance(utterance, true);
  }
  if (incomingAlertsEnabled) await playIncomingMessageCue(INCOMING_MESSAGE_END_CUE_URL);
}

function waitForIncomingVoiceReady(audio) {
  if (audio.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) return Promise.resolve(true);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (ready) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      audio.removeEventListener("loadeddata", onReady);
      audio.removeEventListener("canplay", onReady);
      audio.removeEventListener("error", onError);
      resolve(ready);
    };
    const onReady = () => finish(true);
    const onError = () => finish(false);
    const timer = setTimeout(() => finish(false), VOICE_READY_TIMEOUT_MS);
    audio.addEventListener("loadeddata", onReady, {once: true});
    audio.addEventListener("canplay", onReady, {once: true});
    audio.addEventListener("error", onError, {once: true});
    audio.load();
  });
}

async function playIncomingVoice(event) {
  if (!incomingAlertsEnabled) return;
  const audio = new Audio(`/api/audio/${encodeURIComponent(event.clip_id)}`);
  audio.preload = "auto";
  activeIncomingAudio = audio;
  const ready = await waitForIncomingVoiceReady(audio);
  if (!ready) {
    if (activeIncomingAudio === audio) activeIncomingAudio = null;
    updateIncomingAlertsUi();
    toast("Incoming voice could not be loaded", true);
    return;
  }
  if (!incomingAlertsEnabled) return;
  await playIncomingMessageCue(INCOMING_MESSAGE_CUE_URL);
  if (!incomingAlertsEnabled) return;
  let playbackResult = "error";
  try {
    const finished = new Promise((resolve) => {
      audio.addEventListener("ended", () => resolve("ended"), {once: true});
      audio.addEventListener("error", () => resolve("error"), {once: true});
      audio.addEventListener("pause", () => resolve("pause"), {once: true});
    });
    await audio.play();
    incomingAudioReady = true;
    updateIncomingAlertsUi();
    playbackResult = await finished;
  } catch {
    incomingAudioReady = false;
    updateIncomingAlertsUi();
    toast("Incoming voice blocked · tap ARM ALERTS", true);
  } finally {
    if (activeIncomingAudio === audio) activeIncomingAudio = null;
    updateIncomingAlertsUi();
  }
  if (playbackResult === "ended" && incomingAlertsEnabled) {
    await playIncomingMessageCue(INCOMING_MESSAGE_END_CUE_URL);
  }
}

function uuidFromBytes(bytes) {
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function disposeIncomingLiveVoice(session, playEndCue = false) {
  if (!session) return;
  session.disposed = true;
  session.audio?.pause();
  if (session.objectUrl) URL.revokeObjectURL(session.objectUrl);
  if (activeIncomingAudio === session.audio) activeIncomingAudio = null;
  if (incomingLiveVoice === session) incomingLiveVoice = null;
  updateIncomingAlertsUi();
  if (playEndCue && incomingAlertsEnabled) {
    incomingAlertQueue = incomingAlertQueue
      .then(() => playIncomingMessageCue(INCOMING_MESSAGE_END_CUE_URL))
      .catch(() => undefined);
  }
}

async function beginIncomingLivePlayback(session) {
  if (session.disposed || session.started || session.starting || !incomingAlertsEnabled) return;
  session.starting = true;
  await playIncomingMessageCue(INCOMING_MESSAGE_CUE_URL);
  if (session.disposed || !incomingAlertsEnabled) return;
  try {
    await session.audio.play();
    session.started = true;
    incomingAudioReady = true;
    activeIncomingAudio = session.audio;
    updateIncomingAlertsUi();
  } catch {
    toast("Live voice blocked · tap ARM ALERTS", true);
  } finally {
    session.starting = false;
  }
}

function completeIncomingLiveBuffer(session) {
  if (!session.ended || session.disposed || session.pending.length || session.sourceBuffer?.updating) return;
  if (session.mediaSource?.readyState === "open") {
    try { session.mediaSource.endOfStream(); } catch { /* The next updateend will retry. */ }
  }
}

function appendIncomingLiveBuffer(session) {
  if (session.disposed || !session.sourceBuffer || session.sourceBuffer.updating) return;
  const chunk = session.pending.shift();
  if (!chunk) {
    completeIncomingLiveBuffer(session);
    return;
  }
  try {
    session.sourceBuffer.appendBuffer(chunk);
  } catch {
    session.pending.unshift(chunk);
    if (session.ended) disposeIncomingLiveVoice(session, session.started);
  }
}

function startIncomingLiveVoice(frame) {
  if (frame.sender_hash === localIdentityHash) return;
  if (incomingLiveVoice) disposeIncomingLiveVoice(incomingLiveVoice, false);
  const mimeType = frame.mime_type || "audio/webm;codecs=opus";
  const canStream = typeof MediaSource !== "undefined" && MediaSource.isTypeSupported(mimeType);
  const session = {
    streamId: frame.stream_id,
    callsign: frame.callsign,
    mimeType,
    audio: new Audio(),
    mediaSource: null,
    sourceBuffer: null,
    objectUrl: null,
    pending: [],
    fallbackChunks: [],
    started: false,
    starting: false,
    ended: false,
    disposed: false,
  };
  incomingLiveVoice = session;
  toast(`Live voice · ${frame.callsign}`);
  if (!incomingAlertsEnabled) return;
  if (!canStream) return;
  session.mediaSource = new MediaSource();
  session.objectUrl = URL.createObjectURL(session.mediaSource);
  session.audio.src = session.objectUrl;
  session.audio.preload = "auto";
  session.audio.addEventListener("ended", () => disposeIncomingLiveVoice(session, true), {once: true});
  session.mediaSource.addEventListener("sourceopen", () => {
    if (session.disposed) return;
    try {
      session.sourceBuffer = session.mediaSource.addSourceBuffer(mimeType);
      session.sourceBuffer.mode = "sequence";
      session.sourceBuffer.addEventListener("updateend", () => {
        if (!session.started) beginIncomingLivePlayback(session);
        appendIncomingLiveBuffer(session);
      });
      appendIncomingLiveBuffer(session);
    } catch {
      session.fallbackChunks.push(...session.pending);
      session.pending = [];
      session.sourceBuffer = null;
      session.mediaSource = null;
      if (session.ended) playCompletedLiveFallback(session);
    }
  }, {once: true});
}

async function playCompletedLiveFallback(session) {
  if (session.disposed || !session.fallbackChunks.length || !incomingAlertsEnabled) {
    disposeIncomingLiveVoice(session, false);
    return;
  }
  const blob = new Blob(session.fallbackChunks, {type: session.mimeType});
  session.objectUrl = URL.createObjectURL(blob);
  session.audio.src = session.objectUrl;
  const ready = await waitForIncomingVoiceReady(session.audio);
  if (!ready || session.disposed) {
    disposeIncomingLiveVoice(session, false);
    return;
  }
  await playIncomingMessageCue(INCOMING_MESSAGE_CUE_URL);
  if (session.disposed || !incomingAlertsEnabled) return;
  activeIncomingAudio = session.audio;
  session.audio.addEventListener("ended", () => disposeIncomingLiveVoice(session, true), {once: true});
  try {
    await session.audio.play();
    session.started = true;
    updateIncomingAlertsUi();
  } catch {
    disposeIncomingLiveVoice(session, false);
  }
}

function receiveIncomingLiveChunk(streamId, sequence, audio) {
  const session = incomingLiveVoice;
  if (!session || session.streamId !== streamId || session.disposed) return;
  if (session.sourceBuffer || session.mediaSource) {
    session.pending.push(audio);
    appendIncomingLiveBuffer(session);
  } else {
    session.fallbackChunks.push(audio);
  }
}

function finishIncomingLiveVoice(streamId, canceled = false) {
  const session = incomingLiveVoice;
  if (!session || session.streamId !== streamId) return;
  if (canceled) {
    disposeIncomingLiveVoice(session, false);
    return;
  }
  session.ended = true;
  if (!session.mediaSource) {
    playCompletedLiveFallback(session);
    return;
  }
  if (!session.sourceBuffer) return;
  completeIncomingLiveBuffer(session);
}

function setLiveVoiceAvailability(ready, peers = 0) {
  liveVoiceReady = Boolean(ready);
  document.querySelectorAll(".ptt-button").forEach((button) => {
    if (button.dataset.privateRecipient) {
      button.classList.remove("live-ready");
      button.dataset.idleHint = "PRIVATE MAILBOX · RECORDED · MAX 8 SEC";
      if (!pttRecorder) setPttState("idle");
      return;
    }
    button.classList.toggle("live-ready", liveVoiceReady);
    button.dataset.idleHint = liveVoiceReady
      ? `LIVE RNS · ${Math.max(1, peers)} LINK · MAX 8 SEC`
      : "RECORDED FALLBACK · MAX 8 SEC";
    if (!pttRecorder) setPttState("idle");
  });
}

function handleLiveVoiceMessage(message) {
  if (typeof message.data !== "string") {
    const packet = new Uint8Array(message.data);
    if (packet.byteLength <= LIVE_VOICE_HEADER_BYTES) return;
    const streamId = uuidFromBytes(packet.slice(0, 16));
    const view = new DataView(packet.buffer, packet.byteOffset + 16, 4);
    receiveIncomingLiveChunk(streamId, view.getUint32(0), packet.slice(LIVE_VOICE_HEADER_BYTES).buffer);
    return;
  }
  let frame;
  try { frame = JSON.parse(message.data); } catch { return; }
  if (frame.type === "live.transport") {
    setLiveVoiceAvailability(frame.ready, frame.peers || 0);
  } else if (frame.type === "live.start") {
    startIncomingLiveVoice(frame);
  } else if (frame.type === "live.end" || frame.type === "live.cancel") {
    finishIncomingLiveVoice(frame.stream_id, frame.type === "live.cancel");
  } else if (frame.type === "live.error" && (!frame.stream_id || frame.stream_id === pttLiveStreamId)) {
    pttLiveFailed = true;
    setLiveVoiceAvailability(false, 0);
    toast("Live link interrupted · clip fallback armed", true);
  }
}

function connectLiveVoice() {
  if (!teamOperational || liveVoiceSocket?.readyState === WebSocket.OPEN || liveVoiceSocket?.readyState === WebSocket.CONNECTING) return;
  if (liveVoiceReconnectTimer) clearTimeout(liveVoiceReconnectTimer);
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/api/voice/live`);
  liveVoiceSocket = socket;
  socket.binaryType = "arraybuffer";
  socket.addEventListener("message", handleLiveVoiceMessage);
  socket.addEventListener("close", () => {
    if (liveVoiceSocket === socket) liveVoiceSocket = null;
    setLiveVoiceAvailability(false, 0);
    if (teamOperational) liveVoiceReconnectTimer = setTimeout(connectLiveVoice, 2000);
  });
  socket.addEventListener("error", () => setLiveVoiceAvailability(false, 0));
}

function queueIncomingAlerts(events) {
  try {
    const nativeSeen = JSON.parse(window.RetiumAndroid?.getSeenIncomingEventIds?.() || "[]");
    if (Array.isArray(nativeSeen)) nativeSeen.forEach((eventId) => seenIncomingEventIds.add(eventId));
  } catch {
    // The native bridge is Android-only; browser alert tracking remains in-memory.
  }
  const alertable = events.filter((event) => event?.id && ["chat.message", "private.message", "private.ptt", "ptt.broadcast", "task.created", "waypoint.arrived"].includes(event.type));
  if (!incomingFeedInitialized) {
    alertable.forEach((event) => seenIncomingEventIds.add(event.id));
    window.RetiumAndroid?.syncSeenIncomingEventIds?.(JSON.stringify([...seenIncomingEventIds].slice(-200)));
    incomingFeedInitialized = true;
    return;
  }
  const incoming = alertable
    .filter((event) => !seenIncomingEventIds.has(event.id))
    .sort((left, right) => left.created_at - right.created_at);
  for (const event of incoming) {
    seenIncomingEventIds.add(event.id);
    if (["ptt.broadcast", "private.ptt"].includes(event.type)) requestVoiceTranscript(event.clip_id, false);
    if (event.network?.sender_hash === localIdentityHash || !incomingAlertsEnabled) continue;
    incomingAlertQueue = incomingAlertQueue
      .then(() => ["ptt.broadcast", "private.ptt"].includes(event.type) ? playIncomingVoice(event) : playIncomingTextAlert(event))
      .catch(() => undefined);
  }
  window.RetiumAndroid?.syncSeenIncomingEventIds?.(JSON.stringify([...seenIncomingEventIds].slice(-200)));
}

function shortHash(value, length = 10) {
  return value ? `${value.slice(0, length)}…${value.slice(-6)}` : "—";
}

function timeLabel(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

function updateOwnBearingLine() {
  const source = fieldMap?.getSource("field-bearing");
  if (!source) return;
  if (!fieldBearingOrigin || lastCompassHeading === null) {
    if (fieldBearingHasData) source.setData(emptyFeatureCollection());
    fieldBearingHasData = false;
    return;
  }
  const canvas = fieldMap.getCanvas();
  const start = fieldMap.project(fieldBearingOrigin);
  const endPixel = bearingRayEndPixel(start, lastCompassHeading, canvas.clientWidth || canvas.width, canvas.clientHeight || canvas.height);
  if (!endPixel) return;
  const end = fieldMap.unproject([endPixel.x, endPixel.y]);
  const feature = bearingRayFeature(fieldBearingOrigin, [end.lng, end.lat], lastCompassHeading);
  source.setData({type: "FeatureCollection", features: feature ? [feature] : []});
  fieldBearingHasData = Boolean(feature);
}

function scheduleOwnBearingLine() {
  if (fieldBearingFrame !== null) return;
  fieldBearingFrame = requestAnimationFrame(() => {
    fieldBearingFrame = null;
    updateOwnBearingLine();
  });
}

function renderFieldCompassTargets() {
  const layer = $("fieldCompassTargets");
  if (!layer) return;
  if (lastCompassHeading === null) {
    layer.replaceChildren();
    return;
  }
  layer.replaceChildren(...fieldCompassMapTargets.map((target) => {
    const placement = compassTargetIndicator(lastCompassHeading, target.bearing);
    const marker = document.createElement("i");
    marker.className = `compass-target compass-target-${target.kind}${placement?.offscreen ? " offscreen" : ""}`;
    marker.style.left = `${placement?.left ?? 50}%`;
    marker.dataset.side = placement?.side || "center";
    marker.title = `${target.kind === "contact" ? "Contact" : "Waypoint"} · ${target.label} · ${Math.round(target.bearing).toString().padStart(3, "0")}° · ${formatNavigationDistance(target.distance)}`;
    return marker;
  }));
  const waypointCount = fieldCompassMapTargets.filter((target) => target.kind === "waypoint").length;
  const contactCount = fieldCompassMapTargets.length - waypointCount;
  const targetSummary = [
    waypointCount ? `${waypointCount} waypoint${waypointCount === 1 ? "" : "s"}` : "",
    contactCount ? `${contactCount} contact${contactCount === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(", ");
  $("fieldCompass").setAttribute(
    "aria-label",
    `Heading ${Math.round(lastCompassHeading)} degrees ${compassCardinal(lastCompassHeading)}${targetSummary ? `. ${targetSummary} shown on compass` : ""}`,
  );
}

function updateFieldCompassMapTargets(origin, locations, contactFeatures) {
  if (!origin) {
    fieldCompassMapTargets = [];
    renderFieldCompassTargets();
    return;
  }
  const waypoints = locations
    .filter((event) => event.type === "marker.created" && event.marker_type === "waypoint")
    .map((event) => ({
      id: event.id,
      kind: "waypoint",
      label: event.display_label || event.label || "Waypoint",
      point: [event.lon, event.lat],
    }));
  const contacts = contactFeatures.features
    .filter((feature) => feature.geometry?.type === "Point" && feature.properties?.kind === "contact")
    .map((feature) => ({
      id: feature.properties.reportId || feature.properties.id,
      kind: "contact",
      label: feature.properties.label || "Contact",
      point: feature.geometry.coordinates,
    }));
  fieldCompassMapTargets = [...waypoints, ...contacts]
    .filter((target) => target.point.every(Number.isFinite))
    .map((target) => ({
      ...target,
      bearing: bearingDegrees(origin, target.point),
      distance: distanceMeters(origin, target.point),
    }));
  renderFieldCompassTargets();
}

function renderFieldCompass(value, native = false) {
  const heading = normalizeHeading(value);
  if (heading === null) return;
  if (lastCompassHeading !== null) {
    const change = Math.abs(((heading - lastCompassHeading + 540) % 360) - 180);
    if (change < 0.35) return;
  }
  lastCompassHeading = heading;
  if (native) nativeCompassUpdatedAt = performance.now();
  const compass = $("fieldCompass");
  compass.classList.remove("waiting");
  compass.setAttribute("aria-label", `Heading ${Math.round(heading)} degrees ${compassCardinal(heading)}`);
  $("fieldCompassCardinal").textContent = compassCardinal(heading);
  $("fieldCompassHeading").textContent = String(Math.round(heading) % 360).padStart(3, "0");
  const tape = $("fieldCompassTape");
  tape.replaceChildren(...compassTicks(heading).map((tick) => {
    const marker = document.createElement("span");
    marker.className = `compass-tick${tick.major ? " major" : ""}`;
    marker.style.left = `${tick.left}%`;
    if (tick.label) {
      const label = document.createElement("b");
      label.textContent = tick.label;
      marker.append(label);
    }
    return marker;
  }));
  renderFieldCompassTargets();
  scheduleOwnBearingLine();
}

window.retiumAndroidHeading = (heading) => renderFieldCompass(heading, true);

function browserCompassHeading(event) {
  if (performance.now() - nativeCompassUpdatedAt < 2000) return;
  const alpha = Number(event.webkitCompassHeading ?? event.alpha);
  if (!Number.isFinite(alpha)) return;
  const screenAngle = Number(screen.orientation?.angle ?? window.orientation ?? 0);
  const heading = event.webkitCompassHeading ?? (360 - alpha + screenAngle);
  renderFieldCompass(heading);
}

window.addEventListener("deviceorientationabsolute", browserCompassHeading);
window.addEventListener("deviceorientation", browserCompassHeading);

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}

let connectivitySnapshot = null;
let connectivityProbe = null;

function setNetwork(state) {
  if (state?.team?.destination !== connectivitySnapshot?.team?.destination) connectivityProbe = null;
  connectivitySnapshot = state;
  renderConnectivity();
}

function renderConnectivity() {
  const info = connectivityState(connectivitySnapshot, connectivityProbe);
  const toggle = $("networkToggle");
  toggle.dataset.level = info.bars;
  toggle.title = `${info.label} · connection details`;
  toggle.setAttribute("aria-label", `${info.label}. Open connection details`);
  toggle.querySelectorAll("i").forEach((bar, index) => bar.classList.toggle("active", index < info.bars));
  $("connectivitySummary").textContent = info.label;
  const metrics = [
    ["Active links", `${info.active.length} / ${info.interfaces.length}`],
    ["Team", info.team],
    ["Response", info.latency === null ? "Not measured" : `${Math.round(info.latency)} ms`],
    ["Queued", connectivitySnapshot ? String(fieldQueuedEvents || connectivitySnapshot.network?.queued_events || 0) : "Unknown"],
  ];
  $("connectivityMetrics").innerHTML = metrics.map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  $("connectivityLinks").innerHTML = info.interfaces.map((item) => `<li><span>${escapeHtml(item.short_name || item.name || item.type || "Interface")}</span><small>${info.active.includes(item) ? "UP" : "IDLE / DOWN"}</small></li>`).join("");
}

function closeConnectivity() {
  $("connectivityPanel").classList.add("hidden");
  $("networkToggle").setAttribute("aria-expanded", "false");
}
$("networkToggle").addEventListener("click", (event) => {
  event.stopPropagation();
  const opening = $("connectivityPanel").classList.contains("hidden");
  renderConnectivity();
  $("connectivityPanel").classList.toggle("hidden", !opening);
  $("networkToggle").setAttribute("aria-expanded", String(opening));
  if (opening) $("closeConnectivity").focus();
});
$("closeConnectivity").addEventListener("click", () => { closeConnectivity(); $("networkToggle").focus(); });
document.addEventListener("click", (event) => {
  if (!$("connectivityPanel").contains(event.target) && !$("networkToggle").contains(event.target)) closeConnectivity();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("connectivityPanel").classList.contains("hidden")) {
    closeConnectivity(); $("networkToggle").focus();
  }
});
setInterval(renderConnectivity, 5000);

function voiceTime(seconds, compact = false) {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  if (compact && safe < 10) return `${safe.toFixed(1)}s`;
  const rounded = Math.floor(safe);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
}

function voicePlayerMarkup(event) {
  const duration = Math.max(0, Number(event.duration_ms || 0) / 1000);
  const bars = Array.from({length: VOICE_WAVEFORM_BARS}, () => "<i></i>").join("");
  const transcript = voiceTranscriptCache.get(event.clip_id);
  const transcriptText = transcript?.status === "ready"
    ? transcript.text
    : transcript?.status === "no_speech"
      ? "No speech detected."
      : transcript?.status === "processing"
        ? "Transcribing locally…"
        : "Loads with audio.";
  const transcriptLanguage = transcript?.status === "ready" && transcript.language
    ? ` · ${String(transcript.language).toUpperCase()}`
    : "";
  return `<div class="voice-message">
    <button type="button" class="voice-player" data-audio-src="/api/audio/${encodeURIComponent(event.clip_id)}" data-clip-id="${escapeHtml(event.clip_id)}" data-duration="${duration}" data-callsign="${escapeHtml(event.callsign)}" aria-label="Play voice transmission from ${escapeHtml(event.callsign)}, ${voiceTime(duration, true)}">
      <span class="voice-control" aria-hidden="true"></span>
      <span class="voice-waveform" aria-hidden="true">${bars}</span>
      <span class="voice-time">${voiceTime(duration, true)}</span>
    </button>
    <span class="voice-transcript ${transcript?.status || "idle"}" data-transcript-id="${escapeHtml(event.clip_id)}" aria-live="polite">
      <b>TRANSCRIPT${transcriptLanguage}</b>
      <span>${escapeHtml(transcriptText)}</span>
    </span>
  </div>`;
}

function calculateWaveform(audioBuffer) {
  const peaks = new Array(VOICE_WAVEFORM_BARS).fill(0);
  for (let channelIndex = 0; channelIndex < audioBuffer.numberOfChannels; channelIndex += 1) {
    const samples = audioBuffer.getChannelData(channelIndex);
    const blockSize = Math.max(1, Math.floor(samples.length / VOICE_WAVEFORM_BARS));
    for (let bar = 0; bar < VOICE_WAVEFORM_BARS; bar += 1) {
      const start = bar * blockSize;
      const end = bar === VOICE_WAVEFORM_BARS - 1 ? samples.length : Math.min(samples.length, start + blockSize);
      const sampleStep = Math.max(1, Math.floor((end - start) / 256));
      let energy = 0;
      let count = 0;
      for (let index = start; index < end; index += sampleStep) {
        energy += samples[index] * samples[index];
        count += 1;
      }
      peaks[bar] = Math.max(peaks[bar], count ? Math.sqrt(energy / count) : 0);
    }
  }
  const strongest = Math.max(...peaks, 0.001);
  return peaks.map((peak) => Math.max(0.12, Math.sqrt(peak / strongest)));
}

function getVoiceAsset(source) {
  if (voiceAssetCache.has(source)) return voiceAssetCache.get(source);
  const request = (async () => {
    const response = await fetch(source);
    if (!response.ok) throw new Error("Voice transmission could not be loaded");
    const blob = await response.blob();
    const asset = {url: URL.createObjectURL(blob), blob, peaks: null, duration: null};
    const AudioDecoder = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (AudioDecoder) {
      try {
        const decoder = new AudioDecoder(1, 1, 44100);
        const decoded = await decoder.decodeAudioData(await blob.arrayBuffer());
        asset.peaks = calculateWaveform(decoded);
        asset.duration = decoded.duration;
      } catch {
        // Playback still works when a browser cannot decode waveform samples.
      }
    }
    return asset;
  })().catch((error) => {
    voiceAssetCache.delete(source);
    throw error;
  });
  voiceAssetCache.set(source, request);
  return request;
}

function updateTranscriptDisplays(clipId, transcript) {
  voiceTranscriptCache.set(clipId, transcript);
  document.querySelectorAll(`[data-transcript-id="${CSS.escape(clipId)}"]`).forEach((element) => {
    element.className = `voice-transcript ${transcript.status}`;
    const language = transcript.status === "ready" && transcript.language
      ? ` · ${String(transcript.language).toUpperCase()}`
      : "";
    element.querySelector("b").textContent = `TRANSCRIPT${language}`;
    element.querySelector("span").textContent = transcript.status === "ready"
      ? transcript.text
      : transcript.status === "no_speech"
        ? "No speech detected."
        : transcript.status === "processing"
          ? "Transcribing locally…"
          : "Transcript unavailable. Tap audio to retry.";
  });
  if (transcript.status === "ready") updateTranscriptContactVisuals(clipId);
}

function updateTranscriptContactVisuals(clipId) {
  const events = role === "gateway" ? commandEvents : fieldEvents;
  const voiceEvent = events.find((event) => event.type === "ptt.broadcast" && event.clip_id === clipId);
  const report = voiceEvent && parseAutomaticReport(contactReportText(voiceEvent));
  if (!report) return;
  document.querySelectorAll(`[data-clip-id="${CSS.escape(clipId)}"]`).forEach((player) => {
    const card = player.closest(".latest-message");
    if (!card) return;
    card.classList.add("contact-message");
    const label = card.querySelector(".map-message-type");
    if (label) {
      label.classList.add("contact");
      label.textContent = report.action === "cancel-last" ? "MAP CANCEL" : "MAP REPORT";
    }
  });
  if (role === "gateway" && operationalMap) void renderMap(commandEvents);
  if (role === "field" && fieldMap) void renderFieldMap(fieldEvents);
}

function primeRecentVoiceTranscripts(events) {
  const cutoff = Date.now() / 1000 - AUTOMATIC_REPORT_TRANSCRIPT_LOOKBACK_SECONDS;
  events
    .filter((event) => ["ptt.broadcast", "private.ptt"].includes(event.type) && event.created_at >= cutoff)
    .filter((event) => !voiceTranscriptCache.has(event.clip_id) && !voiceTranscriptRequests.has(event.clip_id))
    .slice(0, 12)
    .forEach((event, index) => setTimeout(() => requestVoiceTranscript(event.clip_id, false), index * 250));
}

async function requestVoiceTranscript(clipId, start = false, attempt = 0) {
  if (!clipId || voiceTranscriptRequests.has(clipId)) return;
  voiceTranscriptRequests.add(clipId);
  if (start || voiceTranscriptCache.get(clipId)?.status === "processing") {
    updateTranscriptDisplays(clipId, {status: "processing", clip_id: clipId});
  }
  try {
    const response = await fetch(`/api/transcriptions/${encodeURIComponent(clipId)}?start=${start ? "true" : "false"}`, {cache: "no-store"});
    const transcript = await response.json();
    if (!response.ok) throw new Error(transcript.detail || "Transcript unavailable");
    updateTranscriptDisplays(clipId, transcript);
    if (transcript.status === "processing" && attempt < 40) {
      setTimeout(() => requestVoiceTranscript(clipId, false, attempt + 1), role === "field" ? 4000 : 2000);
    }
  } catch {
    updateTranscriptDisplays(clipId, {status: "unavailable", clip_id: clipId});
  } finally {
    voiceTranscriptRequests.delete(clipId);
  }
}

function updateVoicePlayer(button, state) {
  if (!button.isConnected) return;
  const duration = Number.isFinite(state.audio.duration) ? state.audio.duration : state.duration;
  const progress = duration > 0 ? state.audio.currentTime / duration : 0;
  button.classList.toggle("playing", !state.audio.paused && !state.audio.ended);
  button.classList.toggle("loading", state.loading || state.starting);
  button.querySelectorAll(".voice-waveform i").forEach((bar, index) => {
    bar.classList.toggle("played", (index + 0.5) / VOICE_WAVEFORM_BARS <= progress);
  });
  const time = button.querySelector(".voice-time");
  time.textContent = state.audio.currentTime > 0
    ? `${voiceTime(state.audio.currentTime)} / ${voiceTime(duration)}`
    : voiceTime(duration, true);
  const action = state.loading ? "Loading" : state.starting ? "Starting" : button.classList.contains("playing") ? "Pause" : "Play";
  button.setAttribute("aria-label", `${action} voice transmission from ${button.dataset.callsign} · ${voiceTime(duration, true)}`);
}

function applyVoiceWaveform(button, peaks) {
  if (!peaks || !button.isConnected) return;
  button.classList.add("waveform-ready");
  button.querySelectorAll(".voice-waveform i").forEach((bar, index) => {
    bar.style.setProperty("--voice-level", `${Math.round(peaks[index] * 100)}%`);
  });
}

function getVoicePlayerState(button) {
  if (voicePlayerStates.has(button)) return voicePlayerStates.get(button);
  const audio = new Audio();
  audio.preload = "auto";
  const state = {audio, duration: Number(button.dataset.duration) || 0, loading: false, starting: false, sequence: 0};
  voicePlayerStates.set(button, state);
  audio.addEventListener("timeupdate", () => updateVoicePlayer(button, state));
  audio.addEventListener("play", () => updateVoicePlayer(button, state));
  audio.addEventListener("pause", () => updateVoicePlayer(button, state));
  audio.addEventListener("ended", () => {
    if (activeVoicePlayer === state) activeVoicePlayer = null;
    updateVoicePlayer(button, state);
  });
  return state;
}

async function toggleVoicePlayer(button) {
  const state = getVoicePlayerState(button);
  if (state.loading || state.starting) return;
  if (!state.audio.paused && !state.audio.ended) {
    state.audio.pause();
    return;
  }
  if (activeVoicePlayer && activeVoicePlayer !== state) {
    activeVoicePlayer.sequence += 1;
    activeVoicePlayer.starting = false;
    activeVoicePlayer.audio.pause();
  }
  activeVoicePlayer = state;
  const sequence = ++state.sequence;
  if (!state.audio.src) {
    state.loading = true;
    updateVoicePlayer(button, state);
    try {
      const asset = await getVoiceAsset(button.dataset.audioSrc);
      state.audio.src = asset.url;
      if (Number.isFinite(asset.duration)) state.duration = asset.duration;
      applyVoiceWaveform(button, asset.peaks);
      requestVoiceTranscript(button.dataset.clipId, true);
      const ready = await waitForIncomingVoiceReady(state.audio);
      if (!ready) throw new Error("Voice transmission could not be decoded");
    } catch (error) {
      if (activeVoicePlayer === state) activeVoicePlayer = null;
      toast(error.message, true);
      return;
    } finally {
      state.loading = false;
      updateVoicePlayer(button, state);
    }
  }
  if (state.audio.ended) {
    state.audio.currentTime = 0;
    updateVoicePlayer(button, state);
  }
  state.starting = true;
  updateVoicePlayer(button, state);
  if (state.sequence !== sequence || activeVoicePlayer !== state) {
    state.starting = false;
    updateVoicePlayer(button, state);
    return;
  }
  try {
    await state.audio.play();
  } catch {
    if (activeVoicePlayer === state) activeVoicePlayer = null;
    toast("Voice playback was blocked · click again", true);
  } finally {
    state.starting = false;
    updateVoicePlayer(button, state);
  }
}

function eventDescription(event) {
  if (event.type === "chat.message") return escapeHtml(event.message);
  if (event.type === "private.message") {
    return `<span class="private-route">${escapeHtml(privateRouteLabel(event))}</span><br>${escapeHtml(event.message)}`;
  }
  if (event.type === "private.ptt") {
    return `<span class="private-route">${escapeHtml(privateRouteLabel(event))}</span>${voicePlayerMarkup(event)}`;
  }
  if (event.type === "task.created") return `New assignment · ${escapeHtml(event.title)}${event.assignee ? ` · ${escapeHtml(event.assignee)}` : " · OPEN TO TEAM"}`;
  if (event.type === "drawing.created") return event.drawing_type === "arrow" ? "Shared map arrow" : `Shared freehand map trace · ${event.points.length} points`;
  if (event.type === "position.updated" && event.network?.became_active) {
    const minutes = Math.max(60, Math.round(Number(event.network.position_silence_seconds || 3600) / 60));
    const duration = minutes >= 120 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
    return `Location sharing resumed after ${duration} without a fix`;
  }
  if (event.type === "position.updated") return "Routine position update";
  if (event.type === "team.joined") return "Joined this team with a verified Reticulum identity";
  if (event.type === "profile.updated") return "Updated callsign, icon, and marker color";
  if (event.type === "task.completed") return `Completed task · ${shortHash(event.task_id, 8)}`;
  if (event.type === "waypoint.arrived") return `${escapeHtml(event.operator_callsign)} reached ${escapeHtml(event.waypoint_label)} · ${Math.round(event.distance_m)} m from marker`;
  if (event.type === "ptt.broadcast") return voicePlayerMarkup(event);
  return `${escapeHtml(event.marker_type.toUpperCase())} · ${escapeHtml(event.display_label || event.label)}<br>${event.lat.toFixed(5)}, ${event.lon.toFixed(5)}`;
}

function intelEvents(events) {
  return events.filter((event) => event.type !== "position.updated" || event.network?.became_active === true);
}

function intelEventType(event) {
  if (event.type === "private.message") return "private message";
  if (event.type === "private.ptt") return "private ptt";
  return event.type === "position.updated" && event.network?.became_active ? "operator.active" : event.type;
}

function privateRouteLabel(event) {
  const recipient = event.recipient_hash === localIdentityHash
    ? currentUser.callsign
    : fieldOperators.get(event.recipient_hash)?.callsign || shortHash(event.recipient_hash, 8);
  return `${event.callsign} → ${recipient}`;
}

function combinedFieldFeedEvents() {
  return [...fieldEvents, ...privateMessages].sort((left, right) =>
    Number(right.network?.received_at || right.created_at || 0)
      - Number(left.network?.received_at || left.created_at || 0));
}

function arrowGeometry(points) {
  if (!Array.isArray(points) || points.length < 2) return {type: "LineString", coordinates: points || []};
  const start = points[0];
  const end = points[points.length - 1];
  const latitudeScale = Math.max(0.2, Math.cos(((start[1] + end[1]) / 2) * Math.PI / 180));
  const dx = (end[0] - start[0]) * latitudeScale;
  const dy = end[1] - start[1];
  const length = Math.hypot(dx, dy);
  if (length === 0) return {type: "LineString", coordinates: points};
  const head = length * 0.24;
  const ux = dx / length;
  const uy = dy / length;
  const baseX = end[0] * latitudeScale - ux * head;
  const baseY = end[1] - uy * head;
  const wing = head * 0.58;
  const left = [(baseX - uy * wing) / latitudeScale, baseY + ux * wing];
  const right = [(baseX + uy * wing) / latitudeScale, baseY - ux * wing];
  return {type: "MultiLineString", coordinates: [points, [end, left], [end, right]]};
}

function drawingGeometry(event) {
  return event.drawing_type === "arrow"
    ? arrowGeometry(event.points)
    : {type: "LineString", coordinates: event.points};
}

function unitMarkerImage(type) {
  const size = 72;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  context.scale(2, 2);
  context.lineWidth = 1.8;
  context.lineCap = "round";
  context.lineJoin = "round";
  context.strokeStyle = "#d1d0bf";
  context.fillStyle = "#111711";
  context.beginPath();
  context.arc(18, 18, 16, 0, Math.PI * 2);
  context.fill();
  context.strokeStyle = "#a48d61";
  context.stroke();
  context.strokeStyle = "#d1d0bf";
  context.fillStyle = "#d1d0bf";
  if (type === "car") {
    context.beginPath();
    context.moveTo(6, 22); context.lineTo(8, 16); context.lineTo(13, 13); context.lineTo(24, 13); context.lineTo(29, 18); context.lineTo(30, 22); context.closePath();
    context.stroke();
    context.beginPath(); context.arc(11, 23, 2.2, 0, Math.PI * 2); context.arc(26, 23, 2.2, 0, Math.PI * 2); context.fill();
  } else if (type === "tank") {
    context.strokeRect(6, 19, 24, 8);
    context.strokeRect(11, 14, 12, 5);
    context.beginPath(); context.moveTo(23, 16); context.lineTo(31, 13); context.stroke();
    context.beginPath(); context.moveTo(10, 23); context.lineTo(26, 23); context.stroke();
  } else if (type === "helicopter") {
    context.beginPath(); context.ellipse(15, 19, 7, 4.5, 0, 0, Math.PI * 2); context.stroke();
    context.beginPath(); context.moveTo(22, 18); context.lineTo(30, 14); context.lineTo(31, 18); context.moveTo(8, 12); context.lineTo(24, 12); context.moveTo(16, 12); context.lineTo(16, 9); context.moveTo(10, 26); context.lineTo(23, 26); context.stroke();
  } else {
    context.beginPath();
    context.moveTo(18, 5); context.lineTo(21, 15); context.lineTo(30, 20); context.lineTo(30, 23); context.lineTo(21, 21); context.lineTo(20, 29); context.lineTo(24, 32); context.lineTo(24, 34); context.lineTo(18, 32); context.lineTo(12, 34); context.lineTo(12, 32); context.lineTo(16, 29); context.lineTo(15, 21); context.lineTo(6, 23); context.lineTo(6, 20); context.lineTo(15, 15); context.closePath(); context.fill();
  }
  return context.getImageData(0, 0, size, size);
}

function hostileMarkerImage() {
  const size = 72;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  context.scale(2, 2);
  context.lineWidth = 2;
  context.lineCap = "round";
  context.lineJoin = "round";
  context.fillStyle = "#160c0a";
  context.strokeStyle = "#c85f4f";
  context.beginPath();
  context.moveTo(18, 2); context.lineTo(34, 18); context.lineTo(18, 34); context.lineTo(2, 18); context.closePath();
  context.fill();
  context.stroke();
  context.beginPath();
  context.moveTo(12, 12); context.lineTo(24, 24); context.moveTo(24, 12); context.lineTo(12, 24);
  context.stroke();
  return context.getImageData(0, 0, size, size);
}

function addUnitMarkerImages(map) {
  UNIT_MARKER_TYPES.forEach((type) => {
    const imageName = `reticom-${type}`;
    if (!map.hasImage(imageName)) map.addImage(imageName, unitMarkerImage(type), {pixelRatio: 2});
  });
  if (!map.hasImage("reticom-enemy")) map.addImage("reticom-enemy", hostileMarkerImage(), {pixelRatio: 2});
}

function unitMarkerLayer(id, source) {
  return {
    id,
    type: "symbol",
    source,
    filter: ["in", ["get", "markerType"], ["literal", UNIT_MARKER_TYPES]],
    layout: {
      "icon-image": ["concat", "reticom-", ["get", "markerType"]],
      "icon-size": 1,
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "text-field": ["get", "label"],
      "text-size": 11,
      "text-font": ["Noto Sans Regular"],
      "text-offset": [0, 2.05],
      "text-anchor": "top",
    },
    paint: {"text-color": "#d1d0bf", "text-halo-color": "#090c09", "text-halo-width": 1.5},
  };
}

function textMarkerLayer(id, source) {
  return {
    id,
    type: "symbol",
    source,
    filter: ["==", ["get", "markerType"], "text"],
    layout: {"text-field": ["get", "label"], "text-size": 14, "text-font": ["Noto Sans Regular"], "text-allow-overlap": true},
    paint: {"text-color": "#e0dfcf", "text-halo-color": "#090c09", "text-halo-width": 2.5},
  };
}

function tacticalMarkerPointLayer(id, source) {
  return {
    id,
    type: "circle",
    source,
    filter: ["in", ["get", "markerType"], ["literal", TACTICAL_MARKER_TYPES]],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 11, 15, 15],
      "circle-color": "#090c09",
      "circle-stroke-width": 2.5,
      "circle-stroke-color": ["coalesce", ["get", "markerColor"], "#a48d61"],
    },
  };
}

function tacticalMarkerLabelLayer(id, source) {
  return {
    id,
    type: "symbol",
    source,
    filter: ["in", ["get", "markerType"], ["literal", TACTICAL_MARKER_TYPES]],
    layout: {
      "text-field": ["get", "symbol"],
      "text-size": 9,
      "text-font": ["Noto Sans Regular"],
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": ["coalesce", ["get", "markerColor"], "#d1d0bf"],
      "text-halo-color": "#090c09",
      "text-halo-width": 1,
    },
  };
}

function tacticalMarkerCaptionLayer(id, source) {
  return {
    id,
    type: "symbol",
    source,
    minzoom: 10,
    filter: ["in", ["get", "markerType"], ["literal", TACTICAL_MARKER_TYPES]],
    layout: {
      "text-field": ["get", "label"],
      "text-size": 11,
      "text-font": ["Noto Sans Regular"],
      "text-offset": [0, 1.8],
      "text-anchor": "top",
      "text-allow-overlap": false,
    },
    paint: {"text-color": "#d1d0bf", "text-halo-color": "#090c09", "text-halo-width": 1.5},
  };
}

function renderTimeline(events) {
  const visibleEvents = intelEvents(events);
  if (!visibleEvents.length) {
    $("timeline").className = "timeline-list empty-copy";
    $("timeline").textContent = "No packets received yet. This view does not seed demo data.";
    return;
  }
  $("timeline").className = "timeline-list";
  $("timeline").innerHTML = visibleEvents.map((event) => `
    <article class="event verified">
      <div class="event-head"><span><b class="operator-glyph" style="color:${operatorColor(event.color)}">${operatorGlyph(event.icon)}</b> ${escapeHtml(event.callsign)} · ${escapeHtml(intelEventType(event))}</span><div class="event-head-actions"><time>${timeLabel(event.network.received_at)}</time>${ownMessageRemoveButton(event)}</div></div>
      <div class="event-body">${eventDescription(event)}</div>
      <div class="event-meta">✓ SIGNATURE · ${shortHash(event.network.sender_hash)}<br>PKT ${shortHash(event.network.packet_hash || "", 8)} · ${escapeHtml(event.network.interface || "Reticulum")}</div>
    </article>`).join("");
}

function currentLocations(events) {
  const positions = new Map();
  const profiles = new Map();
  const markers = [];
  for (const event of events) {
    const identity = event.network?.sender_hash || event.callsign;
    if (!profiles.has(identity)) profiles.set(identity, {callsign: event.callsign, icon: event.icon || "dot", color: event.color || "moss"});
    if (!Number.isFinite(event.lat) || !Number.isFinite(event.lon)) continue;
    if (event.type === "position.updated") {
      if (!positions.has(identity)) positions.set(identity, []);
      positions.get(identity).push(event);
    } else if (event.type === "marker.created") {
      markers.push(event);
    }
  }
  const current = [];
  positions.forEach((operatorPositions, identity) => {
    const series = displayPositionSeries(operatorPositions);
    if (!series.length) return;
    current.push({...series[series.length - 1], ...profiles.get(identity)});
  });
  return [...current, ...markers];
}

function fieldMapEvents(events) {
  if (!localMapPosition || !localIdentityHash) return events;
  return [
    ...events.filter((event) => !(
      event.type === "position.updated"
      && event.network?.sender_hash === localIdentityHash
    )),
    localMapPosition,
  ];
}

function updateLocalMapPosition(position) {
  const lat = Number(position?.coords?.latitude);
  const lon = Number(position?.coords?.longitude);
  const accuracy = Number(position?.coords?.accuracy);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
  const now = Math.floor(Date.now() / 1000);
  localMapPosition = {
    id: "local-device-position",
    type: "position.updated",
    callsign: currentUser?.callsign || "THIS DEVICE",
    icon: currentUser?.icon || "dot",
    color: currentUser?.color || "moss",
    created_at: now,
    lat,
    lon,
    ...(Number.isFinite(accuracy) ? {accuracy} : {}),
    network: {
      verified: false,
      local: true,
      sender_hash: localIdentityHash,
      received_at: now,
      interface: "Local device GPS",
    },
  };
  if (fieldMap) {
    void renderFieldMap(fieldEvents, {preserveView: localMapPositionHasCentered});
    localMapPositionHasCentered = true;
  }
}

function syncLocalMapPosition(joined) {
  if (!joined || !navigator.geolocation) {
    if (localMapWatchId !== null) navigator.geolocation?.clearWatch(localMapWatchId);
    localMapWatchId = null;
    localMapPosition = null;
    localMapPositionHasCentered = false;
    return;
  }
  if (localMapWatchId !== null) return;
  localMapWatchId = navigator.geolocation.watchPosition(
    updateLocalMapPosition,
    () => {},
    {enableHighAccuracy: true, maximumAge: 10_000, timeout: 20_000},
  );
}

function movementTracks(events) {
  const byOperator = new Map();
  events.forEach((event) => {
    if (event.type !== "position.updated" || !Number.isFinite(event.lat) || !Number.isFinite(event.lon)) return;
    const identity = event.network?.sender_hash || event.callsign;
    if (!byOperator.has(identity)) byOperator.set(identity, []);
    byOperator.get(identity).push(event);
  });
  const now = Date.now() / 1000;
  const features = [];
  byOperator.forEach((positions, identity) => {
    const series = displayPositionSeries(positions);
    if (!series.length) return;
    const latest = series[series.length - 1];
    const latestTime = latest.network?.received_at || latest.created_at;
    if (now - latestTime > ACTIVE_TRACK_MAX_AGE_SECONDS) return;
    const recent = series.filter((event) => (event.network?.received_at || event.created_at) >= latestTime - TRACK_HISTORY_SECONDS).slice(-TRACK_MAX_POINTS);
    const coordinates = [];
    recent.forEach((event) => {
      const point = [event.lon, event.lat];
      if (!coordinates.length || distanceMeters(coordinates[coordinates.length - 1], point) >= 3) coordinates.push(point);
      else coordinates[coordinates.length - 1] = point;
    });
    const travelled = coordinates.slice(1).reduce((total, point, index) => total + distanceMeters(coordinates[index], point), 0);
    if (coordinates.length < 2 || travelled < 8) return;
    features.push({
      type: "Feature",
      geometry: {type: "LineString", coordinates},
      properties: {id: identity, callsign: latest.callsign, color: operatorColor(latest.color)},
    });
  });
  return {type: "FeatureCollection", features};
}

function movementTrackLayer(id, source) {
  return {
    id,
    type: "line",
    source,
    layout: {"line-cap": "round", "line-join": "round"},
    paint: {
      "line-color": ["coalesce", ["get", "color"], "#82936f"],
      "line-width": ["interpolate", ["linear"], ["zoom"], 6, 2, 15, 4],
      "line-opacity": 0.68,
      "line-dasharray": [2, 1.4],
    },
  };
}

function contactReportText(event) {
  if (event.type === "chat.message") return event.message;
  if (event.type !== "ptt.broadcast") return null;
  const transcript = voiceTranscriptCache.get(event.clip_id);
  return transcript?.status === "ready" ? transcript.text : null;
}

function contactReportFeatures(events) {
  const positions = new Map();
  events.forEach((event) => {
    if (event.type !== "position.updated" || !Number.isFinite(event.lat) || !Number.isFinite(event.lon)) return;
    const identity = event.network?.sender_hash || event.callsign;
    if (!positions.has(identity)) positions.set(identity, []);
    positions.get(identity).push(event);
  });
  positions.forEach((items, identity) => positions.set(identity, displayPositionSeries(items)));
  const features = [];
  const reports = activeAutomaticReports(events
    .filter((event) => ["chat.message", "ptt.broadcast"].includes(event.type))
    .map((event) => ({
      id: event.id,
      callsign: event.callsign,
      senderHash: event.network?.sender_hash || event.callsign,
      createdAt: event.created_at,
      message: contactReportText(event),
      event,
    })));
  reports.forEach((item) => {
    const {event, report, message: reportMessage} = item;
    const identity = event.network?.sender_hash || event.callsign;
    const candidates = positions.get(identity) || [];
    const originEvent = [...candidates].reverse().find((position) => position.created_at <= event.created_at + 5);
    if (!originEvent || event.created_at - originEvent.created_at > AUTOMATIC_REPORT_POSITION_MAX_AGE_SECONDS) return;
    const origin = [originEvent.lon, originEvent.lat];
    features.push(...buildAutomaticReportFeatures(report, origin, {
      id: event.id,
      callsign: event.callsign,
      senderHash: identity,
      createdAt: event.created_at,
      message: reportMessage,
    }));
  });
  return {type: "FeatureCollection", features};
}

function contactRouteLayer(id, source) {
  return {
    id, type: "line", source,
    filter: ["==", ["get", "kind"], "route"],
    layout: {"line-cap": "round", "line-join": "round"},
    paint: {"line-color": "#c85f4f", "line-width": 4, "line-opacity": 0.92},
  };
}

function contactPointLayer(id, source) {
  return {
    id, type: "symbol", source,
    filter: ["==", ["get", "kind"], "contact"],
    layout: {
      "icon-image": "reticom-enemy", "icon-size": 1,
      "icon-allow-overlap": true, "icon-ignore-placement": true,
      "text-field": ["get", "label"], "text-size": 11,
      "text-font": ["Noto Sans Regular"],
      "text-offset": [0, 2.15], "text-anchor": "top",
    },
    paint: {"text-color": "#dc796b", "text-halo-color": "#090c09", "text-halo-width": 2},
  };
}

function automaticReportAreaLayer(id, source) {
  return {
    id, type: "fill", source,
    filter: ["==", ["get", "kind"], "auto-area"],
    paint: {
      "fill-color": ["coalesce", ["get", "color"], "#c46855"],
      "fill-opacity": ["match", ["get", "areaStyle"], "uncertain", 0.12, "search", 0.08, 0.16],
    },
  };
}

function automaticReportAreaOutlineLayer(id, source) {
  return {
    id, type: "line", source,
    filter: ["==", ["get", "kind"], "auto-area"],
    layout: {"line-cap": "round", "line-join": "round"},
    paint: {
      "line-color": ["coalesce", ["get", "color"], "#c46855"],
      "line-width": 2,
      "line-opacity": 0.82,
      "line-dasharray": [2, 1.5],
    },
  };
}

function automaticReportLineLayer(id, source) {
  return {
    id, type: "line", source,
    filter: ["==", ["get", "kind"], "auto-line"],
    layout: {"line-cap": "round", "line-join": "round"},
    paint: {
      "line-color": ["coalesce", ["get", "color"], "#c46855"],
      "line-width": ["match", ["get", "lineStyle"], "blocked", 5, "phase", 4, 3],
      "line-opacity": 0.9,
      "line-dasharray": [2, 1.25],
    },
  };
}

function automaticReportPointLayer(id, source) {
  return {
    id, type: "circle", source,
    filter: ["==", ["get", "kind"], "auto-point"],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 10, 15, 15],
      "circle-color": "#090c09",
      "circle-stroke-width": 2.5,
      "circle-stroke-color": ["coalesce", ["get", "color"], "#c46855"],
      "circle-opacity": ["coalesce", ["get", "fadeOpacity"], 0.94],
    },
  };
}

function automaticReportLabelLayer(id, source) {
  return {
    id, type: "symbol", source,
    filter: ["==", ["get", "kind"], "auto-point"],
    layout: {
      "text-field": ["get", "symbol"],
      "text-font": ["Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 5, 8, 15, 10],
      "text-letter-spacing": -0.04,
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": ["coalesce", ["get", "color"], "#c46855"],
      "text-opacity": ["coalesce", ["get", "fadeOpacity"], 0.94],
    },
  };
}

function automaticReportCaptionLayer(id, source) {
  return {
    id, type: "symbol", source,
    filter: ["==", ["get", "kind"], "auto-point"],
    minzoom: 10,
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 10,
      "text-offset": [0, 1.9],
      "text-anchor": "top",
      "text-allow-overlap": false,
    },
    paint: {
      "text-color": "#d6d4c5",
      "text-opacity": ["coalesce", ["get", "fadeOpacity"], 0.94],
      "text-halo-color": "#090c09",
      "text-halo-width": 2,
    },
  };
}

function emptyFeatureCollection() {
  return {type: "FeatureCollection", features: []};
}

function addMgrsGridLayers(map, prefix) {
  const source = `${prefix}-mgrs-grid`;
  const labels = `${prefix}-mgrs-labels`;
  map.addSource(source, {type: "geojson", data: emptyFeatureCollection()});
  map.addSource(labels, {type: "geojson", data: emptyFeatureCollection()});
  map.addLayer({
    id: source,
    type: "line",
    source,
    layout: {visibility: mapGridEnabled ? "visible" : "none"},
    paint: {
      "line-color": "#8f8b69",
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.55, 15, 1.15],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 2, 0.34, 15, 0.58],
      "line-dasharray": [2, 2],
    },
  });
  map.addLayer({
    id: labels,
    type: "symbol",
    source: labels,
    layout: {
      visibility: mapGridEnabled ? "visible" : "none",
      "text-field": ["get", "label"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 3, 9, 15, 11],
      "text-font": ["Noto Sans Regular"],
      "text-allow-overlap": false,
      "text-ignore-placement": false,
    },
    paint: {"text-color": "#aaa681", "text-halo-color": "#090c09", "text-halo-width": 1.4},
  });
}

function formatMgrsReference(reference) {
  const compact = String(reference || "").replaceAll(" ", "").toUpperCase();
  const match = compact.match(/^(\d{1,2}[C-HJ-NP-X]|[ABYZ])([A-HJ-NP-Z]{2})(\d*)$/);
  if (!match) return compact;
  const zone = /^\d/.test(match[1]) ? match[1].replace(/^\d+/, (digits) => digits.padStart(2, "0")) : match[1];
  if (!match[3]) return `${zone} ${match[2]}`;
  const half = match[3].length / 2;
  return `${zone} ${match[2]} ${match[3].slice(0, half)} ${match[3].slice(half)}`;
}

function gridDefinitionForZoom(zoom) {
  if (zoom >= 15) return {meters: 100, precision: 3};
  if (zoom >= 12) return {meters: 1000, precision: 2};
  if (zoom >= 9) return {meters: 10000, precision: 1};
  return {meters: 100000, precision: 0};
}

async function mgrsLibrary() {
  if (!mgrsLibraryPromise) mgrsLibraryPromise = import(MGRS_URL).then((module) => module.default);
  return mgrsLibraryPromise;
}

function mgrsCenterPrecision(zoom) {
  if (zoom >= 16) return 5;
  if (zoom >= 14) return 4;
  if (zoom >= 11) return 3;
  if (zoom >= 8) return 2;
  return 1;
}

function geographicZoneGrid(map, mgrs) {
  const bounds = map.getBounds();
  const west = Math.max(-180, bounds.getWest());
  const east = Math.min(180, bounds.getEast());
  const south = Math.max(-90, bounds.getSouth());
  const north = Math.min(90, bounds.getNorth());
  const lines = [];
  const labels = [];
  for (let longitude = Math.ceil(west / 6) * 6; longitude <= east; longitude += 6) {
    lines.push({type: "Feature", geometry: {type: "LineString", coordinates: [[longitude, south], [longitude, north]]}, properties: {}});
  }
  const bandEdges = [-80, -72, -64, -56, -48, -40, -32, -24, -16, -8, 0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 84];
  bandEdges.filter((latitude) => latitude >= south && latitude <= north).forEach((latitude) => {
    lines.push({type: "Feature", geometry: {type: "LineString", coordinates: [[west, latitude], [east, latitude]]}, properties: {}});
  });
  if (map.getZoom() >= 2.5) {
    const firstZone = Math.max(1, Math.floor((west + 180) / 6) + 1);
    const lastZone = Math.min(60, Math.floor((east + 180) / 6) + 1);
    for (let zone = firstZone; zone <= lastZone && labels.length < 70; zone += 1) {
      const longitude = -180 + (zone - 0.5) * 6;
      for (let latitude = Math.max(-76, Math.ceil((south + 76) / 8) * 8 - 76); latitude <= Math.min(80, north); latitude += 8) {
        try {
          const reference = formatMgrsReference(mgrs.forward([longitude, latitude], 0));
          labels.push({type: "Feature", geometry: {type: "Point", coordinates: [longitude, latitude]}, properties: {label: reference.split(" ")[0]}});
        } catch {
          // Polar UPS references remain available in the center readout.
        }
      }
    }
  }
  return {lines: {type: "FeatureCollection", features: lines}, labels: {type: "FeatureCollection", features: labels}};
}

function projectedMgrsGrid(map, mgrs) {
  const bounds = map.getBounds();
  const center = map.getCenter();
  const projection = mgrs.UTMUPS.forward(center.lat, center.lng, false, false);
  const forcedZone = projection.zone || false;
  const corners = [
    [bounds.getWest(), bounds.getSouth()], [bounds.getWest(), bounds.getNorth()],
    [bounds.getEast(), bounds.getSouth()], [bounds.getEast(), bounds.getNorth()],
  ].map(([longitude, latitude]) => mgrs.UTMUPS.forward(latitude, longitude, forcedZone, false));
  let minX = Math.min(...corners.map((point) => point.x));
  let maxX = Math.max(...corners.map((point) => point.x));
  let minY = Math.min(...corners.map((point) => point.y));
  let maxY = Math.max(...corners.map((point) => point.y));
  let {meters, precision} = gridDefinitionForZoom(map.getZoom());
  while ((maxX - minX + maxY - minY) / meters > 90) {
    meters *= 10;
    precision = Math.max(0, precision - 1);
  }
  minX = Math.floor(minX / meters) * meters;
  maxX = Math.ceil(maxX / meters) * meters;
  minY = Math.floor(minY / meters) * meters;
  maxY = Math.ceil(maxY / meters) * meters;
  const lines = [];
  const labels = [];
  const reverse = (x, y) => {
    const point = mgrs.UTMUPS.reverse(projection.zone, projection.northp, x, y, false);
    return [point.lon, point.lat];
  };
  const sampledLine = (fixed, start, end, vertical) => {
    const coordinates = [];
    for (let index = 0; index <= 12; index += 1) {
      const variable = start + (end - start) * index / 12;
      try { coordinates.push(vertical ? reverse(fixed, variable) : reverse(variable, fixed)); } catch { /* Outside this projection. */ }
    }
    return coordinates;
  };
  for (let x = minX; x <= maxX; x += meters) {
    const coordinates = sampledLine(x, minY, maxY, true);
    if (coordinates.length > 1) lines.push({type: "Feature", geometry: {type: "LineString", coordinates}, properties: {}});
  }
  for (let y = minY; y <= maxY; y += meters) {
    const coordinates = sampledLine(y, minX, maxX, false);
    if (coordinates.length > 1) lines.push({type: "Feature", geometry: {type: "LineString", coordinates}, properties: {}});
  }
  for (let x = minX; x < maxX && labels.length < 36; x += meters) {
    for (let y = minY; y < maxY && labels.length < 36; y += meters) {
      try {
        const point = reverse(x + meters / 2, y + meters / 2);
        if (point[0] < bounds.getWest() || point[0] > bounds.getEast() || point[1] < bounds.getSouth() || point[1] > bounds.getNorth()) continue;
        labels.push({type: "Feature", geometry: {type: "Point", coordinates: point}, properties: {label: formatMgrsReference(mgrs.forward(point, precision))}});
      } catch {
        // A neighbouring projection zone will replace invalid edge cells after panning.
      }
    }
  }
  return {lines: {type: "FeatureCollection", features: lines}, labels: {type: "FeatureCollection", features: labels}};
}

async function refreshMgrsGrid(map, prefix, referenceId) {
  if (!map.getSource(`${prefix}-mgrs-grid`)) return;
  const reference = $(referenceId);
  try {
    const mgrs = await mgrsLibrary();
    const center = map.getCenter();
    const centerReference = mgrs.forward([center.lng, center.lat], mgrsCenterPrecision(map.getZoom()));
    reference.textContent = `MGRS ${formatMgrsReference(centerReference)}`;
    reference.dataset.reference = centerReference;
    reference.title = "Copy MGRS reference at map centre";
    if (!mapGridEnabled) return;
    const bounds = map.getBounds();
    const wideView = map.getZoom() < 6 || bounds.getEast() - bounds.getWest() > 18;
    const data = wideView ? geographicZoneGrid(map, mgrs) : projectedMgrsGrid(map, mgrs);
    map.getSource(`${prefix}-mgrs-grid`)?.setData(data.lines);
    map.getSource(`${prefix}-mgrs-labels`)?.setData(data.labels);
  } catch {
    reference.textContent = "MGRS UNAVAILABLE";
    reference.removeAttribute("data-reference");
  }
}

function installMgrsGrid(map, prefix, referenceId) {
  let timer = null;
  const update = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => refreshMgrsGrid(map, prefix, referenceId), 80);
  };
  map.on("moveend", update);
  map.on("zoomend", update);
  map._reticomGridUpdate = update;
  update();
}

function setMgrsGridVisibility(map, prefix) {
  [`${prefix}-mgrs-grid`, `${prefix}-mgrs-labels`].forEach((layer) => {
    if (map.getLayer(layer)) map.setLayoutProperty(layer, "visibility", mapGridEnabled ? "visible" : "none");
  });
  if (mapGridEnabled) map._reticomGridUpdate?.();
}

function toggleMapGrid() {
  saveMapGridPreference(!mapGridEnabled);
  updateMapGridUi();
  if (operationalMap) setMgrsGridVisibility(operationalMap, "verified");
  if (fieldMap) setMgrsGridVisibility(fieldMap, "field");
  toast(mapGridEnabled ? "MGRS grid active" : "MGRS grid hidden");
}

async function copyGridReference(button) {
  const reference = button.dataset.reference;
  if (!reference) return;
  try {
    await navigator.clipboard.writeText(reference);
    toast(`${formatMgrsReference(reference)} copied`);
  } catch {
    toast("Could not copy MGRS reference", true);
  }
}

function addOperationalMapLayers(map) {
  addUnitMarkerImages(map);
  addMgrsGridLayers(map, "verified");
  map.addSource("verified-events", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("verified-tracks", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("verified-contacts", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("verified-drawings", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("verified-draw-preview", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addLayer(automaticReportAreaLayer("verified-automatic-areas", "verified-contacts"));
  map.addLayer(automaticReportAreaOutlineLayer("verified-automatic-area-outlines", "verified-contacts"));
  map.addLayer(movementTrackLayer("verified-tracks", "verified-tracks"));
  map.addLayer({
    id: "verified-drawings",
    type: "line",
    source: "verified-drawings",
    paint: {
      "line-color": ["coalesce", ["get", "color"], "#a48d61"],
      "line-width": 3,
      "line-opacity": 0.9,
    },
    layout: {"line-cap": "round", "line-join": "round"},
  });
  map.addLayer(contactRouteLayer("verified-contact-routes", "verified-contacts"));
  map.addLayer(automaticReportLineLayer("verified-automatic-lines", "verified-contacts"));
  map.addLayer(unitMarkerLayer("verified-unit-symbols", "verified-events"));
  map.addLayer(textMarkerLayer("verified-map-text", "verified-events"));
  map.addLayer(tacticalMarkerPointLayer("verified-tactical-points", "verified-events"));
  map.addLayer(tacticalMarkerLabelLayer("verified-tactical-symbols", "verified-events"));
  map.addLayer(tacticalMarkerCaptionLayer("verified-tactical-captions", "verified-events"));
  map.addLayer(contactPointLayer("verified-contact-points", "verified-contacts"));
  map.addLayer({
    id: "verified-draw-preview",
    type: "line",
    source: "verified-draw-preview",
    paint: {"line-color": "#a48d61", "line-width": 4, "line-opacity": 0.95, "line-dasharray": [1, 1]},
    layout: {"line-cap": "round", "line-join": "round"},
  });
  map.addLayer({
    id: "verified-points",
    type: "circle",
    source: "verified-events",
    paint: {
      "circle-radius": ["case", ["==", ["get", "kind"], "operator"], 7, 6],
      "circle-color": ["case", ["==", ["get", "kind"], "operator"], ["coalesce", ["get", "color"], "#82936f"], "#a48d61"],
      "circle-stroke-width": 2,
      "circle-stroke-color": "#111711",
    },
    filter: ["!", ["in", ["get", "markerType"], ["literal", SYMBOL_MARKER_TYPES]]],
  });
  map.addLayer({
    id: "verified-labels",
    type: "symbol",
    source: "verified-events",
    layout: {
      "text-field": ["get", "label"],
      "text-size": 12,
      "text-font": ["Noto Sans Regular"],
      "text-offset": [0, 1.7],
      "text-anchor": "top",
      "text-allow-overlap": false,
    },
    filter: ["!", ["in", ["get", "markerType"], ["literal", SYMBOL_MARKER_TYPES]]],
    paint: {"text-color": "#d1d0bf", "text-halo-color": "#111711", "text-halo-width": 1.5},
  });
  map.addLayer(automaticReportPointLayer("verified-automatic-points", "verified-contacts"));
  map.addLayer(automaticReportLabelLayer("verified-automatic-symbols", "verified-contacts"));
  map.addLayer(automaticReportCaptionLayer("verified-automatic-captions", "verified-contacts"));
}

function addFieldMapLayers(map) {
  addUnitMarkerImages(map);
  addMgrsGridLayers(map, "field");
  map.addSource("field-events", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("field-tracks", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("field-contacts", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("field-drawings", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("field-draw-preview", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("field-navigation", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addSource("field-bearing", {type: "geojson", data: {type: "FeatureCollection", features: []}});
  map.addLayer(automaticReportAreaLayer("field-automatic-areas", "field-contacts"));
  map.addLayer(automaticReportAreaOutlineLayer("field-automatic-area-outlines", "field-contacts"));
  map.addLayer({
    id: "field-bearing-aura",
    type: "line",
    source: "field-bearing",
    layout: {"line-cap": "butt"},
    paint: {
      "line-color": "#c6c39b",
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 4, 16, 8],
      "line-opacity": 0.08,
      "line-blur": 3,
    },
  });
  map.addLayer({
    id: "field-bearing-ray",
    type: "line",
    source: "field-bearing",
    layout: {"line-cap": "butt"},
    paint: {
      "line-color": "#d2cfaa",
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1, 16, 1.6],
      "line-opacity": 0.42,
    },
  });
  map.addLayer(movementTrackLayer("field-tracks", "field-tracks"));
  map.addLayer({
    id: "field-navigation-route-casing",
    type: "line",
    source: "field-navigation",
    filter: ["==", ["get", "mode"], "route"],
    paint: {"line-color": "#111711", "line-width": 7, "line-opacity": 0.88},
    layout: {"line-cap": "round", "line-join": "round"},
  });
  map.addLayer({
    id: "field-navigation-route",
    type: "line",
    source: "field-navigation",
    filter: ["==", ["get", "mode"], "route"],
    paint: {"line-color": "#b4aa73", "line-width": 4, "line-opacity": 0.98},
    layout: {"line-cap": "round", "line-join": "round"},
  });
  map.addLayer({
    id: "field-navigation",
    type: "line",
    source: "field-navigation",
    filter: ["!=", ["get", "mode"], "route"],
    paint: {"line-color": "#b09a62", "line-width": 3, "line-opacity": 0.95, "line-dasharray": [2, 1.5]},
    layout: {"line-cap": "round"},
  });
  map.addLayer({
    id: "field-drawings",
    type: "line",
    source: "field-drawings",
    paint: {"line-color": ["coalesce", ["get", "color"], "#a48d61"], "line-width": 3, "line-opacity": 0.9},
    layout: {"line-cap": "round", "line-join": "round"},
  });
  map.addLayer(contactRouteLayer("field-contact-routes", "field-contacts"));
  map.addLayer(automaticReportLineLayer("field-automatic-lines", "field-contacts"));
  map.addLayer(unitMarkerLayer("field-unit-symbols", "field-events"));
  map.addLayer(textMarkerLayer("field-map-text", "field-events"));
  map.addLayer(tacticalMarkerPointLayer("field-tactical-points", "field-events"));
  map.addLayer(tacticalMarkerLabelLayer("field-tactical-symbols", "field-events"));
  map.addLayer(tacticalMarkerCaptionLayer("field-tactical-captions", "field-events"));
  map.addLayer(contactPointLayer("field-contact-points", "field-contacts"));
  map.addLayer({
    id: "field-draw-preview",
    type: "line",
    source: "field-draw-preview",
    paint: {"line-color": "#a48d61", "line-width": 4, "line-opacity": 0.95, "line-dasharray": [1, 1]},
    layout: {"line-cap": "round", "line-join": "round"},
  });
  map.addLayer({
    id: "field-points",
    type: "circle",
    source: "field-events",
    paint: {
      "circle-radius": ["case", ["==", ["get", "kind"], "operator"], 8, 6],
      "circle-color": ["case", ["==", ["get", "kind"], "operator"], ["coalesce", ["get", "color"], "#82936f"], "#a48d61"],
      "circle-stroke-width": 2,
      "circle-stroke-color": "#090c09",
    },
    filter: ["all",
      ["!", ["in", ["get", "markerType"], ["literal", SYMBOL_MARKER_TYPES]]],
      ["!=", ["get", "markerType"], "waypoint"],
    ],
  });
  map.addLayer({
    id: "field-labels",
    type: "symbol",
    source: "field-events",
    filter: ["!", ["in", ["get", "markerType"], ["literal", SYMBOL_MARKER_TYPES]]],
    layout: {"text-field": ["get", "label"], "text-size": 12, "text-font": ["Noto Sans Regular"], "text-offset": [0, 1.7], "text-anchor": "top"},
    paint: {"text-color": "#d1d0bf", "text-halo-color": "#090c09", "text-halo-width": 1.5},
  });
  map.addLayer(automaticReportPointLayer("field-automatic-points", "field-contacts"));
  map.addLayer(automaticReportLabelLayer("field-automatic-symbols", "field-contacts"));
  map.addLayer(automaticReportCaptionLayer("field-automatic-captions", "field-contacts"));
}

function replaceMapStyle(map, addLayers) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => finish(new Error("Map style timed out")), 15000);
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve();
    };
    map.once("style.load", () => {
      try {
        addLayers(map);
        finish();
      } catch (error) {
        finish(error);
      }
    });
    try {
      map.setStyle(mapStyleDefinition(), {diff: false});
    } catch (error) {
      finish(error);
    }
  });
}

async function toggleMapStyle() {
  if (mapStyleSwitching) return;
  mapStyleSwitching = true;
  const operationalCamera = operationalMap ? captureMapCamera(operationalMap) : null;
  const fieldCamera = fieldMap ? captureMapCamera(fieldMap) : null;
  const buttons = [$("commandMapStyleToggle"), $("fieldMapStyleToggle")].filter(Boolean);
  buttons.forEach((button) => { button.disabled = true; });
  mapStyleMode = mapStyleMode === "dark" ? "satellite" : "dark";
  saveMapStylePreference(mapStyleMode);
  updateMapStyleUi();
  try {
    const replacements = [];
    if (operationalMap) replacements.push(replaceMapStyle(operationalMap, addOperationalMapLayers));
    if (fieldMap) replacements.push(replaceMapStyle(fieldMap, addFieldMapLayers));
    await Promise.all(replacements);
    lastFieldMapSignature = "";
    if (operationalMap) await renderMap(commandEvents, {preserveView: true});
    if (fieldMap) await renderFieldMap(fieldEvents, {preserveView: true});
    restoreMapCamera(operationalMap, operationalCamera);
    restoreMapCamera(fieldMap, fieldCamera);
    operationalMap?._reticomGridUpdate?.();
    fieldMap?._reticomGridUpdate?.();
    updateDrawPreview(commandMapEditor);
    updateDrawPreview(fieldMapEditor);
    toast(mapStyleMode === "satellite" ? "Satellite map active" : "Dark map active");
  } catch {
    toast("Map style could not be loaded", true);
  } finally {
    mapStyleSwitching = false;
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function ensureMap() {
  if (operationalMap) return operationalMap;
  if (mapReady) return mapReady;
  mapReady = (async () => {
    const {Map: LibreMap, NavigationControl, Popup} = await import(MAPLIBRE_URL);
    const map = new LibreMap({
      container: "mapCanvas",
      style: mapStyleDefinition(),
      center: [0, 18],
      zoom: 1.25,
      pitchWithRotate: false,
      dragRotate: false,
      attributionControl: false,
    });
    map.addControl(new NavigationControl({showCompass: false}), "top-right");
    await new Promise((resolve, reject) => {
      map.once("load", resolve);
      map.once("error", (event) => reject(event.error || new Error("Basemap failed to load")));
    });
    addOperationalMapLayers(map);
    operationalMap = map;
    map.on("moveend", updateOfflineMapEstimate);
    installMgrsGrid(map, "verified", "commandGridReference");
    commandMapEditor = createMapEditor(map, {
      menuId: "commandMapRadialMenu",
      contextMenuId: "commandMapContextMenu",
      paletteId: "commandMapMarkerPalette",
      controlsId: "commandMapDrawControls",
      textComposerId: "commandMapTextComposer",
      previewSource: "verified-draw-preview",
      scope: "command",
    });
    installMapFeatureInteractions(map, Popup, ["verified-points", "verified-unit-symbols", "verified-map-text", "verified-tactical-points", "verified-tactical-symbols", "verified-contact-points", "verified-automatic-points", "verified-automatic-areas", "verified-automatic-lines"], "verified-drawings");
    installMapEditorGestures(commandMapEditor);
    return map;
  })();
  return mapReady;
}

async function renderMap(events, {preserveView = false} = {}) {
  const locations = currentLocations(events);
  const drawings = events.filter((event) => event.type === "drawing.created");
  const contacts = contactReportFeatures(events);
  const verifiedCount = locations.length + drawings.length;
  $("mapEmpty").classList.toggle("hidden", verifiedCount > 0);
  $("mapBounds").textContent = verifiedCount ? `${verifiedCount} VERIFIED` : "WORLD";
  try {
    const map = await ensureMap();
    const features = locations.map((event) => {
      const marker = tacticalMarker(event.marker_type);
      return {
        type: "Feature",
        geometry: {type: "Point", coordinates: [event.lon, event.lat]},
        properties: {
        id: event.id,
        kind: event.type === "position.updated" ? "operator" : "marker",
        label: event.type === "position.updated" ? `${operatorGlyph(event.icon)} ${event.callsign}` : event.display_label || event.label,
        color: operatorColor(event.color),
        mapKind: event.type === "marker.created" ? "marker" : "operator",
        markerType: event.marker_type || "",
        symbol: marker?.symbol || "",
        markerColor: marker?.color || "",
        senderHash: event.network?.sender_hash || "",
        callsign: event.callsign || "",
        removable: event.type === "marker.created",
        },
      };
    });
    map.getSource("verified-events").setData({type: "FeatureCollection", features});
    map.getSource("verified-tracks").setData(movementTracks(events));
    map.getSource("verified-contacts").setData(contacts);
    map.getSource("verified-drawings").setData({
      type: "FeatureCollection",
      features: drawings.map((event) => ({
        type: "Feature",
        geometry: drawingGeometry(event),
        properties: {
          id: event.id,
          color: operatorColor(event.color),
          kind: "drawing",
          mapKind: "drawing",
          drawingType: event.drawing_type || "trace",
          label: event.drawing_type === "arrow" ? "DIRECTION ARROW" : "FREEHAND TRACE",
          removable: true,
        },
      })),
    });
    const coordinates = [
      ...locations.map((event) => [event.lon, event.lat]),
      ...drawings.flatMap((event) => event.points),
      ...contacts.features.filter((feature) => feature.geometry.type === "Point").map((feature) => feature.geometry.coordinates),
    ];
    if (!preserveView && shouldInitiallyFrameMap(commandMapHasFramedData, coordinates.length)) {
      commandMapHasFramedData = true;
      if (commandMapEditor?.drawMode) return;
      const lons = coordinates.map((point) => point[0]);
      const lats = coordinates.map((point) => point[1]);
      const bounds = [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]];
      map.fitBounds(bounds, {padding: 72, maxZoom: 15, duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 500});
    }
  } catch (error) {
    $("mapEmpty").classList.remove("hidden");
    $("mapEmpty").querySelector("b").textContent = "BASEMAP OFFLINE";
    $("mapEmpty").querySelector("span").textContent = "Reticulum remains active. Reconnect to load geographic tiles; offline map packages are not installed yet.";
  }
}

async function ensureFieldMap() {
  if (fieldMap) return fieldMap;
  if (fieldMapReady) return fieldMapReady;
  fieldMapReady = (async () => {
    const {Map: LibreMap, Marker, NavigationControl, Popup} = await import(MAPLIBRE_URL);
    fieldMapMarkerClass = Marker;
    fieldMapPopupClass = Popup;
    const createMap = (style) => new LibreMap({
        container: "fieldMapCanvas",
        style,
        center: [4.3, 51.5],
        zoom: 9,
        pitchWithRotate: false,
        dragRotate: false,
        attributionControl: false,
      });
    const waitForMap = (candidate) => new Promise((resolve, reject) => {
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        candidate.off("style.load", onStyleLoad);
        candidate.off("error", onError);
        callback(value);
      };
      const onStyleLoad = () => finish(resolve, candidate);
      const onError = (event) => finish(reject, event.error || new Error("Basemap failed to load"));
      const timeout = setTimeout(
        () => finish(reject, new Error("Basemap startup timed out")),
        MAP_START_TIMEOUT_MS,
      );
      candidate.on("style.load", onStyleLoad);
      candidate.on("error", onError);
    });
    let map = createMap(mapStyleDefinition());
    try {
      await waitForMap(map);
    } catch (error) {
      map.remove();
      if (mapStyleMode !== "dark") throw error;
      map = createMap(mapStyleDefinition("satellite"));
      await waitForMap(map);
      mapStyleMode = "satellite";
      saveMapStylePreference(mapStyleMode);
      updateMapStyleUi();
    }
    map.addControl(new NavigationControl({showCompass: false}), "top-right");
    addFieldMapLayers(map);
    fieldMap = map;
    map.on("moveend", updateOfflineMapEstimate);
    map.on("move", scheduleOwnBearingLine);
    map.on("resize", scheduleOwnBearingLine);
    fieldMapOnline = true;
    installMgrsGrid(map, "field", "fieldGridReference");
    fieldMapEditor = createMapEditor(map, {
      menuId: "mapRadialMenu",
      contextMenuId: "mapContextMenu",
      paletteId: "mapMarkerPalette",
      controlsId: "mapDrawControls",
      textComposerId: "mapTextComposer",
      previewSource: "field-draw-preview",
      scope: "field",
    });
    installMapFeatureInteractions(map, Popup, ["field-points", "field-unit-symbols", "field-map-text", "field-tactical-points", "field-tactical-symbols", "field-contact-points", "field-automatic-points", "field-automatic-areas", "field-automatic-lines"], "field-drawings");
    installMapEditorGestures(fieldMapEditor);
    return map;
  })();
  return fieldMapReady;
}

function syncFieldWaypointMarkers(map, locations) {
  if (!fieldMapMarkerClass || !fieldMapPopupClass) return;
  const waypoints = locations.filter((event) => event.type === "marker.created" && event.marker_type === "waypoint");
  const activeIds = new Set(waypoints.map((event) => event.id));
  fieldWaypointMarkers.forEach((entry, id) => {
    if (activeIds.has(id)) return;
    entry.marker.remove();
    fieldWaypointMarkers.delete(id);
  });
  waypoints.forEach((event) => {
    const label = event.display_label || event.label;
    let entry = fieldWaypointMarkers.get(event.id);
    if (!entry) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "field-waypoint-marker";
      const caption = document.createElement("span");
      caption.textContent = label;
      button.append(caption);
      button.addEventListener("click", (clickEvent) => {
        clickEvent.stopPropagation();
        showMapFeaturePopup(map, fieldMapPopupClass, {
          type: "Feature",
          geometry: {type: "Point", coordinates: [event.lon, event.lat]},
          properties: {
            id: event.id,
            kind: "marker",
            mapKind: "marker",
            markerType: "waypoint",
            label,
            removable: currentTeamAdmin || event.network?.sender_hash === localIdentityHash,
          },
        }, [event.lon, event.lat]);
      });
      entry = {button, caption, marker: new fieldMapMarkerClass({element: button, anchor: "center"}).setLngLat([event.lon, event.lat]).addTo(map)};
      fieldWaypointMarkers.set(event.id, entry);
    }
    entry.button.setAttribute("aria-label", `Navigate to ${label}`);
    entry.caption.textContent = label;
    entry.marker.setLngLat([event.lon, event.lat]);
  });
}

async function renderFieldMap(events, {preserveView = false} = {}) {
  try {
    const map = await ensureFieldMap();
    fieldMapOnline = true;
    $("fieldMapFallback").classList.add("hidden");
    const mapEvents = fieldMapEvents(events);
    const locations = currentLocations(mapEvents);
    const ownLocation = locations.find((event) => (
      event.type === "position.updated"
      && event.network?.sender_hash === localIdentityHash
    ));
    fieldBearingOrigin = ownLocation ? [ownLocation.lon, ownLocation.lat] : null;
    scheduleOwnBearingLine();
    const drawings = events.filter((event) => event.type === "drawing.created");
    const contacts = contactReportFeatures(events);
    updateFieldCompassMapTargets(fieldBearingOrigin, locations, contacts);
    syncFieldWaypointMarkers(map, locations);
    const mapSignature = [
      ...locations.map((event) => `${event.id}:${event.lat}:${event.lon}:${event.display_label || event.label || event.callsign}`),
      ...drawings.map((event) => `${event.id}:${event.points.length}:${event.points[0]?.join(":")}:${event.points[event.points.length - 1]?.join(":")}`),
      ...contacts.features.map((feature) => `${feature.properties.id}:${feature.properties.kind}:${feature.geometry.coordinates.flat().join(":")}`),
    ].join("|");
    const mapDataChanged = mapSignature !== lastFieldMapSignature;
    const nextMarkerIds = new Set([
      ...mapMarkerIds(locations),
      ...contacts.features.filter((feature) => feature.geometry.type === "Point").map((feature) => `auto:${feature.properties.id}`),
    ]);
    const markerWasAdded = hasAddedMapMarker(fieldMapMarkerIds, nextMarkerIds, fieldMapHasRendered);
    if (mapDataChanged) {
      map.getSource("field-tracks").setData(movementTracks(events));
      map.getSource("field-contacts").setData(contacts);
      map.getSource("field-events").setData({
        type: "FeatureCollection",
        features: locations.map((event) => {
          const marker = tacticalMarker(event.marker_type);
          return {
            type: "Feature",
            geometry: {type: "Point", coordinates: [event.lon, event.lat]},
            properties: {
            kind: event.type === "position.updated" ? "operator" : "marker",
            label: event.type === "position.updated" ? `${operatorGlyph(event.icon)} ${event.callsign}` : event.display_label || event.label,
            color: operatorColor(event.color),
            id: event.id,
            mapKind: event.type === "marker.created" ? "marker" : "operator",
            markerType: event.marker_type || "",
            symbol: marker?.symbol || "",
            markerColor: marker?.color || "",
            senderHash: event.network?.sender_hash || "",
            callsign: event.callsign || "",
            removable: event.type === "marker.created" && (currentTeamAdmin || event.network?.sender_hash === localIdentityHash),
            queued: event.network?.queued === true,
            },
          };
        }),
      });
      map.getSource("field-drawings").setData({
        type: "FeatureCollection",
        features: drawings.map((event) => ({
          type: "Feature",
          geometry: drawingGeometry(event),
          properties: {
            id: event.id,
            color: operatorColor(event.color),
            kind: "drawing",
            mapKind: "drawing",
            drawingType: event.drawing_type || "trace",
            label: event.drawing_type === "arrow" ? "DIRECTION ARROW" : "FREEHAND TRACE",
            removable: currentTeamAdmin || event.network?.sender_hash === localIdentityHash,
          },
        })),
      });
      lastFieldMapSignature = mapSignature;
    }
    if (waypointNavigation?.targetKind === "operator") {
      const latestTarget = latestOperatorMapTarget(locations, waypointNavigation.id);
      if (latestTarget) {
        const targetMoved = distanceMeters(waypointNavigation.target, latestTarget.target) >= 25;
        waypointNavigation.target = latestTarget.target;
        waypointNavigation.label = latestTarget.label;
        $("waypointNavigationLabel").textContent = latestTarget.label;
        if (targetMoved && waypointNavigation.mode === "route") {
          waypointNavigation.routeCoordinates = null;
          waypointNavigation.routeOrigin = null;
          waypointNavigation.routeError = "";
          waypointNavigation.centered = false;
        }
      }
      renderWaypointNavigationLine();
    } else if (waypointNavigation && !events.some((event) => event.type === "marker.created" && event.id === waypointNavigation.id)) {
      stopWaypointNavigation(false);
      toast("Waypoint was removed · navigation ended", true);
    } else {
      renderWaypointNavigationLine();
    }
    const coordinates = [
      ...locations.map((event) => [event.lon, event.lat]),
      ...drawings.flatMap((event) => event.points),
      ...contacts.features.filter((feature) => feature.geometry.type === "Point").map((feature) => feature.geometry.coordinates),
    ];
    if (!preserveView && coordinates.length && mapDataChanged && !markerWasAdded && !fieldMapEditor?.drawMode && !waypointNavigation) {
      const lons = coordinates.map((point) => point[0]);
      const lats = coordinates.map((point) => point[1]);
      map.fitBounds(
        [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
        {padding: 88, maxZoom: 15, duration: 350},
      );
    }
    fieldMapMarkerIds = nextMarkerIds;
    fieldMapHasRendered = true;
    if (localMapPosition) localMapPositionHasCentered = true;
    if (!fieldFeedOnline) $("fieldFeedSync").textContent = fieldQueuedEvents
      ? `LOCAL READY · ${fieldQueuedEvents} QUEUED`
      : "LOCAL READY · TEAM OFFLINE";
    return true;
  } catch {
    fieldMapOnline = false;
    if (!fieldMap) fieldMapReady = null;
    $("fieldMapFallback").classList.remove("hidden");
    $("fieldMapFallback").querySelector("b").textContent = "BASEMAP UNAVAILABLE";
    $("fieldMapFallback").querySelector("span").textContent = "Reticulum remains available. Check the mobile data connection and retry the Map tab.";
    $("fieldFeedSync").textContent = fieldFeedOnline ? "RNS ONLINE · MAP OFFLINE" : "MAP + RNS OFFLINE";
    return false;
  }
}

function sampleDrawing(points, maximum = 48) {
  if (points.length <= maximum) return points;
  return Array.from({length: maximum}, (_, index) => points[Math.round(index * (points.length - 1) / (maximum - 1))]);
}

function createMapEditor(map, {menuId, contextMenuId, paletteId, controlsId, textComposerId, previewSource, scope}) {
  const editor = {
    map,
    menu: $(menuId),
    contextMenu: $(contextMenuId),
    palette: $(paletteId),
    controls: $(controlsId),
    textComposer: $(textComposerId),
    previewSource,
    scope,
    location: null,
    longPressTimer: null,
    longPressStart: null,
    longPressPointerId: null,
    activeTouchPointers: new Set(),
    drawMode: false,
    drawShape: "trace",
    drawing: false,
    points: [],
    lastScreenPoint: null,
    lastPointerType: "mouse",
  };
  editor.sendButton = editor.controls.querySelector("[data-send-map-drawing]");
  editor.drawLabel = editor.controls.querySelector("span");
  renderMapMarkerPalette(editor.palette);
  [editor.menu, editor.contextMenu, editor.palette].forEach((menu) => {
    menu.addEventListener("click", (event) => handleMapMenuAction(editor, event));
  });
  editor.contextMenu.addEventListener("keydown", (event) => navigateMapContextMenu(editor, event));
  document.addEventListener("pointerdown", (event) => {
    if (editor.menu.classList.contains("hidden") && editor.contextMenu.classList.contains("hidden") && editor.palette.classList.contains("hidden") && editor.textComposer.classList.contains("hidden")) return;
    if (editor.menu.contains(event.target) || editor.contextMenu.contains(event.target) || editor.palette.contains(event.target) || editor.textComposer.contains(event.target)) return;
    closeMapRadial(editor);
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMapRadial(editor);
  });
  map.on("movestart", () => closeMapRadial(editor));
  editor.controls.querySelector("[data-clear-map-drawing]").addEventListener("click", () => {
    editor.points = [];
    editor.lastScreenPoint = null;
    updateDrawPreview(editor);
  });
  editor.controls.querySelector("[data-cancel-map-drawing]").addEventListener("click", () => stopMapDrawing(editor));
  editor.textComposer.querySelector("[data-cancel-map-text]").addEventListener("click", () => closeMapRadial(editor));
  editor.textComposer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = editor.textComposer.querySelector("input");
    const label = input.value.trim();
    if (!label) return toast("Enter map text first", true);
    const location = editor.location;
    if (!location) return closeMapRadial(editor);
    const submit = editor.textComposer.querySelector('button[type="submit"]');
    const result = await send({type: "marker.created", lat: location.lat, lon: location.lon, marker_type: "text", label}, submit);
    if (result) {
      input.value = "";
      closeMapRadial(editor);
      await refreshMapEditor(editor);
    }
  });
  editor.sendButton.addEventListener("click", async (event) => {
    const points = sampleDrawing(editor.points);
    if (points.length < 2) return toast("Draw a trace first", true);
    const result = await send({type: "drawing.created", drawing_type: editor.drawShape, points}, event.currentTarget);
    if (result) {
      stopMapDrawing(editor);
      await refreshMapEditor(editor);
    }
  });
  return editor;
}

async function handleMapMenuAction(editor, event) {
  if (event.target.closest("[data-close-map-radial]")) {
    if (editor.menu.dataset.page === "draw") {
      setMapRadialPage(editor, "primary");
      return;
    }
    closeMapRadial(editor);
    return;
  }
  const pageButton = event.target.closest("[data-map-radial-page]");
  if (pageButton) {
    setMapRadialPage(editor, pageButton.dataset.mapRadialPage);
    return;
  }
  if (event.target.closest("[data-map-marker-palette]")) {
    openMapMarkerPalette(editor);
    return;
  }
  if (event.target.closest("[data-map-text]")) {
    openMapTextComposer(editor);
    return;
  }
  const markerButton = event.target.closest("[data-quick-marker]");
  if (markerButton && editor.location) {
    const location = editor.location;
    const markerType = markerButton.dataset.quickMarker;
    closeMapRadial(editor, true);
    const labels = {car: "Car", tank: "Tank", helicopter: "Helicopter", airplane: "Airplane"};
    const tactical = tacticalMarker(markerType);
    const visibleEvents = editor.scope === "command" ? commandEvents : fieldEvents;
    const result = await send({
      type: "marker.created",
      lat: location.lat,
      lon: location.lon,
      marker_type: markerType,
      label: markerType === "waypoint"
        ? nextWaypointLabel(visibleEvents)
        : tactical?.label || labels[markerType] || markerType[0].toUpperCase() + markerType.slice(1),
    }, markerButton);
    editor.location = null;
    if (result) await refreshMapEditor(editor);
    return;
  }
  const drawButton = event.target.closest("[data-map-draw]");
  if (drawButton) startMapDrawing(editor, drawButton.dataset.mapDraw || "trace");
}

function navigateMapContextMenu(editor, event) {
  const items = [...editor.contextMenu.querySelectorAll('[role="menuitem"]')];
  const current = items.indexOf(document.activeElement);
  let next = current;
  if (event.key === "ArrowDown") next = (current + 1) % items.length;
  else if (event.key === "ArrowUp") next = (current - 1 + items.length) % items.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = items.length - 1;
  else if (event.key === "Escape") {
    closeMapRadial(editor);
    editor.map.getCanvas().focus({preventScroll: true});
    return;
  } else return;
  event.preventDefault();
  items[next].focus({preventScroll: true});
}

async function refreshMapEditor(editor) {
  if (editor.scope === "command") await refresh();
  else await refreshFeed();
}

function setMapRadialPage(editor, page) {
  editor.menu.dataset.page = page;
  editor.menu.querySelectorAll(".radial-tool").forEach((button) => button.classList.toggle("hidden", page !== "draw"));
  editor.menu.querySelectorAll(".radial-action:not(.radial-tool)").forEach((button) => button.classList.toggle("hidden", page === "draw"));
  const closeLabel = editor.menu.querySelector(".radial-close small");
  const closeGlyph = editor.menu.querySelector(".radial-close > span");
  closeLabel.textContent = page === "draw" ? "BACK" : "CLOSE";
  closeGlyph.textContent = page === "draw" ? "‹" : "×";
  const focusTarget = page === "draw" ? editor.menu.querySelector(".radial-tool") : editor.menu.querySelector("[data-quick-marker]");
  if (!editor.menu.classList.contains("hidden")) focusTarget?.focus({preventScroll: true});
}

function closeMapRadial(editor, preserveLocation = false) {
  if (!editor) return;
  editor.menu.classList.add("hidden");
  editor.contextMenu.classList.add("hidden");
  editor.palette.classList.add("hidden");
  editor.textComposer.classList.add("hidden");
  setMapRadialPage(editor, "primary");
  if (!preserveLocation) editor.location = null;
}

function renderMapMarkerPalette(palette) {
  const body = palette.querySelector(".map-marker-palette-body");
  body.innerHTML = tacticalMarkerGroups().map((group) => `
    <section class="map-marker-group">
      <h3>${escapeHtml(group.label)}</h3>
      <div>
        ${group.markers.map((marker) => `
          <button type="button" data-quick-marker="${marker.type}" role="menuitem" style="--marker-color:${marker.color}">
            <b>${escapeHtml(marker.symbol)}</b><span>${escapeHtml(marker.label)}</span>
          </button>
        `).join("")}
      </div>
    </section>
  `).join("");
}

function openMapMarkerPalette(editor) {
  if (!editor.location) return;
  editor.menu.classList.add("hidden");
  editor.contextMenu.classList.add("hidden");
  editor.textComposer.classList.add("hidden");
  const canvas = editor.map.getCanvas();
  const coarse = window.matchMedia("(pointer: coarse), (max-width: 720px)").matches;
  if (coarse) {
    editor.palette.style.removeProperty("left");
    editor.palette.style.removeProperty("top");
  } else {
    const point = editor.map.project([editor.location.lon, editor.location.lat]);
    const width = Math.min(380, canvas.clientWidth - 24);
    const height = Math.min(440, canvas.clientHeight - 24);
    editor.palette.style.left = `${Math.max(12, Math.min(canvas.clientWidth - width - 12, point.x))}px`;
    editor.palette.style.top = `${Math.max(12, Math.min(canvas.clientHeight - height - 12, point.y))}px`;
  }
  editor.palette.classList.remove("hidden");
  editor.palette.querySelector("[data-quick-marker]")?.focus({preventScroll: true});
}

function openMapRadial(editor, x, y) {
  if (editor.drawMode) return;
  closeMapRadial(editor);
  const canvas = editor.map.getCanvas();
  const radius = 140;
  const safeX = Math.max(radius, Math.min(canvas.clientWidth - radius, x));
  const safeY = Math.max(radius, Math.min(canvas.clientHeight - radius, y));
  const coordinate = editor.map.unproject([x, y]);
  editor.location = {lat: coordinate.lat, lon: coordinate.lng};
  editor.menu.style.left = `${safeX}px`;
  editor.menu.style.top = `${safeY}px`;
  editor.menu.classList.remove("hidden");
  setMapRadialPage(editor, "primary");
  navigator.vibrate?.(12);
}

function openMapContextMenu(editor, x, y) {
  if (editor.drawMode) return;
  closeMapRadial(editor);
  const canvas = editor.map.getCanvas();
  const menuWidth = 204;
  const menuHeight = 366;
  const gutter = 8;
  const safeX = Math.max(gutter, Math.min(canvas.clientWidth - menuWidth - gutter, x));
  const safeY = Math.max(gutter, Math.min(canvas.clientHeight - menuHeight - gutter, y));
  const coordinate = editor.map.unproject([x, y]);
  editor.location = {lat: coordinate.lat, lon: coordinate.lng};
  editor.contextMenu.style.left = `${safeX}px`;
  editor.contextMenu.style.top = `${safeY}px`;
  editor.contextMenu.classList.remove("hidden");
  editor.contextMenu.querySelector('[role="menuitem"]').focus({preventScroll: true});
}

function openMapTextComposer(editor) {
  if (!editor.location) return;
  editor.menu.classList.add("hidden");
  editor.contextMenu.classList.add("hidden");
  const canvas = editor.map.getCanvas();
  const point = editor.map.project([editor.location.lon, editor.location.lat]);
  const width = Math.min(320, canvas.clientWidth - 24);
  const height = 126;
  const left = Math.max(12, Math.min(canvas.clientWidth - width - 12, point.x - width / 2));
  const top = Math.max(12, Math.min(canvas.clientHeight - height - 12, point.y + 12));
  editor.textComposer.style.left = `${left}px`;
  editor.textComposer.style.top = `${top}px`;
  editor.textComposer.classList.remove("hidden");
  editor.textComposer.querySelector("input").focus({preventScroll: true});
}

function clearMapLongPress(editor) {
  if (editor.longPressTimer) clearTimeout(editor.longPressTimer);
  editor.longPressTimer = null;
  editor.longPressStart = null;
  editor.longPressPointerId = null;
}

function updateDrawPreview(editor) {
  if (!editor?.map.getSource(editor.previewSource)) return;
  editor.map.getSource(editor.previewSource).setData({
    type: "FeatureCollection",
    features: editor.points.length >= 2 ? [{
      type: "Feature",
      geometry: editor.drawShape === "arrow" ? arrowGeometry(editor.points) : {type: "LineString", coordinates: editor.points},
      properties: {},
    }] : [],
  });
  editor.sendButton.disabled = editor.points.length < 2;
}

function startMapDrawing(editor, shape = "trace") {
  if (!editor?.map) return;
  closeMapRadial(editor);
  editor.drawMode = true;
  editor.drawShape = shape;
  editor.drawing = false;
  editor.points = [];
  editor.lastScreenPoint = null;
  editor.map.dragPan.disable();
  editor.map.getCanvas().classList.add("free-draw-active");
  editor.controls.classList.remove("hidden");
  editor.drawLabel.textContent = shape === "arrow" ? "DRAW ARROW" : "DRAW TRACE";
  updateDrawPreview(editor);
  toast(shape === "arrow" ? "Drag from the arrow start to its point" : "Draw on the map, then send the trace");
}

function stopMapDrawing(editor) {
  if (!editor) return;
  editor.drawMode = false;
  editor.drawShape = "trace";
  editor.drawing = false;
  editor.points = [];
  editor.lastScreenPoint = null;
  editor.map.dragPan.enable();
  editor.map.getCanvas().classList.remove("free-draw-active");
  editor.controls.classList.add("hidden");
  updateDrawPreview(editor);
}

function addMapDrawPoint(editor, canvas, event) {
  const rect = canvas.getBoundingClientRect();
  const screen = [event.clientX - rect.left, event.clientY - rect.top];
  if (editor.lastScreenPoint && Math.hypot(screen[0] - editor.lastScreenPoint[0], screen[1] - editor.lastScreenPoint[1]) < 5) return;
  const coordinate = editor.map.unproject(screen);
  const point = [coordinate.lng, coordinate.lat];
  if (editor.drawShape === "arrow" && editor.points.length) editor.points = [editor.points[0], point];
  else editor.points.push(point);
  editor.lastScreenPoint = screen;
  updateDrawPreview(editor);
}

function installMapEditorGestures(editor) {
  const canvas = editor.map.getCanvas();
  canvas.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    if (editor.lastPointerType === "touch") return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX ? event.clientX - rect.left : canvas.clientWidth / 2;
    const y = event.clientY ? event.clientY - rect.top : canvas.clientHeight / 2;
    openMapContextMenu(editor, x, y);
  });
  canvas.addEventListener("pointerdown", (event) => {
    editor.lastPointerType = event.pointerType;
    if (editor.drawMode && event.button === 0) {
      event.preventDefault();
      event.stopPropagation();
      editor.drawing = true;
      editor.points = [];
      editor.lastScreenPoint = null;
      canvas.setPointerCapture(event.pointerId);
      addMapDrawPoint(editor, canvas, event);
      return;
    }
    if (event.pointerType !== "touch") return;
    editor.activeTouchPointers.add(event.pointerId);
    clearMapLongPress(editor);
    // A radial action is intentionally single-finger only. Once another
    // finger participates, do not re-arm until every pointer is lifted.
    if (editor.activeTouchPointers.size !== 1) return;
    const rect = canvas.getBoundingClientRect();
    editor.longPressPointerId = event.pointerId;
    editor.longPressStart = {x: event.clientX, y: event.clientY};
    editor.longPressTimer = setTimeout(() => {
      if (editor.activeTouchPointers.size !== 1 || !editor.activeTouchPointers.has(event.pointerId)) {
        clearMapLongPress(editor);
        return;
      }
      openMapRadial(editor, event.clientX - rect.left, event.clientY - rect.top);
      editor.longPressTimer = null;
    }, 550);
  }, true);
  canvas.addEventListener("pointermove", (event) => {
    if (editor.drawMode && editor.drawing) {
      event.preventDefault();
      event.stopPropagation();
      addMapDrawPoint(editor, canvas, event);
      return;
    }
    if (event.pointerType === "touch" && editor.activeTouchPointers.size !== 1) clearMapLongPress(editor);
    if (editor.longPressStart && event.pointerId === editor.longPressPointerId
        && Math.hypot(event.clientX - editor.longPressStart.x, event.clientY - editor.longPressStart.y) > 10) clearMapLongPress(editor);
  }, true);
  const endPointer = (event) => {
    if (editor.drawMode && editor.drawing) {
      event.preventDefault();
      event.stopPropagation();
      addMapDrawPoint(editor, canvas, event);
      editor.drawing = false;
      editor.points = sampleDrawing(editor.points);
      updateDrawPreview(editor);
    }
    if (event.pointerType === "touch") editor.activeTouchPointers.delete(event.pointerId);
    clearMapLongPress(editor);
  };
  canvas.addEventListener("pointerup", endPointer, true);
  canvas.addEventListener("pointercancel", endPointer, true);
}

function propertyFlag(value) {
  return value === true || value === "true" || value === 1;
}

async function deleteMapItem(kind, eventId, button, popup) {
  if (button.dataset.confirm !== "true") {
    button.dataset.confirm = "true";
    button.textContent = "CONFIRM REMOVE";
    setTimeout(() => {
      if (button.isConnected) {
        button.dataset.confirm = "false";
        button.textContent = "REMOVE";
      }
    }, 5000);
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`/api/map/${encodeURIComponent(kind)}/${encodeURIComponent(eventId)}`, {method: "DELETE"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Map item removal failed");
    showDelivery(data.delivery);
    popup.remove();
    toast(`${kind === "drawing" ? "Drawing" : "Marker"} removed · signed over Reticulum`);
    if (role === "gateway") await refresh();
    else await refreshFeed();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

function renderWaypointNavigationLine() {
  const source = fieldMap?.getSource("field-navigation");
  if (!source) return;
  const calculated = waypointNavigation?.mode === "route"
    && Array.isArray(waypointNavigation.routeCoordinates)
    && waypointNavigation.routeCoordinates.length >= 2;
  const points = calculated
    ? waypointNavigation.routeCoordinates
    : waypointNavigation?.currentPoint
      ? [waypointNavigation.currentPoint, waypointNavigation.target]
      : [];
  source.setData({
    type: "FeatureCollection",
    features: points.length ? [{
      type: "Feature",
      geometry: {type: "LineString", coordinates: points},
      properties: {mode: calculated ? "route" : "direct"},
    }] : [],
  });
}

function fitCalculatedRoute(coordinates) {
  if (!fieldMap || !coordinates?.length || waypointNavigation?.centered) return;
  const lons = coordinates.map((point) => point[0]);
  const lats = coordinates.map((point) => point[1]);
  fieldMap.fitBounds(
    [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
    {padding: 80, maxZoom: 17, duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 500},
  );
  waypointNavigation.centered = true;
}

function stopWaypointNavigation(announce = true) {
  if (waypointNavigationWatchId !== null) navigator.geolocation?.clearWatch(waypointNavigationWatchId);
  const wasActive = Boolean(waypointNavigation);
  const mode = waypointNavigation?.mode;
  waypointNavigation?.routeAbortController?.abort();
  waypointNavigationWatchId = null;
  waypointNavigation = null;
  waypointArrivalReportPending = false;
  $("waypointNavigation").classList.add("hidden");
  $("waypointNavigation").classList.remove("arrived", "calculated");
  renderWaypointNavigationLine();
  if (wasActive && announce) toast(mode === "route" ? "Road navigation ended" : "Direct navigation ended");
}

async function reportWaypointArrival(position) {
  if (!waypointNavigation?.reportArrival || waypointArrivalReportPending || role !== "field") return;
  const now = Date.now();
  if (now - waypointNavigation.lastReportAttempt < 30000) return;
  waypointNavigation.lastReportAttempt = now;
  waypointArrivalReportPending = true;
  $("waypointNavigationState").textContent = "ON WAYPOINT · SHARING ARRIVAL";
  try {
    const data = await postJson("/api/send", {
      type: "position.updated",
      lat: Number(position.coords.latitude.toFixed(6)),
      lon: Number(position.coords.longitude.toFixed(6)),
      accuracy: Number(position.coords.accuracy.toFixed(1)),
    });
    if (!waypointNavigation) return;
    waypointNavigation.reported = true;
    $("waypointNavigationState").textContent = "ON WAYPOINT · TEAM NOTIFIED";
    showDelivery(data.delivery);
    await refreshFeed();
  } catch (error) {
    if (waypointNavigation) $("waypointNavigationState").textContent = "ON WAYPOINT · ARRIVAL NOT SENT";
    toast(`Arrival not sent · ${error.message}`, true);
  } finally {
    waypointArrivalReportPending = false;
  }
}

async function requestCalculatedRoute(currentPoint) {
  const navigation = waypointNavigation;
  if (!navigation || navigation.mode !== "route" || navigation.routeLoading) return;
  const url = calculatedRouteUrl(currentPoint, navigation.target);
  if (!url) {
    navigation.routeError = "Invalid route coordinates";
    return;
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  navigation.routeAbortController?.abort();
  navigation.routeAbortController = controller;
  navigation.routeLoading = true;
  navigation.routeError = "";
  navigation.lastRouteAttempt = Date.now();
  $("waypointNavigationState").textContent = "CALCULATING ROAD ROUTE";
  try {
    const response = await fetch(url, {cache: "no-store", signal: controller.signal, headers: {accept: "application/json"}});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.message || "Route service unavailable");
    const route = parseCalculatedRoute(payload);
    if (waypointNavigation !== navigation) return;
    navigation.routeCoordinates = route.coordinates;
    navigation.routeDistance = route.distance;
    navigation.routeDuration = route.duration;
    navigation.routeSteps = route.steps;
    navigation.routeOrigin = [...currentPoint];
    renderWaypointNavigationLine();
    fitCalculatedRoute(route.coordinates);
  } catch (error) {
    if (waypointNavigation !== navigation) return;
    navigation.routeCoordinates = null;
    navigation.routeError = error.name === "AbortError" ? "Route request timed out" : error.message;
  } finally {
    clearTimeout(timeout);
    if (waypointNavigation === navigation) {
      navigation.routeLoading = false;
      navigation.routeAbortController = null;
      if (navigation.lastPosition) updateWaypointNavigation(navigation.lastPosition);
    }
  }
}

function updateWaypointNavigation(position) {
  if (!waypointNavigation) return;
  const currentPoint = [Number(position.coords.longitude), Number(position.coords.latitude)];
  const accuracy = Number(position.coords.accuracy);
  if (!currentPoint.every(Number.isFinite)) return;
  waypointNavigation.lastPosition = position;
  waypointNavigation.currentPoint = currentPoint;
  waypointNavigation.currentAccuracy = accuracy;
  const directDistance = distanceMeters(currentPoint, waypointNavigation.target);
  const bearing = bearingDegrees(currentPoint, waypointNavigation.target);
  const accurate = Number.isFinite(accuracy) && accuracy > 0 && accuracy <= WAYPOINT_MAX_ARRIVAL_ACCURACY_METERS;
  const arrived = accurate && directDistance <= Math.max(WAYPOINT_ARRIVAL_RADIUS_METERS, accuracy);
  const calculated = waypointNavigation.mode === "route" && waypointNavigation.routeCoordinates?.length >= 2;
  $("waypointNavigationMode").textContent = waypointNavigation.mode === "route" ? "ROAD ROUTE" : "STRAIGHT LINE";
  $("waypointNavigationDistance").textContent = formatNavigationDistance(calculated ? waypointNavigation.routeDistance : directDistance);
  $("waypointNavigationBearing").textContent = calculated
    ? `${formatRouteDuration(waypointNavigation.routeDuration)} · ROAD`
    : `${Math.round(bearing).toString().padStart(3, "0")}° ${cardinalBearing(bearing)}`;
  $("waypointNavigation").classList.toggle("arrived", arrived);
  if (!arrived) {
    if (waypointNavigation.mode === "route" && waypointNavigation.routeLoading) {
      $("waypointNavigationState").textContent = "CALCULATING ROAD ROUTE";
    } else if (waypointNavigation.mode === "route" && waypointNavigation.routeError) {
      $("waypointNavigationState").textContent = `${waypointNavigation.routeError.toUpperCase()} · DIRECT LINE SHOWN`;
    } else if (calculated) {
      const fix = accurate ? ` · ±${Math.round(accuracy)} M` : "";
      $("waypointNavigationState").textContent = `${routeInstruction(waypointNavigation.routeSteps)}${fix}`;
    } else {
      $("waypointNavigationState").textContent = accurate ? `LIVE POSITION · ±${Math.round(accuracy)} M` : "WAITING FOR ACCURATE FIX";
    }
  } else if (!waypointNavigation.reportArrival) {
    $("waypointNavigationState").textContent = "AT LAST VERIFIED POSITION";
  } else if (waypointNavigation.reported) {
    $("waypointNavigationState").textContent = "ON WAYPOINT · TEAM NOTIFIED";
  } else {
    void reportWaypointArrival(position);
  }
  renderWaypointNavigationLine();
  if (waypointNavigation.mode === "route" && !arrived && !waypointNavigation.routeLoading
      && routeNeedsRefresh(
        waypointNavigation.routeOrigin,
        currentPoint,
        Date.now(),
        waypointNavigation.lastRouteAttempt,
        distanceMeters,
      )) {
    void requestCalculatedRoute(currentPoint);
  }
  if (waypointNavigation.mode !== "route" && !waypointNavigation.centered && fieldMap) {
    fieldMap.fitBounds([currentPoint, waypointNavigation.target], {
      padding: 80,
      maxZoom: 16,
      duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 500,
    });
    waypointNavigation.centered = true;
  }
}

function startNavigation(properties, target, popup, targetKind, mode) {
  if (!navigator.geolocation) return toast("Geolocation unavailable", true);
  stopWaypointNavigation(false);
  waypointNavigation = {
    id: String(targetKind === "operator" ? properties.senderHash : properties.id),
    label: String(targetKind === "operator"
      ? properties.callsign || properties.label || "Operator"
      : properties.label || "Waypoint"),
    targetKind,
    reportArrival: targetKind === "waypoint",
    mode,
    target: target.map(Number),
    currentPoint: null,
    currentAccuracy: null,
    lastPosition: null,
    centered: false,
    reported: false,
    lastReportAttempt: 0,
    lastRouteAttempt: 0,
    routeOrigin: null,
    routeCoordinates: null,
    routeDistance: 0,
    routeDuration: 0,
    routeSteps: [],
    routeLoading: false,
    routeError: "",
    routeAbortController: null,
  };
  $("waypointNavigationMode").textContent = mode === "route" ? "ROAD ROUTE" : "STRAIGHT LINE";
  $("waypointNavigation").classList.toggle("calculated", mode === "route");
  $("waypointNavigationLabel").textContent = waypointNavigation.label;
  $("waypointNavigationDistance").textContent = "—";
  $("waypointNavigationBearing").textContent = "—";
  $("waypointNavigationState").textContent = "ACQUIRING POSITION";
  $("waypointNavigation").classList.remove("hidden", "arrived");
  popup.remove();
  waypointNavigationWatchId = navigator.geolocation.watchPosition(
    updateWaypointNavigation,
    (error) => {
      $("waypointNavigationState").textContent = "POSITION UNAVAILABLE";
      toast(error.message, true);
    },
    {enableHighAccuracy: true, maximumAge: 3000, timeout: 20000},
  );
  toast(`${mode === "route" ? "In-app road route" : "Straight-line navigation"} · ${waypointNavigation.label}`);
}

function startDirectNavigation(properties, target, popup, targetKind) {
  startNavigation(properties, target, popup, targetKind, "direct");
}

function startCalculatedNavigation(properties, target, popup, targetKind) {
  startNavigation(properties, target, popup, targetKind, "route");
}

function showMapFeaturePopup(map, Popup, feature, lngLat) {
  const properties = feature.properties || {};
  const content = document.createElement("div");
  content.className = "map-popup";
  const title = document.createElement("strong");
  title.textContent = properties.label || "MAP ITEM";
  const meta = document.createElement("small");
  const kind = properties.mapKind || properties.kind || "map item";
  meta.textContent = properties.automatic
    ? `${String(properties.markerType || kind).toUpperCase()} · AUTO · SIGNED SOURCE`
    : propertyFlag(properties.queued)
      ? `${String(properties.markerType || kind).toUpperCase()} · LOCAL · QUEUED`
    : `${String(properties.markerType || kind).toUpperCase()} · VERIFIED`;
  content.append(title, meta);
  if (properties.reportMessage) {
    const report = document.createElement("p");
    const projection = properties.approximate
      ? "approximate; anchored to the reporter’s latest verified fix"
      : "derived from the reporter’s latest verified fix";
    report.textContent = `Reported by ${properties.callsign}: “${properties.reportMessage}” · ${projection}`;
    content.append(report);
  }
  if (properties.state || properties.landmark || properties.reportedAt) {
    const detail = document.createElement("small");
    const parts = [];
    if (properties.state) parts.push(properties.state);
    if (properties.landmark) parts.push(`LANDMARK: ${properties.landmark} (NOT MAP-RESOLVED)`);
    if (properties.reportType === "last-seen" && properties.observedAt) {
      parts.push(`LAST SEEN ${new Date(Number(properties.observedAt) * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}`);
    }
    if (properties.reportedAt) parts.push(new Date(Number(properties.reportedAt) * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}));
    detail.textContent = parts.join(" · ");
    content.append(detail);
  }
  const popup = new Popup({closeButton: true, offset: 12}).setLngLat(lngLat).setDOMContent(content).addTo(map);
  const capabilities = mapTargetCapabilities({
    role,
    kind,
    markerType: properties.markerType,
    senderHash: properties.senderHash,
    localIdentityHash,
  });
  if (capabilities.navigate) {
    const actions = document.createElement("div");
    actions.className = "map-popup-actions";
    const prompt = document.createElement("span");
    prompt.textContent = kind === "operator" ? "OPERATOR ACTIONS" : "WAYPOINT ACTIONS";
    const straight = document.createElement("button");
    straight.type = "button";
    straight.className = "map-navigation-choice";
    straight.innerHTML = '<b aria-hidden="true">↗</b><span>DIRECT LINE<small>Bearing + distance in Reticom</small></span>';
    straight.addEventListener("click", () => startDirectNavigation(properties, feature.geometry.coordinates, popup, capabilities.targetKind));
    const calculated = document.createElement("button");
    calculated.type = "button";
    calculated.className = "map-navigation-choice secondary";
    calculated.innerHTML = '<b aria-hidden="true">⌁</b><span>CALCULATED ROUTE<small>Road route inside Reticom</small></span>';
    calculated.addEventListener("click", () => startCalculatedNavigation(properties, feature.geometry.coordinates, popup, capabilities.targetKind));
    actions.append(prompt, straight, calculated);
    if (capabilities.privateChat) {
      const chat = document.createElement("button");
      chat.type = "button";
      chat.className = "map-navigation-choice chat";
      chat.innerHTML = '<b aria-hidden="true">••</b><span>PRIVATE CHAT<small>Encrypted Reticulum link</small></span>';
      chat.addEventListener("click", () => {
        popup.remove();
        setFieldPage("ops");
        openPrivateChat(properties.senderHash);
      });
      actions.append(chat);
    }
    const note = document.createElement("small");
    note.className = "map-navigation-note";
    note.textContent = kind === "operator"
      ? "Direct line follows this operator's latest verified position. Calculated route uses the current fix."
      : "Reaching a waypoint with an accurate fix is announced to the team.";
    actions.append(note);
    content.append(actions);
  }
  if (kind !== "operator" && propertyFlag(properties.removable)) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "map-remove-action";
    remove.textContent = "REMOVE";
    remove.addEventListener("click", () => deleteMapItem(kind, properties.id, remove, popup));
    content.append(remove);
  }
}

function installMapFeatureInteractions(map, Popup, pointLayers, drawingLayer) {
  const interactiveLayers = [...pointLayers, drawingLayer];
  map.on("click", (event) => {
    const radius = matchMedia("(pointer: coarse)").matches ? 18 : 10;
    const feature = map.queryRenderedFeatures(
      [[event.point.x - radius, event.point.y - radius], [event.point.x + radius, event.point.y + radius]],
      {layers: interactiveLayers},
    )[0];
    if (!feature) return;
    const popupPosition = feature.geometry.type === "Point" ? feature.geometry.coordinates : event.lngLat;
    showMapFeaturePopup(map, Popup, feature, popupPosition);
  });
  for (const layer of interactiveLayers) {
    map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
  }
}

function renderOperators(operators, events) {
  if (!operators.length) { $("operatorList").className = "operator-list empty-copy"; $("operatorList").textContent = "Waiting for the first verified operator event."; return; }
  const displayedPositions = new Map(
    currentLocations(events)
      .filter((event) => event.type === "position.updated")
      .map((event) => [event.network?.sender_hash || event.callsign, event]),
  );
  $("operatorList").className = "operator-list";
  $("operatorList").innerHTML = operators.map((operator) => {
    const position = displayedPositions.get(operator.sender_hash);
    return `<div class="operator"><strong><b class="operator-glyph" style="color:${operatorColor(operator.color)}">${operatorGlyph(operator.icon)}</b> ${escapeHtml(operator.callsign)}</strong><span>${position ? `${position.lat.toFixed(5)}, ${position.lon.toFixed(5)}` : "No reliable position"}</span><span>${timeLabel(operator.last_seen)} · ${operator.event_count} verified event${operator.event_count === 1 ? "" : "s"}</span><span>${shortHash(operator.sender_hash)}</span></div>`;
  }).join("");
}

function renderUser(user) {
  if (!user) return;
  currentUser = user;
  selectedUserIcon = user.icon;
  selectedUserColor = user.color || "moss";
  $("topbarCallsign").textContent = user.callsign;
  $("userSettingsToggle").setAttribute("aria-label", `Open user settings for ${user.callsign}`);
  $("settingsCallsign").value = user.callsign;
  $("joinCallsign").value = user.callsign;
  document.querySelectorAll(".icon-picker button").forEach((button) => {
    const selected = button.dataset.icon === user.icon;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  document.querySelectorAll(".color-picker button").forEach((button) => {
    const selected = button.dataset.color === selectedUserColor;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function renderCommandUser(user) {
  if (!user) return;
  currentUser = user;
  $("commandSenderBadge").textContent = `${operatorGlyph(user.icon)} ${user.callsign}`;
  $("commandSenderBadge").style.color = operatorColor(user.color);
}

function renderTeam(team) {
  currentTeamModules = Array.isArray(team.modules) ? team.modules : [];
  const previousEveryoneAdmin = currentEveryoneAdmin;
  currentEveryoneAdmin = team.everyone_admin === true;
  currentTeamHosted = team.hosted === true;
  currentTeamAdmin = role === "gateway" || team.admin === true || currentTeamHosted;
  currentTeamJoinCode = team.join_code || "";
  if (previousEveryoneAdmin !== currentEveryoneAdmin) lastFieldMapSignature = "";
  const tasksEnabled = currentTeamModules.includes("tasks");
  teamOperational = role === "gateway" ? Boolean(team.created) : Boolean(team.joined);
  if (teamOperational) connectLiveVoice();
  else if (liveVoiceSocket) liveVoiceSocket.close(1000, "No active team");
  if (role === "gateway") {
    $("commandSetup").classList.toggle("hidden", team.created);
    $("commandView").classList.toggle("hidden", !team.created);
    if (team.created) {
      $("commandTeamName").textContent = team.name;
      $("commandJoinCode").textContent = team.join_code;
      $("teamQr").src = `/api/team/qr?v=${team.created_at}`;
      $("toggleTasksModule").textContent = tasksEnabled ? "DISABLE" : "ENABLE";
      $("toggleEveryoneAdmin").textContent = currentEveryoneAdmin ? "ON" : "OFF";
      $("toggleEveryoneAdmin").setAttribute("aria-pressed", String(currentEveryoneAdmin));
      $("commandTasksModule").classList.toggle("hidden", !tasksEnabled);
    }
    return;
  }
  $("fieldSetup").classList.toggle("hidden", team.joined);
  $("fieldView").classList.toggle("hidden", !team.joined);
  $("fieldBottomNav").classList.toggle("hidden", !team.joined);
  $("teamSessionSettings").classList.toggle("hidden", !team.joined);
  $("adminSettingsToggle").classList.toggle("hidden", !team.joined || !currentTeamAdmin);
  if (!team.joined || !currentTeamAdmin) {
    $("adminSettingsPanel").classList.add("hidden");
    $("adminSettingsToggle").setAttribute("aria-expanded", "false");
  }
  if (team.joined) {
    $("fieldTeamName").textContent = team.name || "Joined team";
    $("fieldTeamDestination").textContent = shortHash(team.destination, 12);
    $("settingsTeamName").textContent = team.name || "Joined team";
    $("fieldAdminMode").classList.toggle("hidden", !currentTeamAdmin);
    $("fieldTasksModule").classList.toggle("hidden", !tasksEnabled);
    $("adminTeamName").textContent = team.name || "Joined team";
    $("adminJoinCode").textContent = currentTeamJoinCode || "Unavailable";
    $("adminHostState").textContent = currentTeamHosted
      ? "HOSTED ON THIS DEVICE · Reticom must keep running for the team destination."
      : "REMOTE TEAM HOST · Admin changes use an identified Reticulum Link.";
    $("fieldToggleTasksModule").textContent = tasksEnabled ? "ON" : "OFF";
    $("fieldToggleTasksModule").setAttribute("aria-pressed", String(tasksEnabled));
    $("fieldToggleEveryoneAdmin").textContent = currentEveryoneAdmin ? "ON" : "OFF";
    $("fieldToggleEveryoneAdmin").setAttribute("aria-pressed", String(currentEveryoneAdmin));
  }
}

function renderTasks(tasks, targetId, field = false) {
  const list = $(targetId);
  const callsign = currentUser.callsign.toLowerCase();
  const visible = field
    ? tasks.filter((task) => !task.assignee || task.assignee.toLowerCase() === callsign)
    : tasks;
  if (!visible.length) {
    list.className = "task-list empty-copy";
    list.textContent = field ? "No assignments for this callsign." : "No tasks yet.";
    return;
  }
  list.className = "task-list";
  list.innerHTML = visible.map((task) => `
    <article class="task ${task.completed_at ? "done" : ""}">
      <div><strong>${escapeHtml(task.title)}</strong><span>${task.assignee ? `FOR ${escapeHtml(task.assignee)}` : "OPEN TO TEAM"}${task.completed_at ? ` · DONE BY ${escapeHtml(task.completed_by || "FIELD")}` : ""}</span></div>
      ${field && !task.completed_at
        ? `<button class="secondary compact task-complete" data-task-id="${escapeHtml(task.id)}">COMPLETE</button>`
        : !field
          ? `<div class="task-actions"><span class="task-status">${task.completed_at ? "✓ DONE" : "OPEN"}</span><button class="text-button task-delete" data-task-id="${escapeHtml(task.id)}" data-confirm="false" aria-label="Delete ${task.completed_at ? "completed" : "open"} task ${escapeHtml(task.title)}">DELETE</button></div>`
          : `<span class="task-status">${task.completed_at ? "✓ DONE" : "OPEN"}</span>`}
    </article>`).join("");
}

async function refreshTasks() {
  if (!currentTeamModules.includes("tasks") || tasksLoading) return;
  tasksLoading = true;
  try {
    const response = await fetch("/api/tasks", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Task sync failed");
    renderTasks(data.tasks || [], role === "gateway" ? "commandTaskList" : "fieldTaskList", role === "field");
    if (role === "field") {
      $("taskSyncState").textContent = `RNS · ${Math.round(data.network?.response_ms || 0)} MS`;
      $("taskSyncState").classList.add("synced");
    }
  } catch (error) {
    if (role === "field") {
      $("taskSyncState").textContent = "SYNC FAILED";
      $("taskSyncState").classList.remove("synced");
      $("fieldTaskList").className = "task-list empty-copy";
      $("fieldTaskList").textContent = error.message;
    }
  } finally {
    tasksLoading = false;
  }
}

function renderMapMessages(events, containerId) {
  const container = $(containerId);
  const isFieldOverlay = containerId === "latestMessages";
  const latest = mapOverlayMessages(events, {
    localIdentityHash,
    readIds: isFieldOverlay ? readMapMessageIds : null,
  });
  if (!latest.length) {
    container.replaceChildren();
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");
  container.innerHTML = latest.map((event) => {
    const automaticReport = parseAutomaticReport(contactReportText(event));
    const contact = automaticReport?.type === "contact";
    const typeLabel = event.type === "ptt.broadcast" ? "VOICE" : automaticReport ? (automaticReport.action === "cancel-last" ? "MAP CANCEL" : "MAP REPORT") : "MESSAGE";
    return `
      <article class="latest-message ${automaticReport ? "contact-message" : ""}"${isFieldOverlay ? ` data-map-message-id="${escapeHtml(event.id)}"` : ""}>
        <div><span class="map-message-type ${automaticReport ? "contact" : ""}">${typeLabel}</span><b class="operator-glyph" style="color:${operatorColor(event.color)}">${operatorGlyph(event.icon)}</b><strong>${escapeHtml(event.callsign)}</strong><time>${timeLabel(event.network.received_at)}</time>${ownMessageRemoveButton(event)}${isFieldOverlay ? `<button class="map-message-read" type="button" aria-label="Mark message from ${escapeHtml(event.callsign)} as read" title="Mark read">✓</button>` : ""}</div>
        ${event.type === "ptt.broadcast" ? eventDescription(event) : `<p>${escapeHtml(event.message)}</p>`}
      </article>`;
  }).join("");
}

function renderLatestMessages(events) {
  renderMapMessages(events, "latestMessages");
}

function finishMapMessageCard(card, messageId, direction = 1) {
  if (!card || !messageId) return;
  readMapMessageIds.add(messageId);
  saveReadMapMessages();
  card.classList.remove("swiping", "settling");
  card.classList.add("dismissing");
  card.style.setProperty("--map-message-swipe-x", `${direction * Math.max(card.offsetWidth * 1.25, window.innerWidth)}px`);
  card.style.setProperty("--map-message-swipe-opacity", "0");
  window.setTimeout(() => renderLatestMessages(fieldEvents), 190);
}

function settleMapMessageCard(card) {
  card.classList.remove("swiping");
  card.classList.add("settling");
  card.style.setProperty("--map-message-swipe-x", "0px");
  card.style.setProperty("--map-message-swipe-opacity", "1");
  window.setTimeout(() => {
    if (!card.isConnected) return;
    card.classList.remove("settling");
    card.style.removeProperty("--map-message-swipe-x");
    card.style.removeProperty("--map-message-swipe-opacity");
  }, 190);
}

function beginMapMessageSwipe(event) {
  const mobileLayout = matchMedia("(max-width: 700px)").matches || event.pointerType === "touch";
  if (!mobileLayout || event.target.closest("button, audio, .voice-player")) return;
  const card = event.target.closest(".latest-message[data-map-message-id]");
  if (!card) return;
  mapMessageSwipe = {
    card,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startedAt: performance.now(),
    width: card.offsetWidth,
    active: false,
  };
  event.stopPropagation();
  try { card.setPointerCapture(event.pointerId); } catch { /* The gesture can continue without capture. */ }
}

function moveMapMessageSwipe(event) {
  const swipe = mapMessageSwipe;
  if (!swipe || swipe.pointerId !== event.pointerId) return;
  event.stopPropagation();
  const deltaX = event.clientX - swipe.startX;
  const deltaY = event.clientY - swipe.startY;
  if (!swipe.active) {
    if (Math.abs(deltaX) < 10 && Math.abs(deltaY) < 10) return;
    if (Math.abs(deltaY) >= Math.abs(deltaX)) {
      mapMessageSwipe = null;
      return;
    }
    swipe.active = true;
    swipe.card.classList.add("swiping");
  }
  event.preventDefault();
  const limitedX = Math.sign(deltaX) * Math.min(Math.abs(deltaX), swipe.width * 1.05);
  swipe.card.style.setProperty("--map-message-swipe-x", `${limitedX}px`);
  swipe.card.style.setProperty("--map-message-swipe-opacity", String(Math.max(0.38, 1 - Math.abs(limitedX) / swipe.width * 0.72)));
}

function endMapMessageSwipe(event, cancelled = false) {
  const swipe = mapMessageSwipe;
  if (!swipe || swipe.pointerId !== event.pointerId) return;
  event.stopPropagation();
  mapMessageSwipe = null;
  if (!swipe.active) return;
  suppressMapMessageClickUntil = performance.now() + 350;
  const deltaX = event.clientX - swipe.startX;
  const deltaY = event.clientY - swipe.startY;
  if (!cancelled && shouldDismissMapMessage({deltaX, deltaY, width: swipe.width, elapsedMs: performance.now() - swipe.startedAt})) {
    finishMapMessageCard(swipe.card, swipe.card.dataset.mapMessageId, Math.sign(deltaX) || 1);
  } else {
    settleMapMessageCard(swipe.card);
  }
}

function ownMessageRemoveButton(event) {
  const mayRemove = role === "gateway" || currentTeamAdmin || event.network?.sender_hash === localIdentityHash;
  if (event.type !== "chat.message" || !mayRemove) return "";
  return `<button class="message-delete" data-message-id="${escapeHtml(event.id)}" aria-label="Remove team message">REMOVE</button>`;
}

function renderFieldFeed(events) {
  const visibleEvents = intelEvents(events);
  $("feedCount").textContent = `${visibleEvents.length} EVENT${visibleEvents.length === 1 ? "" : "S"}`;
  if (!visibleEvents.length) {
    $("fieldFeedList").className = "field-feed-list empty-copy";
    $("fieldFeedList").textContent = "No verified intel received yet.";
    return;
  }
  $("fieldFeedList").className = "field-feed-list";
  $("fieldFeedList").innerHTML = visibleEvents.map((event) => `
    <article class="feed-event ${event.type.startsWith("private.") ? "private" : ""}">
      <div class="feed-event-head"><span><b class="operator-glyph" style="color:${operatorColor(event.color)}">${operatorGlyph(event.icon)}</b> ${escapeHtml(event.callsign)}</span><time>${timeLabel(event.network.received_at)}</time>${ownMessageRemoveButton(event)}</div>
      <strong>${escapeHtml(intelEventType(event).replace(".", " ").toUpperCase())}</strong>
      <div class="feed-event-body">${eventDescription(event)}</div>
      <small>${event.network?.queued ? "◷ LOCAL · QUEUED FOR RNS" : `✓ ${event.type.startsWith("private.") ? "PRIVATE · IDENTIFIED RNS LINK · " : ""}${shortHash(event.network.sender_hash)}`}</small>
    </article>`).join("");
}

function renderFieldOperators(events) {
  const operators = new Map();
  for (const event of events) {
    const sender = event.network?.sender_hash;
    if (!sender) continue;
    if (!operators.has(sender)) {
      operators.set(sender, {callsign: event.callsign, icon: event.icon || "dot", color: event.color || "moss", last_seen: event.network.received_at, count: 0});
    }
    operators.get(sender).count += 1;
  }
  fieldOperators = operators;
  const values = [...operators.entries()];
  if (!values.length) {
    $("opsOperatorList").className = "operator-list empty-copy";
    $("opsOperatorList").textContent = "No verified operators yet.";
    return;
  }
  $("opsOperatorList").className = "operator-list";
  $("opsOperatorList").innerHTML = values.map(([sender, operator]) => `
    <article class="operator ops-operator">
      <div class="ops-operator-copy">
        <strong><b class="operator-glyph" style="color:${operatorColor(operator.color)}">${operatorGlyph(operator.icon)}</b> ${escapeHtml(operator.callsign)}</strong>
        <span>${timeLabel(operator.last_seen)} · ${operator.count} events</span>
        <span>${shortHash(sender)}</span>
      </div>
      ${sender === localIdentityHash
        ? `<span class="operator-self">THIS DEVICE</span>`
        : `<button type="button" class="secondary compact operator-message ${activePrivatePeer?.hash === sender ? "selected" : ""}" data-private-peer="${escapeHtml(sender)}">MESSAGE</button>`}
    </article>`).join("");
}

function privateConversation(peerHash) {
  return privateMessages.filter((message) => {
    const sender = message.network?.sender_hash;
    const recipient = message.recipient_hash;
    return (sender === localIdentityHash && recipient === peerHash)
      || (sender === peerHash && recipient === localIdentityHash);
  });
}

function renderPrivateChatMessages() {
  if (!activePrivatePeer) return;
  const messages = privateConversation(activePrivatePeer.hash);
  const list = $("privateChatMessages");
  if (!messages.length) {
    list.className = "private-chat-messages empty-copy";
    list.textContent = "No private messages yet. This conversation stays out of the team Intel feed.";
    return;
  }
  list.className = "private-chat-messages";
  list.innerHTML = messages.map((message) => {
    const own = message.network?.sender_hash === localIdentityHash;
    return `<article class="private-chat-message ${own ? "own" : ""}">
      <header><span>${own ? "YOU" : escapeHtml(message.callsign)}</span><time>${timeLabel(message.network?.received_at || message.created_at)}</time></header>
      ${message.type === "private.ptt" ? voicePlayerMarkup(message) : `<p>${escapeHtml(message.message)}</p>`}
      <small>✓ PRIVATE · IDENTIFIED RNS LINK</small>
    </article>`;
  }).join("");
  list.scrollTop = list.scrollHeight;
}

async function refreshPrivateChat() {
  if (!activePrivatePeer || privateChatLoading || role !== "field") return;
  privateChatLoading = true;
  $("privateChatStatus").textContent = "SYNCING PRIVATE RETICULUM MAILBOX…";
  try {
    const response = await fetch("/api/private/messages", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Private chat sync failed");
    privateMessages = Array.isArray(data.messages) ? data.messages : [];
    primeRecentVoiceTranscripts(privateMessages);
    renderPrivateChatMessages();
    renderFieldFeed(combinedFieldFeedEvents());
    $("privateChatStatus").textContent = `PRIVATE RNS · ${Math.round(data.network?.response_ms || 0)} MS · STORED BY COMMAND`;
  } catch (error) {
    $("privateChatStatus").textContent = error.message;
  } finally {
    privateChatLoading = false;
  }
}

function openPrivateChat(peerHash) {
  const operator = fieldOperators.get(peerHash);
  if (!operator || peerHash === localIdentityHash) return;
  activePrivatePeer = {hash: peerHash, ...operator};
  $("privateChatCallsign").innerHTML = `<b class="operator-glyph" style="color:${operatorColor(operator.color)}">${operatorGlyph(operator.icon)}</b> ${escapeHtml(operator.callsign)}`;
  $("privateChatIdentity").textContent = peerHash;
  $("privateChatMessage").placeholder = `Message ${operator.callsign}`;
  $("privatePttButton").dataset.privateRecipient = peerHash;
  $("privatePttButton").dataset.idleLabel = `HOLD FOR ${operator.callsign.toUpperCase()}`;
  $("privatePttButton").dataset.idleHint = "PRIVATE MAILBOX · RECORDED · MAX 8 SEC";
  setPttState("idle");
  $("privateChatPanel").classList.remove("hidden");
  renderFieldOperators(fieldEvents);
  $("privateChatMessages").className = "private-chat-messages empty-copy";
  $("privateChatMessages").textContent = "Retrieving private mailbox over Reticulum…";
  void refreshPrivateChat();
  if (matchMedia("(max-width: 700px)").matches) {
    setTimeout(() => $("privateChatPanel").scrollIntoView({block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"}), 0);
  }
}

function closePrivateChat() {
  activePrivatePeer = null;
  $("privateChatPanel").classList.add("hidden");
  $("privateChatMessage").value = "";
  renderFieldOperators(fieldEvents);
}

async function refreshFeed() {
  if (role !== "field" || feedLoading) return;
  if (!fieldMap && !fieldMapReady) void renderFieldMap(fieldEvents);
  feedLoading = true;
  try {
    const response = await fetch("/api/feed", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Feed sync failed");
    if (data.team) renderTeam(data.team);
    fieldFeedOnline = data.network?.online !== false;
    fieldQueuedEvents = Number(data.network?.queued || 0);
    connectivityProbe = {at: Date.now(), online: fieldFeedOnline, ms: data.network?.response_ms};
    renderConnectivity();
    fieldEvents = withDisplayWaypointLabels(data.events || []);
    privateMessages = Array.isArray(data.private_events) ? data.private_events : [];
    saveFieldEventCache(fieldTeamDestination, fieldEvents);
    const incomingEvents = combinedFieldFeedEvents();
    primeRecentVoiceTranscripts(incomingEvents);
    queueIncomingAlerts(incomingEvents);
    renderLatestMessages(fieldEvents);
    renderFieldOperators(fieldEvents);
    renderFieldFeed(incomingEvents);
    if (activePrivatePeer) renderPrivateChatMessages();
    renderFieldMap(fieldEvents);
    $("fieldFeedSync").textContent = fieldFeedOnline
      ? `RNS · ${Math.round(data.network?.response_ms || 0)} MS${fieldQueuedEvents ? ` · ${fieldQueuedEvents} QUEUED` : ""}`
      : fieldQueuedEvents
        ? `LOCAL READY · ${fieldQueuedEvents} QUEUED`
        : "LOCAL READY · TEAM OFFLINE";
    $("fieldFeedSync").classList.toggle("synced", fieldFeedOnline);
  } catch (error) {
    fieldFeedOnline = false;
    connectivityProbe = {at: Date.now(), online: false};
    renderConnectivity();
    $("fieldFeedSync").textContent = fieldMapOnline ? "MAP ONLINE · RNS OFFLINE" : "RNS OFFLINE";
    $("fieldFeedSync").classList.remove("synced");
  } finally {
    feedLoading = false;
  }
}

function renderNearby(teams) {
  const list = $("nearbyTeams");
  if (!teams.length) {
    list.className = "nearby-list empty-copy";
    list.textContent = "No named team announce heard in the last 3 minutes.";
    return;
  }
  list.className = "nearby-list";
  list.innerHTML = teams.map((team) => {
    const hops = team.hops < 128 ? `${team.hops} hop${team.hops === 1 ? "" : "s"}` : "path heard";
    const modules = team.modules?.length ? ` · ${team.modules.map((item) => item.toUpperCase()).join(" + ")}` : "";
    return `<div class="nearby-team"><div><strong>${escapeHtml(team.name)}</strong><span>${hops} · ${escapeHtml(team.via)}${modules}</span></div><button class="secondary compact nearby-join" data-code="${escapeHtml(team.join_code)}" ${team.joined ? "disabled" : ""}>${team.joined ? "JOINED" : "JOIN"}</button></div>`;
  }).join("");
}

async function refresh() {
  try {
    const response = await fetch("/api/state", {cache: "no-store"});
    const state = await response.json();
    role = state.role;
    localIdentityHash = state.network.identity || "";
    $("roleLabel").textContent = role === "gateway" ? "COMMAND" : "FIELD";
    ["commandSetup", "commandView", "fieldSetup", "fieldView"].forEach((id) => $(id).classList.add("hidden"));
    setNetwork(state);
    $("userSettingsToggle").classList.toggle("hidden", role !== "field");
    $("incomingAlertsToggle").classList.remove("hidden");
    updateIncomingAlertsUi();
    if (role === "field") renderUser(state.user);
    else renderCommandUser(state.user);
    renderTeam(state.team);
    if (role === "field") {
      syncLocalMapPosition(Boolean(state.team.joined));
      syncAutomaticLocationSharing(Boolean(state.team.joined));
    }
    if (role === "gateway") {
      commandEvents = withDisplayWaypointLabels(state.events || []);
      primeRecentVoiceTranscripts(commandEvents);
      $("destinationHash").textContent = state.network.destination;
      renderTimeline(commandEvents); renderOperators(state.operators || [], commandEvents);
      renderMapMessages(commandEvents, "commandLatestMessages");
      queueIncomingAlerts(commandEvents);
      if (state.team.created) renderMap(commandEvents);
    } else {
      $("fieldIdentity").textContent = state.network.identity;
      $("setupFieldIdentity").textContent = state.network.identity;
      renderNearby(state.nearby_teams || []);
      if (state.network.last_delivery) showDelivery(state.network.last_delivery);
      fieldQueuedEvents = Number(state.network.queued_events ?? fieldQueuedEvents ?? 0);
      const destination = state.team.destination || "";
      if (destination !== fieldTeamDestination) {
        if (activePrivatePeer) closePrivateChat();
        privateMessages = [];
        fieldTeamDestination = destination;
        loadReadMapMessages(destination);
        lastFieldMapSignature = "";
        fieldMapMarkerIds = new Set();
        fieldMapHasRendered = false;
        fieldFeedOnline = false;
        fieldEvents = readFieldEventCache(destination);
        renderLatestMessages(fieldEvents);
        renderFieldFeed(fieldEvents);
        renderFieldOperators(fieldEvents);
        if (state.team.joined) void renderFieldMap(fieldEvents);
      } else if (state.team.joined && !fieldMap && !fieldMapReady) {
        void renderFieldMap(fieldEvents);
      }
    }
    if ((state.team.created || state.team.joined) && currentTeamModules.includes("tasks")) refreshTasks();
    if (role === "field" && state.team.joined) refreshFeed();
    if (role === "field" && state.team.joined && activePrivatePeer) refreshPrivateChat();
  } catch (error) {
    setNetwork(null);
  }
}

async function postJson(path, payload) {
  const response = await fetch(path, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

async function putJson(path, payload) {
  const response = await fetch(path, {method: "PUT", headers: {"content-type": "application/json"}, body: JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

async function loadNetworkSettings() {
  const status = $("networkSettingsStatus");
  try {
    const response = await fetch("/api/network/settings");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Network settings unavailable");
    const custom = data.custom_node;
    $("customNodeHost").value = custom?.host || "";
    $("customNodePort").value = custom?.port || 4242;
    $("networkRoutingMode").textContent = custom ? "CUSTOM + AUTO" : "AUTO";
    $("networkRoutingSummary").textContent = custom
      ? `Internet TCP node: ${custom.host}:${custom.port}. Auto community connections remain available as fallback.`
      : `Auto uses ${data.community_bootstraps.length} built-in community Internet TCP nodes and can connect to up to ${data.autoconnect_count} discovered TCP nodes over 4G/5G or Internet Wi-Fi.`;
    status.textContent = custom
      ? "Custom Internet node saved. Fully restart Reticom to apply connection changes."
      : "Leave blank for Auto. To use your own Internet node, enter its hostname or IP address and TCP port. Fully restart Reticom after saving changes.";
  } catch (error) {
    status.textContent = error.message;
  }
}

function activeOfflineMap() {
  return role === "gateway" ? operationalMap : fieldMap;
}

function visibleOfflineBounds() {
  const map = activeOfflineMap();
  if (!map) return null;
  const bounds = map.getBounds();
  const result = {
    west: Math.max(-180, Number(bounds.getWest())),
    south: Math.max(-85.05112878, Number(bounds.getSouth())),
    east: Math.min(180, Number(bounds.getEast())),
    north: Math.min(85.05112878, Number(bounds.getNorth())),
  };
  return result.west < result.east && result.south < result.north ? result : null;
}

function updateOfflineMapEstimate() {
  const estimate = $("offlineMapEstimate");
  const button = $("downloadOfflineMap");
  if (!estimate || !button) return;
  const bounds = visibleOfflineBounds();
  if (!bounds) {
    estimate.classList.remove("error");
    estimate.textContent = "Open the map to select an area.";
    button.disabled = true;
    return;
  }
  const maxZoom = Number($("offlineMapDetail").value || 14);
  const tiles = tileCountForBounds(bounds, maxZoom, offlineMapMaxTiles);
  if (!tiles || tiles > offlineMapMaxTiles) {
    estimate.classList.add("error");
    estimate.textContent = `Area too large · zoom in before downloading`;
    button.disabled = true;
    return;
  }
  estimate.classList.remove("error");
  estimate.innerHTML = `<span>CURRENT VIEW · ${tiles} TILE${tiles === 1 ? "" : "S"}</span><strong>EST. ${formatMapBytes(tiles * 220 * 1024)}</strong>`;
  button.disabled = false;
}

function offlineMapStatus(pack) {
  if (pack.status === "ready") return `READY · ${formatMapBytes(pack.bytes)} · Z${pack.max_zoom}`;
  if (pack.status === "error") return `STOPPED · ${pack.downloaded_tiles}/${pack.tile_count} TILES`;
  if (pack.status === "queued") return `QUEUED · ${pack.tile_count} TILES`;
  return `${packProgress(pack)}% · ${pack.downloaded_tiles}/${pack.tile_count} TILES`;
}

function renderOfflineMaps(data) {
  offlineMapMaxTiles = Number(data.max_tiles) || 2500;
  const packs = Array.isArray(data.packs) ? data.packs : [];
  $("offlineMapStorage").textContent = formatMapBytes(data.bytes);
  const list = $("offlineMapList");
  if (!packs.length) {
    list.className = "offline-map-list empty-copy";
    list.textContent = "No offline areas saved.";
  } else {
    list.className = "offline-map-list";
    list.innerHTML = packs.map((pack) => {
      const progress = packProgress(pack) / 100;
      const failed = pack.status === "error";
      const detail = failed && pack.error ? ` title="${escapeHtml(pack.error)}"` : "";
      return `<div class="offline-map-pack${failed ? " failed" : ""}"${detail}><div><strong>${escapeHtml(pack.name)}</strong><span>${offlineMapStatus(pack)}</span></div><button type="button" data-delete-offline-map="${escapeHtml(pack.id)}" data-confirm="false" ${pack.status === "downloading" ? "disabled" : ""}>DELETE</button>${pack.status === "downloading" || pack.status === "queued" ? `<div class="offline-map-progress"><i style="--progress:${progress}"></i></div>` : ""}</div>`;
    }).join("");
  }
  const downloading = packs.some((pack) => pack.status === "downloading" || pack.status === "queued");
  clearTimeout(offlineMapPollTimer);
  offlineMapPollTimer = downloading ? setTimeout(refreshOfflineMaps, 900) : null;
  updateOfflineMapEstimate();
}

async function refreshOfflineMaps() {
  try {
    const response = await fetch("/api/offline-maps", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Offline map storage could not be read");
    renderOfflineMaps(data);
  } catch (error) {
    $("offlineMapEstimate").classList.add("error");
    $("offlineMapEstimate").textContent = error.message;
  }
}

async function joinWithCode(joinCode, button) {
  stopQrScan();
  button.disabled = true;
  try {
    const data = await postJson("/api/team/join", {join_code: joinCode, callsign: $("joinCallsign").value});
    $("joinCodeInput").value = data.team.join_code;
    renderTeam(data.team);
    showDelivery(data.delivery);
    toast(data.delivery?.status === "queued"
      ? "Team saved locally · waiting for Reticulum sync"
      : "Team joined · Reticulum proof received");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function stopQrScan() {
  if (qrScanTimer) clearTimeout(qrScanTimer);
  qrScanTimer = null;
  if (qrStream) qrStream.getTracks().forEach((track) => track.stop());
  qrStream = null;
  $("qrVideo").srcObject = null;
  $("qrScanner").classList.add("hidden");
  $("scanQr").textContent = "SCAN QR";
}

async function startQrScan() {
  if (window.RetiumAndroid?.scanQr) {
    $("scanQr").textContent = "SCANNING…";
    $("qrScanStatus").textContent = "Opening Android QR scanner…";
    window.RetiumAndroid.scanQr();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    toast("Camera access is unavailable", true);
    return;
  }
  try {
    let detector = null;
    if (typeof BarcodeDetector !== "undefined") {
      const supported = await BarcodeDetector.getSupportedFormats();
      if (supported.includes("qr_code")) detector = new BarcodeDetector({formats: ["qr_code"]});
    }
    qrStream = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: "environment"}}, audio: false});
    const video = $("qrVideo");
    video.srcObject = qrStream;
    await video.play();
    $("qrScanner").classList.remove("hidden");
    $("scanQr").textContent = "SCANNING…";

    const scan = async () => {
      if (!qrStream) return;
      try {
        let joinCode = null;
        if (detector) {
          const codes = await detector.detect(video);
          joinCode = codes.find((code) => code.rawValue?.trim().toUpperCase().startsWith("RTM1-"))?.rawValue.trim() || null;
        } else if (video.videoWidth > 0) {
          const canvas = document.createElement("canvas");
          const scale = Math.min(1, 960 / video.videoWidth);
          canvas.width = Math.round(video.videoWidth * scale);
          canvas.height = Math.round(video.videoHeight * scale);
          canvas.getContext("2d", {alpha: false}).drawImage(video, 0, 0, canvas.width, canvas.height);
          const frame = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.78));
          if (frame) {
            const response = await fetch("/api/team/qr/decode", {method: "POST", headers: {"content-type": "image/jpeg"}, body: frame});
            if (response.ok) joinCode = (await response.json()).join_code;
          }
        }
        if (joinCode) {
          $("joinCodeInput").value = joinCode;
          $("qrScanStatus").textContent = "Code found. Verifying over Reticulum…";
          await joinWithCode(joinCode, $("scanQr"));
          return;
        }
      } catch (error) {
        $("qrScanStatus").textContent = `Camera active · ${error.message}`;
      }
      qrScanTimer = setTimeout(scan, 300);
    };
    scan();
  } catch (error) {
    stopQrScan();
    toast(error.name === "NotAllowedError" ? "Camera permission was denied" : error.message, true);
  }
}

window.retiumAndroidQrResult = async (value) => {
  const joinCode = String(value || "").trim();
  if (!joinCode.toUpperCase().startsWith("RTM1-")) {
    $("scanQr").textContent = "SCAN QR";
    return toast("That is not a Reticom team QR code", true);
  }
  $("joinCodeInput").value = joinCode;
  $("qrScanStatus").textContent = "Code found. Verifying over Reticulum…";
  await joinWithCode(joinCode, $("scanQr"));
};

window.retiumAndroidQrError = (message) => {
  $("scanQr").textContent = "SCAN QR";
  if (message) toast(String(message), true);
};

function toast(message, error = false) {
  $("toast").textContent = message; $("toast").className = `toast show${error ? " error" : ""}`;
  setTimeout(() => $("toast").className = "toast", 2800);
}

function showDelivery(delivery) {
  const target = role === "gateway" ? $("commandDeliveryState") : $("deliveryState");
  const queued = delivery.status === "queued";
  const published = delivery.status === "published";
  const success = delivery.delivered || published;
  target.className = `delivery-state${role === "gateway" ? " command-delivery" : ""} ${queued ? "queued" : success ? "success" : "error"}`;
  target.textContent = queued
    ? `SAVED LOCALLY · ${delivery.queued || 1} QUEUED FOR RETICULUM`
    : published
    ? `PUBLISHED · SIGNED · ${delivery.packet_bytes} BYTES · ${shortHash(delivery.event_id)}`
    : delivery.delivered
      ? `PROVEN · ${delivery.rtt_ms} ms · ${delivery.packet_bytes} bytes · ${shortHash(delivery.event_id)}`
      : "DELIVERY FAILED";
}

function applyQueuedFieldEvent(data) {
  if (role !== "field" || data?.delivery?.status !== "queued" || !data.event) return;
  const receivedAt = Math.floor(Date.now() / 1000);
  const event = {
    ...data.event,
    network: {
      verified: false,
      queued: true,
      sender_hash: localIdentityHash,
      received_at: receivedAt,
      interface: "Local device · Reticulum outbox",
    },
  };
  fieldQueuedEvents = Number(data.delivery.queued || fieldQueuedEvents || 1);
  fieldEvents = withDisplayWaypointLabels([
    event,
    ...fieldEvents.filter((existing) => existing.id !== event.id),
  ]);
  saveFieldEventCache(fieldTeamDestination, fieldEvents);
  renderLatestMessages(fieldEvents);
  renderFieldOperators(fieldEvents);
  renderFieldFeed(combinedFieldFeedEvents());
  void renderFieldMap(fieldEvents, {preserveView: true});
}

async function send(payload, button) {
  button.disabled = true;
  try {
    const response = await fetch("/api/send", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Send failed");
    applyQueuedFieldEvent(data);
    showDelivery(data.delivery);
    toast(data.delivery.status === "queued"
      ? "Saved locally · queued for Reticulum"
      : data.delivery.status === "published"
        ? "Published to the Reticulum team feed"
        : "Reticulum delivery proven");
    return data;
  } catch (error) {
    toast(error.message, true);
    const target = role === "gateway" ? $("commandDeliveryState") : $("deliveryState");
    target.className = `delivery-state${role === "gateway" ? " command-delivery" : ""} error`;
    target.textContent = error.message;
  }
  finally { button.disabled = false; }
}

$("sendMessage").addEventListener("click", async (event) => {
  const message = $("message").value.trim(); if (!message) return toast("Enter a message", true);
  const result = await send({type: "chat.message", message}, event.currentTarget); if (result) $("message").value = "";
});
$("sendCommandMessage").addEventListener("click", async (event) => {
  const message = $("commandMessage").value.trim();
  if (!message) return toast("Enter a message", true);
  const result = await send({type: "chat.message", message}, event.currentTarget);
  if (result) $("commandMessage").value = "";
});

function enableDesktopEnterToSend(textareaId, buttonId) {
  $(textareaId).addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.repeat) return;
    if (!matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    event.preventDefault();
    $(buttonId).click();
  });
}

enableDesktopEnterToSend("commandMessage", "sendCommandMessage");
enableDesktopEnterToSend("message", "sendMessage");

function transmitPosition(button) {
  if (!navigator.geolocation) return toast("Geolocation unavailable", true);
  const original = button.textContent;
  button.disabled = true; button.textContent = "ACQUIRING FIX…";
  navigator.geolocation.getCurrentPosition(async (position) => {
    updateLocalMapPosition(position);
    const lat = Number(position.coords.latitude.toFixed(6));
    const lon = Number(position.coords.longitude.toFixed(6));
    const accuracy = Number(position.coords.accuracy.toFixed(1));
    button.disabled = false; button.textContent = original;
    await send({type: "position.updated", lat, lon, accuracy}, button);
  }, (error) => { button.disabled = false; button.textContent = original; toast(error.message, true); }, {enableHighAccuracy: true, timeout: 12000});
}
$("mapSharePosition").addEventListener("click", (event) => transmitPosition(event.currentTarget));

function updateMovementSharingUi() {
  const button = $("mapMovementToggle");
  const active = movementWatchId !== null || nativeMovementTracking;
  button.classList.toggle("tracking", active);
  button.setAttribute("aria-pressed", String(active));
  button.setAttribute("aria-label", active ? "Stop sharing movement trail" : "Start sharing movement trail");
  button.title = active ? "Automatic location updates are active" : "Resume automatic location updates";
  button.querySelector("span").textContent = active ? "AUTO" : "LOC";
}

function stopMovementSharing({announce = true, persist = true} = {}) {
  if (movementWatchId !== null) navigator.geolocation?.clearWatch(movementWatchId);
  const wasActive = movementWatchId !== null || nativeMovementTracking;
  movementWatchId = null;
  nativeMovementTracking = false;
  movementLastSent = null;
  movementPendingRelocation = null;
  movementSendPending = false;
  if (persist) saveAutomaticLocationPreference(false);
  updateMovementSharingUi();
  if (wasActive && announce) toast("Automatic location updates paused");
}

async function sendMovementFix(position) {
  updateLocalMapPosition(position);
  if (movementWatchId === null || movementSendPending) return;
  const point = [Number(position.coords.longitude.toFixed(6)), Number(position.coords.latitude.toFixed(6))];
  const accuracy = Number(position.coords.accuracy.toFixed(1));
  if (!Number.isFinite(accuracy) || accuracy <= 0 || accuracy > MAX_AUTOMATIC_ACCURACY_METERS) return;
  const now = Date.now();
  if (movementLastSent) {
    const elapsed = now - movementLastSent.sentAt;
    const moved = distanceMeters(movementLastSent.point, point);
    const movementThreshold = Math.max(10, Math.min(50, (movementLastSent.accuracy + accuracy) / 2));
    if (elapsed < 15000 || (moved < movementThreshold && elapsed < 60000)) return;
    const plausibleDistance = Math.max(75, 2 * (movementLastSent.accuracy + accuracy), 35 * elapsed / 1000);
    if (moved > plausibleDistance) {
      if (!movementPendingRelocation) {
        movementPendingRelocation = {point, accuracy, seenAt: now};
        return;
      }
      const confirmationElapsed = Math.max(1, (now - movementPendingRelocation.seenAt) / 1000);
      const confirmationDistance = Math.max(75, 2 * (movementPendingRelocation.accuracy + accuracy), 250 * confirmationElapsed);
      if (distanceMeters(movementPendingRelocation.point, point) > confirmationDistance) {
        movementPendingRelocation = {point, accuracy, seenAt: now};
        return;
      }
    }
  }
  movementPendingRelocation = null;
  movementSendPending = true;
  try {
    const data = await postJson("/api/send", {
      type: "position.updated",
      lat: point[1],
      lon: point[0],
      accuracy,
    });
    applyQueuedFieldEvent(data);
    movementLastSent = {point, accuracy, sentAt: now};
    showDelivery(data.delivery);
    await refreshFeed();
  } catch (error) {
    toast(`Movement fix not sent · ${error.message}`, true);
  } finally {
    movementSendPending = false;
  }
}

function startMovementSharing({announce = true, persist = true} = {}) {
  if (persist) saveAutomaticLocationPreference(true);
  if (window.RetiumAndroid?.setAutomaticLocationEnabled) {
    nativeMovementTracking = true;
    window.RetiumAndroid.setAutomaticLocationEnabled(true);
    updateMovementSharingUi();
    if (announce) toast("Automatic location updates active");
    return;
  }
  if (!navigator.geolocation) return toast("Geolocation unavailable", true);
  movementLastSent = null;
  movementPendingRelocation = null;
  movementWatchId = navigator.geolocation.watchPosition(
    (position) => sendMovementFix(position),
    (error) => {
      saveAutomaticLocationPreference(false);
      stopMovementSharing({announce: false, persist: false});
      toast(error.message, true);
    },
    {enableHighAccuracy: true, maximumAge: 5000, timeout: 20000},
  );
  updateMovementSharingUi();
  if (announce) toast("Automatic location updates active");
}

function syncAutomaticLocationSharing(joined) {
  if (!joined) {
    stopMovementSharing({announce: false, persist: false});
    return;
  }
  automaticLocationEnabled = readAutomaticLocationPreference();
  if (automaticLocationEnabled && movementWatchId === null && !nativeMovementTracking) {
    startMovementSharing({announce: false, persist: false});
  } else {
    updateMovementSharingUi();
  }
}

$("mapMovementToggle").addEventListener("click", () => {
  if (movementWatchId === null && !nativeMovementTracking) startMovementSharing();
  else stopMovementSharing();
});
$("stopWaypointNavigation").addEventListener("click", () => stopWaypointNavigation());
window.addEventListener("pagehide", () => {
  if (localMapWatchId !== null) navigator.geolocation?.clearWatch(localMapWatchId);
  localMapWatchId = null;
  stopMovementSharing({announce: false, persist: false});
  stopWaypointNavigation(false);
});

function setPttState(state) {
  document.querySelectorAll(".ptt-button").forEach((button) => {
    const hint = button.querySelector("small");
    if (!button.dataset.idleHint) button.dataset.idleHint = hint.textContent;
    button.classList.toggle("transmitting", state === "recording" || state === "live");
    button.classList.toggle("live-streaming", state === "live" || state === "cutting");
    button.classList.toggle("canceling", state === "canceling" || state === "cutting");
    button.disabled = state === "sending";
    const idleLabel = button.dataset.idleLabel || "HOLD TO BROADCAST";
    button.querySelector("b").textContent = state === "live" ? "LIVE · RELEASE TO END" : state === "cutting" ? "RELEASE TO CUT" : state === "recording" ? "RELEASE TO SEND" : state === "canceling" ? "RELEASE TO DISCARD" : state === "sending" ? "SENDING OVER RNS…" : idleLabel;
    hint.textContent = state === "live" ? "ENCRYPTED RNS CHANNEL · SWIPE TO CUT" : state === "cutting" ? "ALREADY SENT AUDIO CANNOT BE UNDONE" : state === "recording" ? "SWIPE LEFT OR UP TO CANCEL" : state === "canceling" ? "RECORDING WILL NOT BE SENT" : state === "sending" ? "AUTHENTICATED RETICULUM LINK" : button.dataset.idleHint;
  });
}

function releasePttStream() {
  pttStream?.getTracks().forEach((track) => track.stop());
  pttStream = null;
}

async function openPttStream() {
  const preferredConstraints = {
    audio: {
      channelCount: {ideal: 1},
      echoCancellation: {ideal: true},
      noiseSuppression: {ideal: true},
      autoGainControl: {ideal: true},
    },
  };
  try {
    return await navigator.mediaDevices.getUserMedia(preferredConstraints);
  } catch (error) {
    if (!["OverconstrainedError", "ConstraintNotSatisfiedError", "TypeError"].includes(error.name)) throw error;
    return navigator.mediaDevices.getUserMedia({audio: true});
  }
}

function microphoneErrorMessage(error) {
  if (error?.name === "NotAllowedError" || error?.name === "SecurityError") return "Microphone permission was denied";
  if (error?.name === "NotReadableError" || error?.name === "AbortError") return "Could not open microphone · check the active audio device and try again";
  if (error?.name === "NotFoundError") return "No microphone was found";
  return error?.message || "Could not start the microphone";
}

async function startPtt(button, pointerId) {
  if (pttRecorder || pttStarting || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    if (!navigator.mediaDevices?.getUserMedia) toast("Microphone recording is unavailable", true);
    return;
  }
  pttStarting = true;
  pttHoldActive = true;
  pttShouldSend = true;
  pttButton = button;
  pttPrivateRecipient = button.dataset.privateRecipient || null;
  try {
    if (pointerId != null) button.setPointerCapture(pointerId);
    releasePttStream();
    pttStream = await openPttStream();
    if (!pttHoldActive) {
      releasePttStream();
      toast("Microphone ready · hold again to broadcast");
      return;
    }
    const preferred = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((type) => MediaRecorder.isTypeSupported(type));
    pttRecorder = preferred ? new MediaRecorder(pttStream, {mimeType: preferred, audioBitsPerSecond: 16000}) : new MediaRecorder(pttStream);
    pttChunks = [];
    pttMode = !pttPrivateRecipient && liveVoiceReady
      && liveVoiceSocket?.readyState === WebSocket.OPEN
      && ["audio/webm;codecs=opus", "audio/ogg;codecs=opus"].includes(pttRecorder.mimeType.toLowerCase().replaceAll(" ", ""))
      ? "live"
      : "recorded";
    pttLiveStreamId = pttMode === "live" ? crypto.randomUUID() : null;
    pttLiveFailed = false;
    pttLiveSendChain = Promise.resolve();
    if (pttMode === "live") {
      try {
        liveVoiceSocket.send(JSON.stringify({action: "start", stream_id: pttLiveStreamId, mime_type: pttRecorder.mimeType}));
      } catch {
        pttLiveFailed = true;
      }
    }
    pttRecorder.addEventListener("dataavailable", (event) => {
      if (!event.data.size) return;
      pttChunks.push(event.data);
      if (pttMode !== "live" || pttLiveFailed) return;
      pttLiveSendChain = pttLiveSendChain.then(async () => {
        if (liveVoiceSocket?.readyState !== WebSocket.OPEN || liveVoiceSocket.bufferedAmount > 256_000) {
          pttLiveFailed = true;
          return;
        }
        liveVoiceSocket.send(await event.data.arrayBuffer());
      }).catch(() => { pttLiveFailed = true; });
    });
    pttRecorder.addEventListener("stop", finishPtt);
    pttStartedAt = performance.now();
    pttRecorder.start(250);
    setPttState(pttMode === "live" ? "live" : "recording");
    pttStopTimer = setTimeout(() => stopPtt(true), 8000);
  } catch (error) {
    pttHoldActive = false;
    pttRecorder = null;
    releasePttStream();
    setPttState("idle");
    toast(microphoneErrorMessage(error), true);
  } finally {
    pttStarting = false;
  }
}

function stopPtt(sendClip) {
  pttHoldActive = false;
  if (pttStopTimer) clearTimeout(pttStopTimer);
  pttStopTimer = null;
  if (!pttRecorder) return;
  pttShouldSend = sendClip;
  if (pttRecorder.state !== "inactive") pttRecorder.stop();
}

async function finishPtt() {
  const recorder = pttRecorder;
  const mode = pttMode;
  const liveStreamId = pttLiveStreamId;
  const privateRecipient = pttPrivateRecipient;
  const durationMs = Math.max(100, Math.min(10000, Math.round(performance.now() - pttStartedAt)));
  const shouldSend = pttShouldSend;
  const mimeType = (recorder?.mimeType || "audio/webm").split(";", 1)[0];
  const blob = new Blob(pttChunks, {type: mimeType});
  releasePttStream();
  pttRecorder = null;
  pttChunks = [];
  pttShouldSend = true;
  await pttLiveSendChain;
  const liveSucceeded = mode === "live" && !pttLiveFailed && liveVoiceSocket?.readyState === WebSocket.OPEN;
  if (mode === "live" && liveVoiceSocket?.readyState === WebSocket.OPEN && liveStreamId) {
    liveVoiceSocket.send(JSON.stringify({action: liveSucceeded && shouldSend ? "end" : "cancel", stream_id: liveStreamId}));
  }
  pttMode = "recorded";
  pttLiveStreamId = null;
  pttLiveFailed = false;
  pttLiveSendChain = Promise.resolve();
  pttPrivateRecipient = null;
  if (liveSucceeded) {
    setPttState("idle");
    toast(shouldSend ? "Live Reticulum broadcast ended" : "Live transmission cut · sent audio cannot be undone");
    pttButton = null;
    return;
  }
  if (!shouldSend || blob.size === 0) {
    setPttState("idle");
    if (!shouldSend) toast(mode === "live" ? "Live transmission cut" : "Voice recording discarded");
    pttButton = null;
    return;
  }
  setPttState("sending");
  try {
    const endpoint = privateRecipient
      ? `/api/private/ptt?duration_ms=${durationMs}&recipient_hash=${encodeURIComponent(privateRecipient)}`
      : `/api/ptt?duration_ms=${durationMs}`;
    const response = await fetch(endpoint, {method: "POST", headers: {"content-type": mimeType}, body: blob});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || (privateRecipient ? "Private voice failed" : "Voice broadcast failed"));
    if (!privateRecipient) {
      applyQueuedFieldEvent(data);
      showDelivery(data.delivery);
    }
    toast(privateRecipient
      ? "Private voice accepted by Command mailbox"
      : data.delivery.status === "queued"
        ? "Voice saved locally · queued for Reticulum"
        : data.delivery.status === "published"
          ? "Voice published to the Reticulum team feed"
          : "Voice broadcast proven over Reticulum");
    if (data.delivery.status !== "queued") requestVoiceTranscript(data.event?.clip_id, false);
    if (privateRecipient) await refreshPrivateChat();
    else await refreshFeed();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setPttState("idle");
    pttButton = null;
  }
}

document.querySelectorAll(".ptt-button").forEach((button) => {
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    pttGesture = {pointerId: event.pointerId, startX: event.clientX, startY: event.clientY};
    pttCancelArmed = false;
    startPtt(button, event.pointerId);
  });
  button.addEventListener("pointermove", (event) => {
    if (!pttGesture || event.pointerId !== pttGesture.pointerId || pttCancelArmed) return;
    if (event.clientX - pttGesture.startX <= -64 || event.clientY - pttGesture.startY <= -64) {
      pttCancelArmed = true;
      if (pttHoldActive) setPttState(pttMode === "live" ? "cutting" : "canceling");
    }
  });
  button.addEventListener("pointerup", (event) => {
    event.preventDefault();
    const shouldSend = !pttCancelArmed;
    pttGesture = null;
    pttCancelArmed = false;
    stopPtt(shouldSend);
  });
  button.addEventListener("pointercancel", () => {
    pttGesture = null;
    pttCancelArmed = false;
    stopPtt(false);
  });
  button.addEventListener("contextmenu", (event) => event.preventDefault());
});

async function removeOwnMessage(button) {
  if (button.dataset.confirm !== "true") {
    button.dataset.confirm = "true";
    button.textContent = "CONFIRM";
    setTimeout(() => {
      if (button.isConnected) {
        button.dataset.confirm = "false";
        button.textContent = "REMOVE";
      }
    }, 8000);
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`/api/messages/${encodeURIComponent(button.dataset.messageId)}`, {method: "DELETE"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Message removal failed");
    if (role === "gateway") {
      toast("Command message removed · signed update published");
      await refresh();
    } else {
      fieldEvents = fieldEvents.filter((event) => event.id !== button.dataset.messageId);
      renderLatestMessages(fieldEvents);
      renderFieldFeed(fieldEvents);
      renderFieldOperators(fieldEvents);
      toast("Message removed · signed over Reticulum");
      await refreshFeed();
    }
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

function clearVoiceUiCaches() {
  if (activeVoicePlayer) {
    activeVoicePlayer.sequence += 1;
    activeVoicePlayer.audio.pause();
    activeVoicePlayer.audio.removeAttribute("src");
    activeVoicePlayer = null;
  }
  for (const assetRequest of voiceAssetCache.values()) {
    Promise.resolve(assetRequest).then((asset) => URL.revokeObjectURL(asset.url)).catch(() => {});
  }
  voiceAssetCache.clear();
  voiceTranscriptCache.clear();
  voiceTranscriptRequests.clear();
}

[$("timeline"), $("commandLatestMessages"), $("latestMessages"), $("fieldFeedList")].forEach((list) => {
  list.addEventListener("click", (event) => {
    if (list.id === "latestMessages" && performance.now() < suppressMapMessageClickUntil) {
      event.preventDefault();
      return;
    }
    const readButton = event.target.closest(".map-message-read");
    if (readButton) {
      const card = readButton.closest(".latest-message[data-map-message-id]");
      finishMapMessageCard(card, card?.dataset.mapMessageId, 1);
      return;
    }
    const voicePlayer = event.target.closest(".voice-player");
    if (voicePlayer) {
      toggleVoicePlayer(voicePlayer);
      return;
    }
    const button = event.target.closest(".message-delete");
    if (button) removeOwnMessage(button);
  });
});

$("latestMessages").addEventListener("pointerdown", beginMapMessageSwipe);
$("latestMessages").addEventListener("pointermove", moveMapMessageSwipe);
$("latestMessages").addEventListener("pointerup", (event) => endMapMessageSwipe(event));
$("latestMessages").addEventListener("pointercancel", (event) => endMapMessageSwipe(event, true));

$("incomingAlertsToggle").addEventListener("click", () => {
  if (incomingAlertsEnabled && incomingAudioReady) muteIncomingAlerts();
  else armIncomingAlerts();
});

[$("commandMapStyleToggle"), $("fieldMapStyleToggle")].forEach((button) => {
  button.addEventListener("click", toggleMapStyle);
});
$("mapGridSettingsToggle").addEventListener("click", toggleMapGrid);
[$("commandGridReference"), $("fieldGridReference")].forEach((button) => {
  button.addEventListener("click", () => copyGridReference(button));
});
updateMapStyleUi();
updateMapGridUi();
window.RetiumAndroid?.setIncomingAlertsEnabled?.(incomingAlertsEnabled);

function setFieldPage(page) {
  closeMapRadial(fieldMapEditor);
  if (page !== "map" && fieldMapEditor?.drawMode) stopMapDrawing(fieldMapEditor);
  $("fieldMapPage").classList.toggle("hidden", page !== "map");
  $("fieldFeedPage").classList.toggle("hidden", page !== "feed");
  $("fieldOpsPage").classList.toggle("hidden", page !== "ops");
  document.querySelectorAll("[data-field-page]").forEach((item) => {
    const selected = item.dataset.fieldPage === page;
    item.classList.toggle("selected", selected);
    item.setAttribute("aria-selected", String(selected));
  });
  if (page === "map" && fieldMap) setTimeout(() => fieldMap.resize(), 0);
}

document.querySelectorAll("[data-field-page]").forEach((button) => {
  button.addEventListener("click", () => setFieldPage(button.dataset.fieldPage));
});

$("opsOperatorList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-private-peer]");
  if (button) openPrivateChat(button.dataset.privatePeer);
});

$("closePrivateChat").addEventListener("click", closePrivateChat);

$("privateChatComposer").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activePrivatePeer) return;
  const message = $("privateChatMessage").value.trim();
  if (!message) return;
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  $("privateChatStatus").textContent = "SENDING OVER PRIVATE RETICULUM LINK…";
  try {
    const data = await postJson("/api/private/messages", {
      recipient_hash: activePrivatePeer.hash,
      message,
    });
    $("privateChatMessage").value = "";
    $("privateChatStatus").textContent = `COMMAND MAILBOX ACCEPTED · ${Math.round(data.delivery?.rtt_ms || 0)} MS`;
    await refreshPrivateChat();
  } catch (error) {
    $("privateChatStatus").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
    $("privateChatMessage").focus();
  }
});

$("privateChatMessage").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && matchMedia("(pointer: fine)").matches) {
    event.preventDefault();
    $("privateChatComposer").requestSubmit();
  }
});

$("leaveTeam").addEventListener("click", async () => {
  if (!leaveArmed) {
    leaveArmed = true;
    $("leaveTeam").textContent = "PRESS AGAIN TO LEAVE";
    setTimeout(() => { leaveArmed = false; $("leaveTeam").textContent = "LEAVE TEAM"; }, 4000);
    return;
  }
  try {
    stopMovementSharing({announce: false, persist: false});
    const data = await postJson("/api/team/leave", {});
    leaveArmed = false;
    $("leaveTeam").textContent = "LEAVE TEAM";
    $("userSettingsPanel").classList.add("hidden");
    renderTeam(data.team);
    toast("Left team · identity kept on this device");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});

$("createTeam").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const modules = $("createTasksModule").checked ? ["tasks"] : [];
    const team = await postJson("/api/team/create", {name: $("teamNameInput").value, modules});
    renderTeam(team);
    toast("Team created · announce sent");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("fieldCreateTeam").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const name = $("fieldTeamNameInput").value.trim();
  if (!name) return toast("Enter a team name", true);
  button.disabled = true;
  try {
    const modules = $("fieldCreateTasksModule").checked ? ["tasks"] : [];
    const team = await postJson("/api/team/create", {
      name,
      modules,
      callsign: $("joinCallsign").value,
    });
    renderUser({...currentUser, callsign: $("joinCallsign").value.trim() || currentUser.callsign});
    renderTeam(team);
    if (team.delivery) showDelivery(team.delivery);
    toast("Team created · this device is now the Reticulum host");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("toggleTasksModule").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const modules = currentTeamModules.includes("tasks") ? [] : ["tasks"];
    const team = await postJson("/api/team/modules", {modules});
    renderTeam(team);
    toast(modules.length ? "Tasks module enabled · announce sent" : "Tasks module disabled");
    if (modules.length) await refreshTasks();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("toggleEveryoneAdmin").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const team = await postJson("/api/team/permissions", {everyone_admin: !currentEveryoneAdmin});
    renderTeam(team);
    toast(currentEveryoneAdmin ? "Everyone is admin · signed announce sent" : "Team admin mode disabled");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("fieldToggleTasksModule").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  $("adminSettingsStatus").textContent = "Applying module change over Reticulum…";
  try {
    const modules = currentTeamModules.includes("tasks") ? [] : ["tasks"];
    const team = await postJson("/api/team/modules", {modules});
    renderTeam(team);
    $("adminSettingsStatus").textContent = modules.length
      ? "Tasks module enabled and announced to the team."
      : "Tasks module disabled and announced to the team.";
    toast(modules.length ? "Tasks module enabled" : "Tasks module disabled");
    if (modules.length) await refreshTasks();
  } catch (error) {
    $("adminSettingsStatus").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("fieldToggleEveryoneAdmin").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  $("adminSettingsStatus").textContent = "Applying team permission…";
  try {
    const team = await postJson("/api/team/permissions", {everyone_admin: !currentEveryoneAdmin});
    renderTeam(team);
    $("adminSettingsStatus").textContent = currentEveryoneAdmin
      ? "Every verified team member now has admin controls."
      : "Everyone-admin mode disabled.";
    toast(currentEveryoneAdmin ? "Everyone is admin" : "Everyone-admin mode disabled");
  } catch (error) {
    $("adminSettingsStatus").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("fieldClearMessageHistory").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (button.dataset.confirm !== "true") {
    button.dataset.confirm = "true";
    button.textContent = "PRESS AGAIN TO CLEAR";
    setTimeout(() => {
      if (button.isConnected && button.dataset.confirm === "true") {
        button.dataset.confirm = "false";
        button.textContent = "CLEAR COMMS HISTORY";
      }
    }, 8000);
    return;
  }
  button.disabled = true;
  $("adminSettingsStatus").textContent = "Clearing communications on the team host…";
  try {
    const response = await fetch("/api/messages/history", {method: "DELETE"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Communication cleanup failed");
    clearVoiceUiCaches();
    button.dataset.confirm = "false";
    button.textContent = "CLEAR COMMS HISTORY";
    const count = Number(data.cleared?.events || 0);
    $("adminSettingsStatus").textContent = `${count} communication event${count === 1 ? "" : "s"} removed from the host.`;
    toast(`${count} communication event${count === 1 ? "" : "s"} cleared`);
    await refreshFeed();
  } catch (error) {
    $("adminSettingsStatus").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("adminCopyJoinCode").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(currentTeamJoinCode);
    toast("Join code copied");
  } catch {
    toast("Copy unavailable · select the code manually", true);
  }
});

$("createTask").addEventListener("click", async (event) => {
  const title = $("taskTitle").value.trim();
  if (!title) return toast("Enter a task", true);
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await postJson("/api/tasks", {title, assignee: $("taskAssignee").value});
    $("taskTitle").value = "";
    toast("Task added");
    await refreshTasks();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("commandTaskList").addEventListener("click", async (event) => {
  const button = event.target.closest(".task-delete");
  if (!button) return;
  if (button.dataset.confirm !== "true") {
    button.dataset.confirm = "true";
    button.textContent = "CONFIRM";
    setTimeout(() => {
      if (button.isConnected) {
        button.dataset.confirm = "false";
        button.textContent = "DELETE";
      }
    }, 8000);
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`/api/tasks/${encodeURIComponent(button.dataset.taskId)}`, {method: "DELETE"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Task deletion failed");
    toast(data.deleted?.completed_at ? "Completed assignment deleted" : "Open assignment deleted");
    await refreshTasks();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
});

$("clearMessageHistory").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (button.dataset.confirm !== "true") {
    button.dataset.confirm = "true";
    button.textContent = "CONFIRM CLEAR";
    setTimeout(() => {
      if (button.isConnected) {
        button.dataset.confirm = "false";
        button.textContent = "CLEAR COMMS";
      }
    }, 8000);
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch("/api/messages/history", {method: "DELETE"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Communication cleanup failed");
    clearVoiceUiCaches();
    button.dataset.confirm = "false";
    button.textContent = "CLEAR COMMS";
    const count = Number(data.cleared?.events || 0);
    toast(`${count} communication event${count === 1 ? "" : "s"} cleared`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("fieldTaskList").addEventListener("click", async (event) => {
  const button = event.target.closest(".task-complete");
  if (!button) return;
  button.disabled = true;
  try {
    const data = await postJson(`/api/tasks/${encodeURIComponent(button.dataset.taskId)}/complete`, {});
    showDelivery(data.delivery);
    toast("Completion proven over Reticulum");
    await refreshTasks();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
});

$("userSettingsToggle").addEventListener("click", () => {
  const panel = $("userSettingsPanel");
  const willOpen = panel.classList.contains("hidden");
  $("adminSettingsPanel").classList.add("hidden");
  $("adminSettingsToggle").setAttribute("aria-expanded", "false");
  panel.classList.toggle("hidden", !willOpen);
  $("userSettingsToggle").setAttribute("aria-expanded", String(willOpen));
  if (willOpen) {
    renderUser(currentUser);
    loadTtsVoiceSettings();
    refreshOfflineMaps();
    loadNetworkSettings();
  }
});

$("adminSettingsToggle").addEventListener("click", () => {
  const panel = $("adminSettingsPanel");
  const willOpen = panel.classList.contains("hidden");
  $("userSettingsPanel").classList.add("hidden");
  $("userSettingsToggle").setAttribute("aria-expanded", "false");
  panel.classList.toggle("hidden", !willOpen);
  $("adminSettingsToggle").setAttribute("aria-expanded", String(willOpen));
  if (willOpen) {
    $("adminSettingsStatus").textContent = currentTeamHosted
      ? "This device controls the hosted team directly."
      : "Admin changes use an identified Reticulum Link to the team host.";
  }
});

$("offlineMapDetail").addEventListener("change", updateOfflineMapEstimate);

$("downloadOfflineMap").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const bounds = visibleOfflineBounds();
  if (!bounds) return toast("Open the map and choose an area first", true);
  button.disabled = true;
  button.textContent = "STARTING DOWNLOAD…";
  try {
    const data = await postJson("/api/offline-maps", {bounds, max_zoom: Number($("offlineMapDetail").value || 14)});
    toast(`Downloading ${data.pack.tile_count} map tiles · keep Reticom open`);
    await refreshOfflineMaps();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.innerHTML = "DOWNLOAD CURRENT VIEW <span>↓</span>";
    updateOfflineMapEstimate();
  }
});

$("offlineMapList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-offline-map]");
  if (!button) return;
  if (button.dataset.confirm !== "true") {
    button.dataset.confirm = "true";
    button.textContent = "CONFIRM";
    setTimeout(() => {
      if (button.isConnected) {
        button.dataset.confirm = "false";
        button.textContent = "DELETE";
      }
    }, 8000);
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`/api/offline-maps/${encodeURIComponent(button.dataset.deleteOfflineMap)}`, {method: "DELETE"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Offline map could not be deleted");
    toast(`${data.deleted.name} deleted from offline maps`);
    await refreshOfflineMaps();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
});

$("closeUserSettings").addEventListener("click", () => {
  renderUser(currentUser);
  $("userSettingsPanel").classList.add("hidden");
  $("userSettingsToggle").setAttribute("aria-expanded", "false");
});

$("closeAdminSettings").addEventListener("click", () => {
  $("adminSettingsPanel").classList.add("hidden");
  $("adminSettingsToggle").setAttribute("aria-expanded", "false");
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("userSettingsPanel").classList.contains("hidden")) {
    $("closeUserSettings").click();
  }
  if (event.key === "Escape" && !$("adminSettingsPanel").classList.contains("hidden")) {
    $("closeAdminSettings").click();
  }
});

document.querySelector(".icon-picker").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-icon]");
  if (!button) return;
  selectedUserIcon = button.dataset.icon;
  document.querySelectorAll(".icon-picker button").forEach((item) => {
    const selected = item === button;
    item.classList.toggle("selected", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
});

document.querySelector(".color-picker").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-color]");
  if (!button) return;
  selectedUserColor = button.dataset.color;
  document.querySelectorAll(".color-picker button").forEach((item) => {
    const selected = item === button;
    item.classList.toggle("selected", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
});

$("ttsVoiceSelect").addEventListener("change", (event) => {
  const select = event.currentTarget;
  try {
    if (!window.RetiumAndroid.setTtsVoice(select.value)) throw new Error("Voice unavailable");
    $("ttsVoiceStatus").textContent = "Selected voice saved on this phone.";
    toast("TTS voice changed");
  } catch (error) {
    $("ttsVoiceStatus").textContent = error.message;
    toast("TTS voice could not be changed", true);
  }
});

$("previewTtsVoice").addEventListener("click", (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    if (!window.RetiumAndroid.previewTtsVoice($("ttsVoiceSelect").value)) throw new Error("Preview unavailable");
    $("ttsVoiceStatus").textContent = "Playing the selected voice…";
  } catch {
    toast("TTS preview could not play", true);
  } finally {
    setTimeout(() => { button.disabled = false; }, 1200);
  }
});

$("saveUserSettings").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const data = await postJson("/api/user/settings", {callsign: $("settingsCallsign").value, icon: selectedUserIcon, color: selectedUserColor});
    renderUser(data.user);
    $("userSettingsPanel").classList.add("hidden");
    $("userSettingsToggle").setAttribute("aria-expanded", "false");
    if (data.delivery) showDelivery(data.delivery);
    toast(data.delivery?.status === "queued"
      ? "Profile saved locally · queued for Reticulum"
      : data.delivery
        ? "Profile updated · Reticulum proof received"
        : data.warning
          ? "Saved locally · team destination not reached"
          : "User settings saved");
    await refreshTasks();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("advancedNetworkSettings").addEventListener("toggle", (event) => {
  if (event.currentTarget.open) loadNetworkSettings();
});

$("saveNetworkSettings").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const data = await putJson("/api/network/settings", {
      host: $("customNodeHost").value.trim(),
      port: Number($("customNodePort").value || 4242),
    });
    const custom = data.custom_node;
    await loadNetworkSettings();
    toast(custom ? `Internet node saved · ${custom.host}:${custom.port}` : "Auto Internet connections restored · restart Reticom to apply");
  } catch (error) {
    $("networkSettingsStatus").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("copyJoinCode").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("commandJoinCode").textContent);
    toast("Join code copied");
  } catch {
    toast("Copy unavailable · select the code manually", true);
  }
});

$("toggleQr").addEventListener("click", () => {
  const willShow = $("qrPanel").classList.contains("hidden");
  $("qrPanel").classList.toggle("hidden", !willShow);
  $("toggleQr").textContent = willShow ? "HIDE QR" : "SHOW QR";
});

$("joinTeam").addEventListener("click", (event) => joinWithCode($("joinCodeInput").value, event.currentTarget));
$("scanQr").addEventListener("click", () => qrStream ? stopQrScan() : startQrScan());
$("stopQr").addEventListener("click", stopQrScan);
document.addEventListener("visibilitychange", () => { if (document.hidden) stopQrScan(); });
window.addEventListener("online", () => {
  if (role !== "field" || fieldMap) return;
  fieldMapReady = null;
  void renderFieldMap(fieldEvents);
});

$("refreshNearby").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/team/nearby", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Scan failed");
    renderNearby(data.teams);
    toast(data.teams.length ? `${data.teams.length} team${data.teams.length === 1 ? "" : "s"} heard` : "No nearby team heard yet");
  } catch (error) {
    toast(error.message, true);
  }
});

$("nearbyTeams").addEventListener("click", (event) => {
  const button = event.target.closest(".nearby-join");
  if (button) joinWithCode(button.dataset.code, button);
});

function connectLiveStream() {
  if (role !== "gateway") return;
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/api/live`);
  socket.addEventListener("message", (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type === "event.received") refresh();
  });
  socket.addEventListener("close", () => setTimeout(connectLiveStream, 1500));
}

refresh().then(connectLiveStream);
setInterval(refresh, 15000);
