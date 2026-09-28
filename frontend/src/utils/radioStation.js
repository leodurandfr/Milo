/**
 * True for a station the user added, as opposed to a RadioBrowser one.
 *
 * An added station is a favorite for as long as it exists: its heart cannot
 * release it, and it is deleted from Settings. The prefix is the backend's
 * (`StationDataService.is_custom_station`).
 */
export function isCustomStation(stationId) {
  return typeof stationId === 'string' && stationId.startsWith('custom_');
}
