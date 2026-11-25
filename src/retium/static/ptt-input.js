// Hardware-friendly PTT input rules shared by Command and Field's WebView.
// F13-F24 are common mappings for dedicated USB/Bluetooth PTT switches;
// F9 remains a usable default on ordinary laptop keyboards.
export const HARDWARE_PTT_CODES = Object.freeze([
  "F9",
  "F13", "F14", "F15", "F16", "F17", "F18",
  "F19", "F20", "F21", "F22", "F23", "F24",
]);

export function isEditablePttTarget(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(String(target.tagName || "").toUpperCase());
}

export function isHardwarePttKeyboardEvent(event, target = event?.target) {
  if (!event || (event.type !== "keydown" && event.type !== "keyup")) return false;
  if (event.ctrlKey || event.altKey || event.metaKey || isEditablePttTarget(target)) return false;
  return HARDWARE_PTT_CODES.includes(event.code || event.key);
}
