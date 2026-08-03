// frontend/src/components/gallery/canvasHttp.js
/**
 * The canvas's stand-in for the backend.
 *
 * The gallery mounts real views — radio's FavoritesView, the Music Library
 * home, the podcast home — and those views run real stores, which fetch. Two
 * things follow, and this file is both of them.
 *
 * **The page may not write to the appliance.** `CanvasApp` already replaces
 * `sendCommand` for that reason; every source that acts through the store is
 * covered by it, but a browser acts through `apiCall` directly — radio's
 * playStation POSTs, a playlist is created, a share is mounted. So the four
 * mutating verbs are stubbed here too, and report to the event log instead.
 * The rule is worth stating once rather than per store: a documentation page
 * has no business changing what is playing in the next room.
 *
 * **A read is served from a fixture, not from the unit.** Pointing GET at the
 * real backend would make the page show this unit's favourites and this unit's
 * albums — so "radio with no favourites" would be unreachable on a unit that
 * has some, which is exactly the permutation the page exists to show. Fixtures
 * are declared per scenario in sources.js as *backend responses*, so the real
 * store parses them by its real code path: seeding `favoriteStations` directly
 * would skip `loadStations` and stop testing the shape it expects.
 *
 * An unmatched GET returns not-ok — which every store here treats as "leave
 * what you have" — and is reported as `unstubbed`. That is the important half:
 * a view that grew a new call shows up in the event log as a gap to fill,
 * rather than silently rendering an empty state that looks deliberate.
 *
 * Installed once for the whole canvas, not per selection: the safety half must
 * hold whatever is on the stage.
 */
import { apiCall } from '@/services/apiCall';

const WRITE_VERBS = ['post', 'put', 'patch', 'delete'];

let fixtures = {};
let report = () => {};

/**
 * The GET responses the current scenario declares, keyed by URL prefix — a
 * prefix rather than an exact match because every call carries query params
 * (`?size=…&library_id=…`) that are the store's business, not the fixture's.
 */
export function setApiFixtures(map) {
  fixtures = map || {};
}

/** Replaces apiCall's five verbs. `reporter(name, detail)` reaches the event log. */
export function installApiHarness(reporter) {
  report = reporter;

  for (const verb of WRITE_VERBS) {
    apiCall[verb] = async (url) => {
      report(`${verb.toUpperCase()} ${url}`, 'blocked — the canvas may not write');
      // Shaped like a success so a caller's optimistic path runs to completion
      // and the UI settles, rather than showing an error nothing caused.
      return { ok: true, data: { status: 'success' } };
    };
  }

  apiCall.get = async (url) => {
    const match = Object.keys(fixtures).find(prefix => url.startsWith(prefix));
    if (match) return { ok: true, data: fixtures[match] };

    report(`GET ${url}`, 'unstubbed — add a fixture in sources.js');
    return { ok: false, error: 'no fixture' };
  };
}
