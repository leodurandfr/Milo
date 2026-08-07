/**
 * How often a view that shows the WiFi signal arc re-reads it.
 *
 * The backend does not push RSSI (subscribing to it woke a full status re-read
 * several times a minute for an icon that almost never changed), so each view
 * polls for exactly as long as it displays the arc. 5 s tracks NetworkManager's
 * own sampling cadence, and the arc has four steps (25/50/75) — a finer poll
 * could not draw anything a coarser one misses.
 */
export const WIFI_SIGNAL_POLL_MS = 5000;
