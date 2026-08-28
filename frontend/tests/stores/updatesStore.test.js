// frontend/tests/stores/updatesStore.test.js
/**
 * updatesStore mirrors program/satellite update state, fed by `programs.*` WS
 * deltas and reconciled from GET /api/programs.
 *
 * Two things are pinned here. The resync gate: App.vue resyncs every delta
 * store on reconnect and on every tab return, and this one is loaded lazily by
 * UpdateManager. Each refetch costs an installed-version probe per program on
 * the Pi, so resyncing a store no view has opened is pure cost. And what
 * happens to a satellite between the moment it reports an update finished and
 * the moment its own API answers again — the window that left a row showing a
 * skeleton for a fetch nobody would ever make.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useUpdatesStore } from '@/stores/updatesStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

describe('updatesStore resync gate', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
  });

  it('does nothing before the store has ever loaded', async () => {
    await store.resync();

    expect(apiCall.get).not.toHaveBeenCalled();
  });

  it('refetches once the store has been loaded', async () => {
    apiCall.get.mockResolvedValue(ok({ programs: { milo: { status: 'installed' } }, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.get.mockClear();

    await store.resync();

    expect(apiCall.get).toHaveBeenCalledWith('/api/programs', expect.anything());
  });

  it('leaves the satellites alone while they have never been loaded', async () => {
    // Satellites only exist while multiroom is on, and discovering them probes
    // every unit over HTTP — the price of resyncing a list nobody asked for.
    apiCall.get.mockResolvedValue(ok({ programs: {}, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.get.mockClear();

    await store.resync();

    expect(apiCall.get).not.toHaveBeenCalledWith('/api/programs/satellites', expect.anything());
  });

  it('refetches the satellites once they have been loaded', async () => {
    // Their in-flight flags are delta-only too: a progress or completion event
    // missed while the tab was hidden leaves a button stuck on "updating".
    apiCall.get.mockResolvedValue(ok({ programs: {}, active_updates: [], satellites: [] }));
    await store.loadLocalPrograms();
    await store.loadSatellites();
    apiCall.get.mockClear();

    await store.resync();

    expect(apiCall.get).toHaveBeenCalledWith('/api/programs/satellites', expect.anything());
  });
});

describe('updatesStore satellite return', () => {
  const MAC = 'dc:a6:32:7e:d3:43';
  let store;

  const withSatellite = () => ok({ satellites: [{ mac_id: MAC, app_version: 'v0.1.0-1749-gc6247d94' }] });
  const withoutSatellite = () => ok({ satellites: [] });

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps fetching until the satellite that just restarted answers', async () => {
    // The completion is emitted while the unit is restarting its own services,
    // so the inventory it triggers answers without it. Measured on the fleet:
    // one satellite of two, 8 s after the press.
    apiCall.get
      .mockResolvedValueOnce(withoutSatellite())
      .mockResolvedValueOnce(withoutSatellite())
      .mockResolvedValue(withSatellite());

    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: true });
    await vi.advanceTimersByTimeAsync(30000);

    expect(store.satelliteByMacId[MAC]).toBeTruthy();
    expect(store.isSatelliteAwaitingReturn(MAC)).toBe(false);
  });

  it('draws the skeleton while it waits, not once it has given up', async () => {
    // Both halves matter: without the first the row reads "offline" during a
    // restart that is going fine, without the second it waits forever.
    apiCall.get.mockResolvedValue(withoutSatellite());

    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.isSatelliteAwaitingReturn(MAC)).toBe(true);

    await vi.advanceTimersByTimeAsync(60000);
    expect(store.isSatelliteAwaitingReturn(MAC)).toBe(false);

    const polls = apiCall.get.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60000);
    expect(apiCall.get.mock.calls.length).toBe(polls);
  });

  it('holds the fleet it already has while it refetches', async () => {
    // The skeleton is drawn from `satellites === null`, so re-entering that on
    // a refresh flashed every satellite section back to skeletons for the
    // length of a fleet-wide probe.
    apiCall.get.mockResolvedValue(withSatellite());
    await store.loadSatellites();

    let release;
    apiCall.get.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const inFlight = store.loadSatellites();

    expect(store.satelliteByMacId[MAC]).toBeTruthy();

    release(withSatellite());
    await inFlight;
  });
});
