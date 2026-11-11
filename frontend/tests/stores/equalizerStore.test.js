// frontend/tests/stores/equalizerStore.test.js
/**
 * equalizerStore drives the unified per-target EQ surface
 * (GET/PUT/POST /api/equalizer/target/{target}, target ∈ local · <mac> · zone:<id>).
 *
 * The two things worth pinning are the *target token derivation* — the one place
 * that decides whether a write lands on the local DAC, a satellite or a whole
 * zone — and the WS ingestion guards (relevance + drag-throttle echo).
 *
 * multiroomStore and unifiedAudioStore are the real stores here, driven through
 * their own WS handlers: mocking them would only assert against a fixture of
 * their API, which is what rotted the previous version of this file.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { publishState, makeSession } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const LOCAL_MAC = 'dc:a6:32:00:00:01';
const REMOTE_MAC = 'dc:a6:32:7e:d3:43';
const OTHER_MAC = 'dc:a6:32:7e:d3:44';

let equalizerStore;
let multiroomStore;
let audioStore;

function registerClient(macId, extra = {}) {
  multiroomStore.handleMultiroomEvent({
    type: 'client_state_changed',
    data: {
      mac_id: macId,
      client: { mac_id: macId, snapcast_id: macId, name: `Client ${macId}`, online: true, ...extra },
    },
  });
}

function registerZone(zoneId, clientIds, extra = {}) {
  multiroomStore.handleMultiroomEvent({
    type: 'zone_changed',
    data: { action: 'created', zone_id: zoneId, zone: { id: zoneId, name: `Zone ${zoneId}`, client_ids: clientIds, ...extra } },
  });
}

function setMultiroom(enabled, volumeClients = {}) {
  publishState(audioStore, {
    source: 'spotify',
    service: 'running',
    session: makeSession(),
    multiroom_enabled: enabled,
    equalizer_effects_enabled: true,
  });
  audioStore.handleVolumeEvent({
    data: {
      display: 'none',
      state: {
        mode: enabled ? 'multiroom' : 'direct',
        global_volume_db: -30,
        global_mute: false,
        volume_control: true,
        any_volume_control: true,
        clients: volumeClients,
        zones: {},
      },
    },
  });
}

/**
 * Answer loadTargets()' registry fetch with the local + one remote client;
 * `remote` overrides the remote client's fields, `clients`/`zones` add more.
 */
function registryTargets({ remote = {}, clients = {}, zones = {} } = {}) {
  apiCall.get.mockResolvedValueOnce(ok({
    clients: {
      [LOCAL_MAC]: { mac_id: LOCAL_MAC, name: 'Milo', online: true, is_local: true },
      [REMOTE_MAC]: { mac_id: REMOTE_MAC, name: 'Kitchen', online: true, ...remote },
      ...clients,
    },
    zones,
  }));
}

/** The path segment the store derived for the currently selected target. */
async function resolvedTargetPath() {
  apiCall.post.mockResolvedValueOnce(ok({ status: 'success', gains: [] }));
  await equalizerStore.loadPreset('flat');
  const [url] = apiCall.post.mock.calls.at(-1);
  return url.replace('/api/equalizer/target/', '').replace('/preset', '');
}

const FILTER = (id, freq, extra = {}) => ({
  id, freq, gain: 0, q: 1.41, type: 'Peaking', displayName: String(freq), ...extra,
});

describe('equalizerStore', () => {
  beforeEach(() => {
    resetApiCallMock();
    multiroomStore = useMultiroomStore();
    audioStore = useUnifiedAudioStore();
    equalizerStore = useEqualizerStore();
    registerClient(LOCAL_MAC, { is_local: true, name: 'Milo' });
    registerClient(REMOTE_MAC, { name: 'Kitchen' });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('target token derivation', () => {
    it('falls back to the local sentinel when no target is selected', async () => {
      expect(await resolvedTargetPath()).toBe('local');
    });

    it('resolves the local client to the sentinel, not its MAC', async () => {
      // The local DAC is addressable without a registry entry; the backend
      // (_resolve_target) expects 'local', never the machine's own MAC.
      equalizerStore.selectedTarget = LOCAL_MAC;

      expect(await resolvedTargetPath()).toBe('local');
    });

    it('resolves a standalone remote client to its MAC', async () => {
      equalizerStore.selectedTarget = REMOTE_MAC;

      expect(await resolvedTargetPath()).toBe(REMOTE_MAC);
    });

    it('resolves a zone member to zone:<id> while multiroom is on', async () => {
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;

      expect(await resolvedTargetPath()).toBe('zone:z1');
    });

    it('resolves an eq_independent zone member to its MAC, not the zone', async () => {
      // Detaching a member's EQ keeps it in the zone for playback but addresses
      // its own record for EQ — targetRef must skip the zone redirect.
      registerClient(REMOTE_MAC, { name: 'Kitchen', eq_independent: true });
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;

      expect(await resolvedTargetPath()).toBe(REMOTE_MAC);
    });

    it('resolves a shared (non-independent) zone member to the zone', async () => {
      // The other half: a member that has NOT detached still routes to the zone.
      registerClient(REMOTE_MAC, { name: 'Kitchen', eq_independent: false });
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;

      expect(await resolvedTargetPath()).toBe('zone:z1');
    });

    it('ignores zone membership while multiroom is off', async () => {
      // Zones only exist as an audio grouping under multiroom; with it off the
      // write must address the client itself.
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(false);
      equalizerStore.selectedTarget = REMOTE_MAC;

      expect(await resolvedTargetPath()).toBe(REMOTE_MAC);
    });

    it('uses the same base for every per-target write', async () => {
      equalizerStore.selectedTarget = REMOTE_MAC;

      await equalizerStore.saveCustomPreset();
      await equalizerStore.toggleEqualizerEffectsEnabled(false);
      await equalizerStore.updateCompressor({ enabled: true });

      const base = `/api/equalizer/target/${REMOTE_MAC}`;
      expect(apiCall.post).toHaveBeenCalledWith(`${base}/save-custom`, null, expect.anything());
      expect(apiCall.put).toHaveBeenCalledWith(`${base}/enabled`, { enabled: false }, expect.anything());
      expect(apiCall.put).toHaveBeenCalledWith(`${base}/compressor`, { enabled: true }, expect.anything());
    });
  });

  describe('loadStatus', () => {
    it('populates the whole record from a single GET on the target', async () => {
      equalizerStore.selectedTarget = REMOTE_MAC;
      apiCall.get.mockImplementation(async (url) => {
        if (url.endsWith('/presets')) {
          return ok({ presets: [{ id: 'flat', gains: [0] }, { id: 'jazz', gains: [4] }] });
        }
        return ok({
          state: 'running',
          enabled: true,
          filters: [{ id: 'eq_band_00', freq: 1000, gain: 3, q: 1.41, type: 'Peaking' }],
          compressor: { enabled: true, threshold: -15 },
          loudness: { enabled: true },
          mono: true,
          active_preset: 'jazz',
        });
      });

      await equalizerStore.loadStatus();

      expect(apiCall.get).toHaveBeenCalledWith(
        `/api/equalizer/target/${REMOTE_MAC}`,
        expect.anything(),
      );
      expect(equalizerStore.filters[0].displayName).toBe('1k');
      expect(equalizerStore.activePreset).toBe('jazz');
      expect(equalizerStore.builtinPresets).toHaveLength(2);
      expect(equalizerStore.mono).toBe(true);
      // Missing compressor fields keep their defaults instead of becoming undefined.
      expect(equalizerStore.compressor).toMatchObject({ enabled: true, threshold: -15, ratio: 4 });
      expect(equalizerStore.filtersLoaded).toBe(true);
    });

    it('falls back to the default 10-band layout when the record has no filters', async () => {
      apiCall.get.mockResolvedValue(ok({ state: 'running', filters: [], presets: [] }));

      await equalizerStore.loadStatus();

      expect(equalizerStore.filters).toHaveLength(10);
      expect(equalizerStore.filters[0]).toMatchObject({ id: 'eq_band_00', freq: 31 });
      expect(equalizerStore.filters.at(-1).displayName).toBe('16k');
    });
  });

  describe('superseded loads', () => {
    /**
     * loadStatus fetches the target record and the preset catalog together, and
     * only the record carries the abort signal. So a target switch while the
     * presets are still in flight leaves the previous call holding a *complete*
     * record for a target that is no longer displayed — the abort came too late
     * to stop it. Nothing but controller identity separates the two.
     */
    function deferPresets() {
      const resolvers = [];
      const signals = [];
      let records = 0;
      apiCall.get.mockImplementation(async (url, { signal } = {}) => {
        if (url.endsWith('/presets')) {
          return new Promise(resolve => resolvers.push(resolve));
        }
        signals.push(signal);
        records += 1;
        return ok({
          state: 'running',
          filters: [],
          active_preset: records === 1 ? 'rock' : 'jazz',
        });
      });
      return { resolvers, signals };
    }

    it('does not apply the superseded target record over the newer one', async () => {
      const { resolvers } = deferPresets();

      const first = equalizerStore.loadStatus();
      const second = equalizerStore.loadStatus();

      resolvers[1](ok({ presets: [] }));
      await second;
      expect(equalizerStore.activePreset).toBe('jazz');

      resolvers[0](ok({ presets: [] }));
      await first;
      expect(equalizerStore.activePreset).toBe('jazz');
    });

    it('leaves the newer load abortable by cleanup()', async () => {
      const { resolvers, signals } = deferPresets();

      const first = equalizerStore.loadStatus();
      equalizerStore.loadStatus();

      resolvers[0](ok({ presets: [] }));
      await first;

      equalizerStore.cleanup();
      expect(signals[1].aborted).toBe(true);
    });
  });

  describe('one loader per target', () => {
    /**
     * The record carries the master toggle, so loadStatus() is the only thing
     * that needs to read it. selectTarget used to call loadEnabledState() right
     * after, re-fetching the whole record to set a field loadStatus had just set
     * from the same response — and EqualizerModal read it a third time on open.
     */
    it('selectTarget reads the target record exactly once', async () => {
      apiCall.get.mockImplementation(async (url) => (
        url.endsWith('/presets') ? ok({ presets: [] }) : ok({ state: 'running', enabled: false, filters: [] })
      ));

      await equalizerStore.selectTarget(REMOTE_MAC);

      const recordReads = apiCall.get.mock.calls
        .filter(([url]) => url === `/api/equalizer/target/${REMOTE_MAC}`);
      expect(recordReads).toHaveLength(1);
      // ...and that single read is what set the master toggle.
      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(false);
    });

    it('restores the whole effect object when neither the write nor the read-back answers', async () => {
      const before = { ...equalizerStore.compressor };
      apiCall.put.mockResolvedValueOnce(fail());
      apiCall.get.mockResolvedValueOnce(fail());

      expect(await equalizerStore.updateCompressor({ enabled: true, threshold: -99 })).toBe(false);
      expect(equalizerStore.compressor).toEqual(before);
    });
  });

  describe('refused effect writes', () => {
    /**
     * EqualizerModal writes every slider input straight into the store while
     * the finger moves, so by the time updateLoudness() runs the store already
     * holds the dragged value: a snapshot taken there "restores" the value the
     * target refused. No WS echo follows a refused write, so the slider would
     * stay on a level the DSP is not playing until the modal is reopened.
     */
    async function loadLocalRecord(loudness) {
      apiCall.get.mockImplementation(async (url) => (
        url.endsWith('/presets')
          ? ok({ presets: [] })
          : ok({ state: 'running', enabled: true, filters: [], loudness })
      ));
      await equalizerStore.loadStatus();
      apiCall.get.mockReset();
    }

    it('a refused effect write shows the record the target still holds, even after the view wrote the dragged value', async () => {
      await loadLocalRecord({ enabled: true, low_boost: 8, high_boost: 5 });
      equalizerStore.loudness.low_boost = 12;
      apiCall.put.mockResolvedValueOnce(fail());
      apiCall.get.mockResolvedValueOnce(ok({
        state: 'running', loudness: { enabled: true, low_boost: 9, high_boost: 5 },
      }));

      expect(await equalizerStore.updateLoudness({ low_boost: 12 })).toBe(false);

      const putOrder = apiCall.put.mock.invocationCallOrder[0];
      const readBack = apiCall.get.mock.calls.findIndex(([url]) => url === '/api/equalizer/target/local');
      expect(readBack).toBeGreaterThanOrEqual(0);
      expect(apiCall.get.mock.invocationCallOrder[readBack]).toBeGreaterThan(putOrder);
      expect(equalizerStore.loudness.low_boost).toBe(9);
    });

    it('keeps a value the target persisted before its fan-out failed', async () => {
      // A satellite answering 503 after the record was saved: the server holds 12.
      await loadLocalRecord({ enabled: true, low_boost: 8, high_boost: 5 });
      equalizerStore.loudness.low_boost = 12;
      apiCall.put.mockResolvedValueOnce(fail('Satellite unavailable', 503));
      apiCall.get.mockResolvedValueOnce(ok({
        state: 'running', loudness: { enabled: true, low_boost: 12, high_boost: 6 },
      }));

      expect(await equalizerStore.updateLoudness({ low_boost: 12 })).toBe(false);

      expect(equalizerStore.loudness.low_boost).toBe(12);
      expect(equalizerStore.loudness.high_boost).toBe(6);
    });

    it('writes nothing into the target selected while the read-back was in flight', async () => {
      await loadLocalRecord({ enabled: true, low_boost: 8, high_boost: 5 });
      equalizerStore.loudness.low_boost = 12;
      apiCall.put.mockResolvedValueOnce(fail());
      apiCall.get.mockImplementationOnce(async () => {
        equalizerStore.selectedTarget = REMOTE_MAC;
        equalizerStore.loudness = { enabled: false, low_boost: 3, high_boost: 2 };
        return ok({ state: 'running', loudness: { enabled: true, low_boost: 9, high_boost: 5 } });
      });

      expect(await equalizerStore.updateLoudness({ low_boost: 12 })).toBe(false);

      expect(equalizerStore.loudness).toEqual({ enabled: false, low_boost: 3, high_boost: 2 });
    });

    it('reads nothing back from a target selected while the refused write was in flight', async () => {
      await loadLocalRecord({ enabled: true, low_boost: 8, high_boost: 5 });
      apiCall.put.mockImplementationOnce(async () => {
        equalizerStore.selectedTarget = REMOTE_MAC;
        return fail();
      });

      expect(await equalizerStore.updateLoudness({ low_boost: 12 })).toBe(false);

      expect(apiCall.get).not.toHaveBeenCalledWith(`/api/equalizer/target/${REMOTE_MAC}`, expect.anything());
    });

    it('a slow read-back does not overwrite a later value released in the meantime', async () => {
      await loadLocalRecord({ enabled: true, low_boost: 8, high_boost: 5 });
      let answerReadBack;
      apiCall.put.mockResolvedValueOnce(fail());
      apiCall.get.mockImplementationOnce(() => new Promise((resolve) => { answerReadBack = resolve; }));

      equalizerStore.loudness.low_boost = 12;
      const refused = equalizerStore.updateLoudness({ low_boost: 12 });
      await vi.waitFor(() => expect(answerReadBack).toBeTypeOf('function'));

      equalizerStore.loudness.low_boost = 10;
      apiCall.put.mockResolvedValueOnce(ok({ status: 'success' }));
      expect(await equalizerStore.updateLoudness({ low_boost: 10 })).toBe(true);

      answerReadBack(ok({ state: 'running', loudness: { enabled: true, low_boost: 8, high_boost: 5 } }));
      expect(await refused).toBe(false);

      expect(equalizerStore.loudness.low_boost).toBe(10);
    });
  });

  describe('master toggle', () => {
    /**
     * A zone toggle that fails may still have moved some of its members: the
     * backend answers "error" when one speaker refused, after announcing the
     * zone's enabled as the conjunction of what the members now hold. Restoring
     * the value from before the click leaves this screen showing the effects on
     * while the zone record, every other screen and the local DSP say off.
     */
    it('reads the zone back after a refused toggle rather than restoring the old value', async () => {
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;
      apiCall.put.mockImplementationOnce(async () => {
        equalizerStore.handleZoneEnabledChanged({ data: { zone_id: 'z1', enabled: false } });
        return fail('Zone z1 equalizer enabled not applied', 200);
      });
      apiCall.get.mockImplementation(async (url) => (
        url.endsWith('/presets')
          ? ok({ presets: [] })
          : ok({ state: 'running', enabled: false, filters: [] })
      ));

      expect(await equalizerStore.toggleEqualizerEffectsEnabled(false)).toBe(false);

      expect(apiCall.get).toHaveBeenCalledWith('/api/equalizer/target/zone:z1', expect.anything());
      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(false);
    });

    it('keeps the old value when nothing can be read back either', async () => {
      // The server unreachable: the write most likely never landed.
      apiCall.put.mockResolvedValueOnce(fail());
      apiCall.get.mockResolvedValue(fail());

      expect(await equalizerStore.toggleEqualizerEffectsEnabled(false)).toBe(false);

      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(true);
    });
  });

  describe('resync gate', () => {
    /**
     * App.vue resyncs every delta store on reconnect and on every tab return.
     * This one is loaded lazily by EqualizerModal, so before it has ever opened
     * there is nothing to heal — refetching would spend two requests per tab
     * focus on state no view is showing.
     */
    it('does nothing before the store has ever loaded', async () => {
      await equalizerStore.resync();

      expect(apiCall.get).not.toHaveBeenCalled();
    });

    it('refetches once the store has been loaded', async () => {
      apiCall.get.mockResolvedValue(ok({ state: 'running', filters: [], presets: [] }));
      await equalizerStore.loadStatus();
      apiCall.get.mockClear();

      await equalizerStore.resync();

      expect(apiCall.get).toHaveBeenCalled();
    });
  });

  describe('equalizer_changed ingestion', () => {
    beforeEach(() => {
      equalizerStore.selectedTarget = REMOTE_MAC;
      equalizerStore.filters = [FILTER('eq_band_00', 31), FILTER('eq_band_01', 62)];
    });

    it('applies a change addressed to the selected client', () => {
      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
        equalizer_settings: {
          filters: [{ id: 'eq_band_00', freq: 1000, gain: 5 }],
          compressor: { enabled: true, threshold: -15 },
          loudness: { enabled: true, low_boost: 8 },
        },
      });

      expect(equalizerStore.filters[0].gain).toBe(5);
      // A frequency change must refresh the label shown on the band.
      expect(equalizerStore.filters[0].displayName).toBe('1k');
      expect(equalizerStore.compressor).toMatchObject({ enabled: true, threshold: -15, ratio: 4 });
      expect(equalizerStore.loudness).toMatchObject({ enabled: true, low_boost: 8, high_boost: 5 });
    });

    it('ignores a change addressed to another client', () => {
      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: OTHER_MAC,
        equalizer_settings: { filters: [{ id: 'eq_band_00', gain: 9 }] },
      });

      expect(equalizerStore.filters[0].gain).toBe(0);
    });

    it('applies a zone change when the selected target belongs to that zone', () => {
      registerZone('z1', [REMOTE_MAC, OTHER_MAC]);
      setMultiroom(true);

      equalizerStore.handleEqualizerChanged({
        target_type: 'zone',
        target_id: 'z1',
        equalizer_settings: { filters: [{ id: 'eq_band_01', gain: -3 }] },
      });

      expect(equalizerStore.filters[1].gain).toBe(-3);
    });

    it('ignores a zone change for a zone the target is not in', () => {
      registerZone('z1', [OTHER_MAC]);
      setMultiroom(true);

      equalizerStore.handleEqualizerChanged({
        target_type: 'zone',
        target_id: 'z1',
        equalizer_settings: { filters: [{ id: 'eq_band_01', gain: -3 }] },
      });

      expect(equalizerStore.filters[1].gain).toBe(0);
    });

    /**
     * The backend broadcasts `target_id` as it was written, and the local unit
     * outside a zone is written as the 'local' sentinel. A second screen showing
     * the local unit compared that to its MAC and dropped the echo — then a
     * slider touched there re-posted the stale compressor it still showed.
     */
    it('applies the echo of a local edit (target_id local) while the local unit is shown', () => {
      setMultiroom(false);
      equalizerStore.selectedTarget = LOCAL_MAC;
      equalizerStore.compressor.enabled = true;

      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: 'local',
        equalizer_settings: {
          compressor: { enabled: false },
          filters: [{ id: 'eq_band_00', gain: 4 }],
        },
      });

      expect(equalizerStore.compressor.enabled).toBe(false);
      expect(equalizerStore.filters[0].gain).toBe(4);
    });

    /**
     * A member that detached its EQ is excluded from every zone write, so a
     * zone record is not what it plays — adopting it (preset included) shows a
     * curve the satellite does not have, which a later discard re-posts to it.
     */
    it('ignores a zone change while the selected member is eq-independent', () => {
      registerClient(REMOTE_MAC, { name: 'Kitchen', eq_independent: true });
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);

      equalizerStore.handleEqualizerChanged({
        target_type: 'zone',
        target_id: 'z1',
        equalizer_settings: {
          filters: [{ id: 'eq_band_00', gain: 6 }],
          active_preset: 'rock',
          mono: true,
        },
      });

      expect(equalizerStore.filters[0].gain).toBe(0);
      expect(equalizerStore.activePreset).toBe('flat');
      expect(equalizerStore.mono).toBe(false);
    });

    /**
     * A member's record is the zone's except for its own master flag, and the
     * registry announces it under the member's MAC. With the zone on screen the
     * zone's own events are the ones that describe it, so a member's echo is not
     * adopted in its place.
     */
    it("ignores a member's own record while its zone is displayed", () => {
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);

      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
        equalizer_settings: { filters: [{ id: 'eq_band_00', gain: 2 }] },
      });

      expect(equalizerStore.filters[0].gain).toBe(0);
    });

    it('ignores a zone change while multiroom is off', () => {
      // Zones route nothing in direct mode: the selected client is addressed
      // on its own, so the zone's record is not the one on screen.
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(false);

      equalizerStore.handleEqualizerChanged({
        target_type: 'zone',
        target_id: 'z1',
        equalizer_settings: { filters: [{ id: 'eq_band_00', gain: -7 }] },
      });

      expect(equalizerStore.filters[0].gain).toBe(0);
    });

    /**
     * A standalone satellite's master flag lives in its record, and the
     * registry's record event is the only announcement it gets. The local unit
     * and a zone have their own enabled events — a zone's record carries no
     * flag of its own, and the local one is a settings value, not the record.
     */
    it('adopts enabled from a record addressed to the selected remote client', () => {
      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
        equalizer_settings: { enabled: false },
      });

      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(false);
    });

    it('leaves enabled to its own events on the local unit and on a zone', () => {
      setMultiroom(false);
      equalizerStore.selectedTarget = LOCAL_MAC;
      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: 'local',
        equalizer_settings: { enabled: false },
      });
      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(true);

      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);
      equalizerStore.handleEqualizerChanged({
        target_type: 'zone',
        target_id: 'z1',
        equalizer_settings: { enabled: false },
      });
      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(true);
    });

    /**
     * A save on another screen names "custom" and carries the curve it saved.
     * Snapshotting the previous one instead, this screen reads its own matching
     * bands as an edit of the preset just saved.
     */
    it.each([
      ['a screen on another preset', 'flat'],
      ['a screen already on custom', 'custom'],
    ])('adopts the saved custom curve on %s', (_, shownPreset) => {
      vi.useFakeTimers();
      equalizerStore.filters = [FILTER('eq_band_00', 31, { gain: 3 }), FILTER('eq_band_01', 62, { gain: -1 })];
      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
        equalizer_settings: { active_preset: shownPreset, custom_gains: [1, 1] },
      });

      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
        equalizer_settings: { active_preset: 'custom', custom_gains: [3, -1] },
      });
      equalizerStore.updateFilter('eq_band_00', 'gain', 3);

      expect(equalizerStore.activePreset).toBe('custom');
      expect(equalizerStore.isPresetEdited).toBe(false);
    });

    it('ignores an event with no settings', () => {
      expect(() => equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
      })).not.toThrow();

      expect(equalizerStore.filters[0].gain).toBe(0);
    });

    it('adopts a preset change and clears the edited flag', () => {
      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
        equalizer_settings: { active_preset: 'rock' },
      });

      expect(equalizerStore.activePreset).toBe('rock');
      expect(equalizerStore.isPresetEdited).toBe(false);
    });

    it('does not overwrite a band the user is currently dragging', () => {
      vi.useFakeTimers();
      // updateFilter arms the per-filter throttle; the echo of an older value
      // must not fight the slider under the finger.
      equalizerStore.updateFilter('eq_band_00', 'gain', 6);

      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
        equalizer_settings: { filters: [{ id: 'eq_band_00', gain: 0 }, { id: 'eq_band_01', gain: -2 }] },
      });

      expect(equalizerStore.filters[0].gain).toBe(6);
      // Bands that aren't being edited still take the update.
      expect(equalizerStore.filters[1].gain).toBe(-2);
    });
  });

  describe('band write serialization', () => {
    const BAND = 'eq_band_00';

    beforeEach(() => {
      vi.useFakeTimers();
      equalizerStore.selectedTarget = REMOTE_MAC;
      equalizerStore.filters = [FILTER(BAND, 31)];
    });

    /** Hold every PUT until the test answers it; returns the queue of answers. */
    function holdPuts() {
      const answers = [];
      apiCall.put.mockImplementation(() => new Promise(resolve => answers.push(() => resolve(ok()))));
      return answers;
    }

    const sentGains = () => apiCall.put.mock.calls
      .filter(([url]) => url.includes('/filter/'))
      .map(([, body]) => body.gain);

    it('the last PUT for a band carries the released value when an earlier PUT resolves late', async () => {
      // A satellite applies writes in the order they reach it. Sent side by
      // side, a late one landed after the release and left the DSP on an older
      // gain than the slider, whose echo the drag guard then ignored.
      const answers = holdPuts();
      equalizerStore.updateFilter(BAND, 'gain', 1);
      await vi.advanceTimersByTimeAsync(50);
      equalizerStore.updateFilter(BAND, 'gain', 2);
      await vi.advanceTimersByTimeAsync(50);
      equalizerStore.updateFilter(BAND, 'gain', 3);
      let released = false;
      equalizerStore.finalizeFilterUpdate(BAND).then(() => { released = true; });
      await vi.advanceTimersByTimeAsync(500);

      expect(apiCall.put).toHaveBeenCalledTimes(1);

      answers.shift()();
      await vi.advanceTimersByTimeAsync(0);
      expect(sentGains()).toEqual([1, 3]);
      expect(released).toBe(false);

      answers.shift()();
      await vi.advanceTimersByTimeAsync(1000);
      expect(released).toBe(true);
      expect(sentGains()).toEqual([1, 3]);
    });

    it('a second drag right after a release leaves no stale final PUT', async () => {
      equalizerStore.updateFilter(BAND, 'gain', 3);
      await equalizerStore.finalizeFilterUpdate(BAND);
      // Taken again inside the window the release keeps the band guarded,
      // and still moving when that window ends.
      await vi.advanceTimersByTimeAsync(200);
      equalizerStore.updateFilter(BAND, 'gain', 10);
      await vi.advanceTimersByTimeAsync(50);
      equalizerStore.updateFilter(BAND, 'gain', 12);
      await vi.advanceTimersByTimeAsync(60);
      equalizerStore.updateFilter(BAND, 'gain', 14);
      await vi.advanceTimersByTimeAsync(10);
      await equalizerStore.finalizeFilterUpdate(BAND);
      const sentByRelease = apiCall.put.mock.calls.length;

      await vi.advanceTimersByTimeAsync(1000);

      expect(apiCall.put).toHaveBeenCalledTimes(sentByRelease);
      expect(sentGains().at(-1)).toBe(14);
    });

    it('a drag taken again right after a release keeps ignoring stale echoes while held', async () => {
      equalizerStore.updateFilter(BAND, 'gain', 3);
      await equalizerStore.finalizeFilterUpdate(BAND);
      await vi.advanceTimersByTimeAsync(100);
      equalizerStore.updateFilter(BAND, 'gain', 10);
      // Held past the window the first release armed.
      await vi.advanceTimersByTimeAsync(400);

      equalizerStore.handleEqualizerChanged({
        target_type: 'client',
        target_id: REMOTE_MAC,
        equalizer_settings: { filters: [{ id: BAND, gain: 3 }] },
      });

      expect(equalizerStore.filters[0].gain).toBe(10);
    });

    it('still sends a released value queued behind a write in flight when the editor is left', async () => {
      // Closing the modal or switching tab runs cleanup() right after the
      // release; the value is the old target's, and it is what the user set.
      const answers = holdPuts();
      equalizerStore.updateFilter(BAND, 'gain', 1);
      await vi.advanceTimersByTimeAsync(50);
      equalizerStore.updateFilter(BAND, 'gain', 3);
      equalizerStore.finalizeFilterUpdate(BAND);
      equalizerStore.cleanup();

      answers.shift()();
      await vi.advanceTimersByTimeAsync(0);

      expect(apiCall.put.mock.calls.map(([url, body]) => [url, body.gain])).toEqual([
        [`/api/equalizer/target/${REMOTE_MAC}/filter/${BAND}`, 1],
        [`/api/equalizer/target/${REMOTE_MAC}/filter/${BAND}`, 3],
      ]);
    });

    it('waits for a band write in flight before re-posting the preset it left', async () => {
      // The restore must be the last write the target sees: a band PUT landing
      // after it would put the discarded edit back. Called as the modal does,
      // cleanup() right behind it.
      equalizerStore.activePreset = 'jazz';
      equalizerStore.isPresetEdited = true;
      const answers = holdPuts();
      equalizerStore.updateFilter(BAND, 'gain', 6);
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success', gains: [4] }));

      const discarded = equalizerStore.discardPresetEdits();
      equalizerStore.cleanup();
      equalizerStore.selectedTarget = LOCAL_MAC;
      await vi.advanceTimersByTimeAsync(0);
      expect(apiCall.post).not.toHaveBeenCalled();

      answers.shift()();
      expect(await discarded).toBe(true);
      expect(apiCall.post).toHaveBeenCalledWith(
        `/api/equalizer/target/${REMOTE_MAC}/preset`,
        { preset_id: 'jazz' },
        expect.anything(),
      );
      await vi.advanceTimersByTimeAsync(1000);
      expect(apiCall.put).toHaveBeenCalledTimes(1);
    });
  });

  describe('single-event WS handlers', () => {
    beforeEach(() => {
      equalizerStore.filters = [FILTER('eq_band_00', 31)];
    });

    it('enabled_changed flips the effects flag, and ignores an event without one', () => {
      equalizerStore.handleEnabledChanged({ data: { enabled: false } });
      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(false);

      equalizerStore.handleEnabledChanged({ data: {} });
      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(false);
    });

    /**
     * enabled_changed is the local DAC's master bypass and carries no target
     * (routing.py; also fired by toggling the Equalizer dock app). Adopting it
     * while another target is on screen reports that target as bypassed when it
     * is not — a zone announces through zone_enabled_changed, a remote client
     * through its own record.
     */
    it('enabled_changed is ignored while a remote client is displayed', () => {
      equalizerStore.selectedTarget = REMOTE_MAC;

      equalizerStore.handleEnabledChanged({ data: { enabled: false } });

      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(true);
    });

    it('enabled_changed is ignored while a zone is displayed', () => {
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);
      equalizerStore.selectedTarget = LOCAL_MAC;

      equalizerStore.handleEnabledChanged({ data: { enabled: false } });

      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(true);
    });

    it('zone_enabled_changed flips the effects flag while its zone is displayed', () => {
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;

      equalizerStore.handleZoneEnabledChanged({ data: { zone_id: 'z1', enabled: false } });

      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(false);
    });

    it('ignores zone_enabled_changed while the selected member is eq-independent', () => {
      // The zone's master toggle leaves a detached member's flag alone, so the
      // zone's value says nothing about the one on screen.
      registerClient(REMOTE_MAC, { name: 'Kitchen', eq_independent: true });
      registerZone('z1', [LOCAL_MAC, REMOTE_MAC]);
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;

      equalizerStore.handleZoneEnabledChanged({ data: { zone_id: 'z1', enabled: false } });

      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(true);
    });

    it('state_changed records the CamillaDSP state and drives isConnected', () => {
      equalizerStore.handleStateChanged({ state: 'running' });
      expect(equalizerStore.isConnected).toBe(true);

      equalizerStore.handleStateChanged({ state: 'disconnected' });
      expect(equalizerStore.isConnected).toBe(false);
    });

    /**
     * state_changed is the server's own CamillaDSP connection and carries no
     * target, while a satellite's state comes from its own /equalizer/status
     * (api/equalizer.py). Adopting it turned the modal of an unreachable
     * satellite into live controls the moment the local daemon reconnected.
     */
    function answerRecordWithState(recordState) {
      apiCall.get.mockImplementation(async (url) => (url.endsWith('/presets')
        ? ok({ presets: [] })
        : ok({ state: recordState, enabled: true, filters: [] })));
    }

    it('state_changed from the local DSP leaves a satellite target\'s state alone', async () => {
      equalizerStore.selectedTarget = REMOTE_MAC;
      answerRecordWithState('disconnected');
      await equalizerStore.loadStatus();

      equalizerStore.handleStateChanged({ state: 'running' });

      expect(equalizerStore.isConnected).toBe(false);
    });

    it('state_changed still drives a zone target, which reads the local DSP', async () => {
      // A zone's state is the local CamillaDSP's whoever its members are, so
      // the guard is about the kind of target, not about a local member.
      registerZone('z1', [REMOTE_MAC, OTHER_MAC]);
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;
      answerRecordWithState('disconnected');
      await equalizerStore.loadStatus();

      equalizerStore.handleStateChanged({ state: 'running' });

      expect(equalizerStore.isConnected).toBe(true);
    });

    /**
     * EqualizerModal shows the "not running" spinner card iff !isConnected, and
     * the controls' skeleton otherwise. A target nothing has answered for yet is
     * unknown, not disconnected: reading it as disconnected held every first
     * opening on that card while the record was merely in flight.
     */
    it('a target no record has answered for is not reported disconnected', async () => {
      equalizerStore.selectedTarget = REMOTE_MAC;
      answerRecordWithState('disconnected');
      await equalizerStore.loadStatus();
      expect(equalizerStore.isConnected).toBe(false);

      apiCall.get.mockImplementation(() => new Promise(() => {}));
      equalizerStore.selectTarget(OTHER_MAC);

      expect(equalizerStore.isConnected).toBe(true);
      // Nor do the controls hold its record yet: they are its inert skeleton.
      expect(equalizerStore.hasRecord).toBe(false);
    });

    /**
     * The other half: a reload of the same target (resync on a tab return, the
     * master toggle turned back on) keeps what its last record said, or the
     * modal on an offline DSP flips card → skeleton → card on every one, and
     * every control goes inert and the preset name blank for the round trip.
     */
    it('a reload of the same target keeps its last known state and record', async () => {
      equalizerStore.selectedTarget = REMOTE_MAC;
      answerRecordWithState('disconnected');
      await equalizerStore.loadStatus();

      apiCall.get.mockImplementation(() => new Promise(() => {}));
      equalizerStore.loadStatus();

      expect(equalizerStore.isConnected).toBe(false);
      expect(equalizerStore.hasRecord).toBe(true);
    });

    it('levels falls back to silence when the monitor reports unavailable', () => {
      equalizerStore.handleLevelsChanged({ available: true, output_peak: [-12, -14] });
      expect(equalizerStore.outputPeak).toEqual([-12, -14]);

      equalizerStore.handleLevelsChanged({ available: false, output_peak: [-12, -14] });
      expect(equalizerStore.outputPeak).toEqual([-80, -80]);
    });

  });

  describe('targets derived from the registry', () => {
    it('exposes the registry clients as EQ targets', () => {
      const targets = equalizerStore.availableTargets;

      expect(targets.map(t => t.id)).toEqual([LOCAL_MAC, REMOTE_MAC]);
      expect(targets[0]).toMatchObject({ name: 'Milo', is_local: true, online: true });
    });

    it('defaults an unconfigured speaker type to bookshelf', () => {
      registerClient(OTHER_MAC, { speaker_type: 'subwoofer' });

      expect(equalizerStore.getClientSpeakerType(OTHER_MAC)).toBe('subwoofer');
      expect(equalizerStore.getClientSpeakerType(REMOTE_MAC)).toBe('bookshelf');
      expect(equalizerStore.getClientSpeakerType('unknown')).toBe('bookshelf');
    });

    it('loadTargets initialises the registry then auto-selects the local client', async () => {
      registryTargets();

      await equalizerStore.loadTargets();

      expect(apiCall.get).toHaveBeenCalledWith('/api/multiroom/state', expect.anything());
      expect(equalizerStore.selectedTarget).toBe(LOCAL_MAC);
    });

    /**
     * The selected target outlives the modal, so it can name a client that was
     * forgotten from Réglages meanwhile. Nothing in the store used to check
     * that: targetRef() kept addressing the dead MAC, so the record GET 404'd
     * and loadStatus() returned in silence — leaving the previous target's EQ on
     * screen and every write failing, until the user happened to pick another
     * target by hand.
     */
    it('drops a selected target that no longer exists and re-adopts the local client', async () => {
      registryTargets();
      equalizerStore.selectedTarget = OTHER_MAC;

      await equalizerStore.loadTargets();

      expect(equalizerStore.selectedTarget).toBe(LOCAL_MAC);
      expect(await resolvedTargetPath()).toBe('local');
    });

    it('keeps a selected target that is still registered', async () => {
      registryTargets();
      equalizerStore.selectedTarget = REMOTE_MAC;

      await equalizerStore.loadTargets();

      expect(equalizerStore.selectedTarget).toBe(REMOTE_MAC);
    });

    /**
     * A satellite unplugged after its tab was picked stays in the registry,
     * offline. Its record GET answers `state: "disconnected"` (the router skips
     * an offline client), and the modal then shows only "Connecting…", with no
     * tab strip to leave by: reopening kept the same target, so the server's own
     * EQ was out of reach until a page reload, which a kiosk barely offers.
     */
    it.each([
      ['multiroom on', true],
      ['multiroom off', false],
    ])('drops a selected satellite that is offline and re-adopts the local client (%s)', async (_, multiroom) => {
      registryTargets({ remote: { online: false } });
      setMultiroom(multiroom);
      equalizerStore.selectedTarget = REMOTE_MAC;

      await equalizerStore.loadTargets();

      expect(equalizerStore.selectedTarget).toBe(LOCAL_MAC);
      expect(await resolvedTargetPath()).toBe('local');
    });

    it('keeps a zone tab whose representative member is offline', async () => {
      // A zone reads the local DSP whoever represents it: dropping it would
      // send the page to the server's own EQ for no reason.
      registryTargets({
        remote: { online: false },
        clients: { [OTHER_MAC]: { mac_id: OTHER_MAC, name: 'Office', online: true } },
        zones: { z1: { id: 'z1', name: 'Upstairs', client_ids: [REMOTE_MAC, OTHER_MAC] } },
      });
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;

      await equalizerStore.loadTargets();

      expect(equalizerStore.selectedTarget).toBe(REMOTE_MAC);
      expect(await resolvedTargetPath()).toBe('zone:z1');
    });

    it('drops an offline zone member that detached its EQ', async () => {
      // A detached member reads its own satellite, not the zone's DSP.
      registryTargets({
        remote: { online: false, eq_independent: true },
        clients: { [OTHER_MAC]: { mac_id: OTHER_MAC, name: 'Office', online: true } },
        zones: { z1: { id: 'z1', name: 'Upstairs', client_ids: [REMOTE_MAC, OTHER_MAC] } },
      });
      setMultiroom(true);
      equalizerStore.selectedTarget = REMOTE_MAC;

      await equalizerStore.loadTargets();

      expect(equalizerStore.selectedTarget).toBe(LOCAL_MAC);
    });

    it('sends nothing left over for a satellite a resync drops', async () => {
      // A reconnect can find the satellite on screen offline: a band write still
      // throttled for it would fire through the new selection, onto the server.
      vi.useFakeTimers();
      registryTargets();
      equalizerStore.selectedTarget = REMOTE_MAC;
      await equalizerStore.loadTargets();
      apiCall.get.mockResolvedValue(ok({ state: 'running', filters: [FILTER('eq_band_00', 31)], presets: [] }));
      await equalizerStore.loadStatus();
      equalizerStore.updateFilter('eq_band_00', 'gain', 3);
      registerClient(REMOTE_MAC, { name: 'Kitchen', online: false });

      await equalizerStore.resync();
      await vi.advanceTimersByTimeAsync(1000);

      expect(equalizerStore.selectedTarget).toBe(LOCAL_MAC);
      const bandWrites = apiCall.put.mock.calls.map(([url]) => url).filter(url => url.includes('/filter/'));
      expect(bandWrites).toEqual([`/api/equalizer/target/${REMOTE_MAC}/filter/eq_band_00`]);
    });
  });

  describe('preset edit tracking', () => {
    /**
     * Load a target sitting on the "jazz" preset. `gains` are what the record
     * carries — equal to the preset's unless a test wants them to diverge.
     */
    async function loadOnJazzPreset(gains = [4, 0]) {
      apiCall.get.mockImplementation(async (url) => {
        if (url.endsWith('/presets')) {
          return ok({ presets: [{ id: 'jazz', gains: [4, 0] }] });
        }
        return ok({
          state: 'running',
          filters: [
            { id: 'eq_band_00', freq: 31, gain: gains[0], q: 1.41, type: 'Peaking' },
            { id: 'eq_band_01', freq: 63, gain: gains[1], q: 1.41, type: 'Peaking' },
          ],
          active_preset: 'jazz',
        });
      });
      await equalizerStore.loadStatus();
    }

    it('marks the preset edited once a gain leaves the preset values', async () => {
      vi.useFakeTimers();
      await loadOnJazzPreset();
      expect(equalizerStore.isPresetEdited).toBe(false);

      equalizerStore.updateFilter('eq_band_00', 'gain', 6);

      expect(equalizerStore.isPresetEdited).toBe(true);
    });

    it('stays unedited when a gain is set back to the preset value', async () => {
      vi.useFakeTimers();
      await loadOnJazzPreset();

      equalizerStore.updateFilter('eq_band_00', 'gain', 4);

      expect(equalizerStore.isPresetEdited).toBe(false);
    });

    it('treats a Q change as an edit even without touching the gains', async () => {
      vi.useFakeTimers();
      await loadOnJazzPreset();

      equalizerStore.updateFilter('eq_band_00', 'q', 2.0);

      expect(equalizerStore.isPresetEdited).toBe(true);
    });

    it('reports a record whose gains do not match the preset it names as edited', async () => {
      // The drag is written to the target as it happens, so a reload landing
      // mid-edit reads back "jazz" over gains jazz does not define. Printing the
      // preset name over someone else's curve is the bug this answers.
      await loadOnJazzPreset([6, 0]);

      expect(equalizerStore.activePreset).toBe('jazz');
      expect(equalizerStore.isPresetEdited).toBe(true);
    });

    it('re-posts the preset when the editor is left on unsaved edits', async () => {
      vi.useFakeTimers();
      await loadOnJazzPreset();
      equalizerStore.updateFilter('eq_band_00', 'gain', 6);
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success', gains: [4, 0] }));

      expect(await equalizerStore.discardPresetEdits()).toBe(true);

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/equalizer/target/local/preset',
        { preset_id: 'jazz' },
        expect.anything(),
      );
      // Deliberately not applied locally: the answer lands after the editor is
      // gone, on state that no longer describes what is on screen.
      expect(equalizerStore.filters[0].gain).toBe(6);
    });

    it('writes nothing when the editor is left with no unsaved edit', async () => {
      await loadOnJazzPreset();

      expect(await equalizerStore.discardPresetEdits()).toBe(false);

      expect(apiCall.post).not.toHaveBeenCalled();
    });
  });

  describe('levels monitor', () => {
    // An empty client list is the backend's word for "the local DAC", so a
    // selection whose every client is muted must never reach it as []: the
    // meters of a muted satellite would then follow the living room's music.
    const MONITOR = '/api/equalizer/levels/monitor';
    const monitorPosts = () => apiCall.post.mock.calls.filter(([url]) => url === MONITOR);

    it('does not arm the local meter when every selected client is muted', async () => {
      setMultiroom(true, { [REMOTE_MAC]: { mute: true, volume_db: -30 } });

      await equalizerStore.keepLevelsMonitorAlive([REMOTE_MAC]);
      equalizerStore.handleLevelsChanged({ available: true, output_peak: [-12, -14] });

      expect(monitorPosts()).toEqual([]);
      expect(equalizerStore.outputPeak).toEqual([-80, -80]);
    });

    it('does not arm the local meter for a zone of muted satellites', async () => {
      setMultiroom(true, {
        [REMOTE_MAC]: { mute: true, volume_db: -30 },
        [OTHER_MAC]: { mute: true, volume_db: -30 },
      });

      await equalizerStore.keepLevelsMonitorAlive([REMOTE_MAC, OTHER_MAC]);
      equalizerStore.handleLevelsChanged({ available: true, output_peak: [-12, -14] });

      expect(monitorPosts()).toEqual([]);
      expect(equalizerStore.outputPeak).toEqual([-80, -80]);
    });

    it('silences meters a monitor armed on another selection keeps feeding', async () => {
      // The monitor runs ~15 s past its last keepalive: the local DAC's samples
      // keep arriving after the selection moved to a muted satellite.
      setMultiroom(true, { [REMOTE_MAC]: { mute: true, volume_db: -30 } });
      await equalizerStore.keepLevelsMonitorAlive([LOCAL_MAC]);
      equalizerStore.handleLevelsChanged({ available: true, output_peak: [-12, -14] });

      await equalizerStore.keepLevelsMonitorAlive([REMOTE_MAC]);
      equalizerStore.handleLevelsChanged({ available: true, output_peak: [-10, -11] });

      expect(equalizerStore.outputPeak).toEqual([-80, -80]);
    });

    it('arms the monitor on the unmuted members only', async () => {
      setMultiroom(true, {
        [REMOTE_MAC]: { mute: false, volume_db: -30 },
        [OTHER_MAC]: { mute: true, volume_db: -30 },
      });

      await equalizerStore.keepLevelsMonitorAlive([REMOTE_MAC]);
      await equalizerStore.keepLevelsMonitorAlive([REMOTE_MAC, OTHER_MAC]);

      expect(monitorPosts().map(([, body]) => body)).toEqual([
        { client_ids: [REMOTE_MAC] },
        { client_ids: [REMOTE_MAC] },
      ]);
    });

    it('takes levels again once the selection is unmuted', async () => {
      setMultiroom(true, { [REMOTE_MAC]: { mute: true, volume_db: -30 } });
      await equalizerStore.keepLevelsMonitorAlive([REMOTE_MAC]);
      setMultiroom(true, { [REMOTE_MAC]: { mute: false, volume_db: -30 } });

      await equalizerStore.keepLevelsMonitorAlive([REMOTE_MAC]);
      equalizerStore.handleLevelsChanged({ available: true, output_peak: [-12, -14] });

      expect(monitorPosts().map(([, body]) => body)).toEqual([{ client_ids: [REMOTE_MAC] }]);
      expect(equalizerStore.outputPeak).toEqual([-12, -14]);
    });
  });
});
