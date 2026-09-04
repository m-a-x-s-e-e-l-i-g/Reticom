export const TACTICAL_MARKERS = [
  {type: "casualty", label: "Casualty", symbol: "MED", color: "#d5d1bd", group: "Medical"},
  {type: "medevac", label: "MEDEVAC", symbol: "EVAC", color: "#d5d1bd", group: "Medical"},
  {type: "evac-point", label: "Evac point", symbol: "EVAC", color: "#d5d1bd", group: "Medical"},
  {type: "medical-point", label: "Medical point", symbol: "MED", color: "#d5d1bd", group: "Medical"},

  {type: "rally-point", label: "Rally point", symbol: "RP", color: "#a79669", group: "Control"},
  {type: "checkpoint", label: "Checkpoint", symbol: "CP", color: "#899a78", group: "Control"},
  {type: "landing-zone", label: "Landing zone", symbol: "LZ", color: "#899a78", group: "Control"},
  {type: "search-area", label: "Search area", symbol: "SAR", color: "#a79669", group: "Control"},
  {type: "hold-line", label: "Hold line", symbol: "HOLD", color: "#a79669", group: "Control"},

  {type: "contact", label: "Contact", symbol: "ENY", color: "#c46855", group: "Threat / hazard"},
  {type: "possible-movement", label: "Possible movement", symbol: "?", color: "#c79558", group: "Threat / hazard"},
  {type: "drone-spotted", label: "Drone spotted", symbol: "UAV", color: "#c46855", group: "Threat / hazard"},
  {type: "fire-smoke", label: "Fire / smoke", symbol: "FIRE", color: "#c46855", group: "Threat / hazard"},
  {type: "road-blocked", label: "Road blocked", symbol: "OBS", color: "#c46855", group: "Threat / hazard"},
  {type: "route-compromised", label: "Route compromised", symbol: "RTE", color: "#c46855", group: "Threat / hazard"},
  {type: "bridge-damaged", label: "Bridge damaged", symbol: "BRG", color: "#c46855", group: "Threat / hazard"},

  {type: "unit-moving", label: "Unit moving", symbol: "MOV", color: "#718d80", group: "Movement / support"},
  {type: "vehicle-disabled", label: "Vehicle disabled", symbol: "VEH", color: "#c79558", group: "Movement / support"},
  {type: "radio-dead-zone", label: "Radio dead zone", symbol: "COM", color: "#9d7892", group: "Movement / support"},
  {type: "supply-cache", label: "Supply cache", symbol: "SUP", color: "#a79669", group: "Movement / support"},
  {type: "water-point", label: "Water point", symbol: "H2O", color: "#668995", group: "Movement / support"},
  {type: "last-seen", label: "Last seen", symbol: "LKP", color: "#c79558", group: "Movement / support"},
];

export const TACTICAL_UNIT_MARKERS = [
  {type: "car", label: "Car", symbol: "CAR", color: "#668995", group: "Units"},
  {type: "tank", label: "Tank", symbol: "TANK", color: "#a79669", group: "Units"},
  {type: "helicopter", label: "Helicopter", symbol: "HELO", color: "#718d80", group: "Units"},
  {type: "airplane", label: "Airplane", symbol: "AIR", color: "#78859d", group: "Units"},
];

export const TACTICAL_PALETTE_MARKERS = [...TACTICAL_MARKERS, ...TACTICAL_UNIT_MARKERS];

const TACTICAL_MARKERS_BY_TYPE = new Map(TACTICAL_PALETTE_MARKERS.map((item) => [item.type, item]));

export const TACTICAL_MARKER_TYPES = TACTICAL_MARKERS.map((item) => item.type);

export function tacticalMarker(type) {
  return TACTICAL_MARKERS_BY_TYPE.get(String(type || "")) || null;
}

export function tacticalMarkerGroups() {
  const groups = new Map();
  TACTICAL_PALETTE_MARKERS.forEach((item) => {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  });
  return [...groups.entries()].map(([label, markers]) => ({label, markers}));
}
