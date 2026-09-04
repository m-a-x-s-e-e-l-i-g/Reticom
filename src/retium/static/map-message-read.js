export const MAP_MESSAGE_READ_LIMIT = 256;

export function mapMessageReadStorageKey(destination) {
  return `retium.mapMessagesRead.${destination || "unjoined"}`;
}

export function parseReadMessageIds(value, limit = MAP_MESSAGE_READ_LIMIT) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((id) => typeof id === "string" && id.length > 0))].slice(-limit);
}

export function mapOverlayMessages(events, {localIdentityHash = "", readIds = null, limit = 3} = {}) {
  if (!Array.isArray(events)) return [];
  return events
    .filter((event) => ["chat.message", "ptt.broadcast"].includes(event?.type))
    .filter((event) => !localIdentityHash || event.network?.sender_hash !== localIdentityHash)
    .filter((event) => !readIds?.has(event.id))
    .slice(0, limit);
}

export function shouldDismissMapMessage({deltaX, deltaY, width, elapsedMs}) {
  const horizontalDistance = Math.abs(Number(deltaX) || 0);
  const verticalDistance = Math.abs(Number(deltaY) || 0);
  const cardWidth = Math.max(1, Number(width) || 1);
  const duration = Math.max(1, Number(elapsedMs) || 1);
  if (horizontalDistance <= verticalDistance) return false;

  const distanceThreshold = Math.min(96, Math.max(52, cardWidth * 0.22));
  const deliberateFlick = horizontalDistance >= 36 && horizontalDistance / duration >= 0.5;
  return horizontalDistance >= distanceThreshold || deliberateFlick;
}
