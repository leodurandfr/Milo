// frontend/tests/stores/equalizerStore.test.js
/**
 * equalizerStore drives the unified per-target EQ surface
 * (GET/PUT/POST /api/equalizer/target/{target}, target ∈ local · <mac> · zone:<id>)
 * plus the per-client volume/mute endpoints.
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
    data: { zone_id: zoneId, zone: { id: zoneId, name: `Zone ${zoneId}`, client_ids: clientIds, ...extra } },
  });
}

function setMultiroom(enabled, volumeClients = {}) {
  audioStore.updateState({
    data: {
      full_state: {
        active_source: 'spotify',
        source_state: 'active',
        transitioning: false,
        metadata: {},
        multiroom_enabled: enabled,
        equalizer_effects_enabled: true,
      },
    },
  });
  audioStore.handleVolumeEvent({
    data: {
      show_bar: false,
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

  describe('client volume', () => {
    it('addresses the client by colon-free MAC', async () => {
      setMultiroom(true);

      await equalizerStore.updateClientEqualizerVolume(REMOTE_MAC, -25);

      expect(apiCall.patch).toHaveBeenCalledWith(
        '/api/volume/client/mac/dca6327ed343',
        { volume_db: -25 },
        expect.anything(),
      );
    });

    it('refuses to touch a remote client while multiroom is off', async () => {
      setMultiroom(false);

      const result = await equalizerStore.updateClientEqualizerVolume(REMOTE_MAC, -25);

      expect(result).toBe(false);
      expect(apiCall.patch).not.toHaveBeenCalled();
    });

    it('still allows the local client while multiroom is off', async () => {
      setMultiroom(false);

      const result = await equalizerStore.updateClientEqualizerVolume(LOCAL_MAC, -25);

      expect(result).toBe(true);
      expect(apiCall.patch).toHaveBeenCalled();
    });

    it('reports failure when the request fails', async () => {
      setMultiroom(true);
      apiCall.patch.mockResolvedValueOnce(fail());

      expect(await equalizerStore.updateClientEqualizerVolume(REMOTE_MAC, -25)).toBe(false);
    });

    it('reads volume and mute from the unified volume state, with defaults', () => {
      setMultiroom(true, { [REMOTE_MAC]: { volume_db: -30, mute: true } });

      expect(equalizerStore.getClientEqualizerVolume(REMOTE_MAC)).toBe(-30);
      expect(equalizerStore.getClientEqualizerMute(REMOTE_MAC)).toBe(true);
      expect(equalizerStore.getClientEqualizerVolume('unknown')).toBe(-30);
      expect(equalizerStore.getClientEqualizerMute('unknown')).toBe(false);
    });
  });

  describe('client mute', () => {
    beforeEach(() => {
      registerClient(OTHER_MAC, { name: 'Bedroom' });
      registerZone('z1', [REMOTE_MAC, OTHER_MAC]);
      setMultiroom(true);
    });

    it('mutes only the addressed client by default', async () => {
      await equalizerStore.updateClientEqualizerMute(REMOTE_MAC, true);

      expect(apiCall.patch).toHaveBeenCalledTimes(1);
      expect(apiCall.patch).toHaveBeenCalledWith(
        '/api/volume/client/mac/dca6327ed343/mute',
        { mute: true },
        expect.anything(),
      );
    });

    it('propagates to the other zone members when asked', async () => {
      await equalizerStore.updateClientEqualizerMute(REMOTE_MAC, true, { propagate: true });

      expect(apiCall.patch).toHaveBeenCalledTimes(2);
      expect(apiCall.patch).toHaveBeenCalledWith(
        '/api/volume/client/mac/dca6327ed344/mute',
        { mute: true },
        expect.anything(),
      );
    });

    it('skips offline members while propagating', async () => {
      registerClient(OTHER_MAC, { name: 'Bedroom', online: false });

      await equalizerStore.updateClientEqualizerMute(REMOTE_MAC, true, { propagate: true });

      expect(apiCall.patch).toHaveBeenCalledTimes(1);
    });

    it('does not propagate when the primary request fails', async () => {
      apiCall.patch.mockResolvedValueOnce(fail());

      const result = await equalizerStore.updateClientEqualizerMute(REMOTE_MAC, true, { propagate: true });

      expect(result).toBe(false);
      expect(apiCall.patch).toHaveBeenCalledTimes(1);
    });
  });

  describe('applyZoneDelta', () => {
    it('sends one atomic delta for the whole zone', async () => {
      setMultiroom(true);
      apiCall.patch.mockResolvedValueOnce(ok({ status: 'success', new_average_db: -25 }));

      const result = await equalizerStore.applyZoneDelta('z1', 5);

      expect(apiCall.patch).toHaveBeenCalledWith(
        '/api/volume/zone/z1',
        { delta_db: 5 },
        expect.objectContaining({ rethrow: true }),
      );
      expect(result.new_average_db).toBe(-25);
    });

    it('refuses while multiroom is off', async () => {
      setMultiroom(false);

      const result = await equalizerStore.applyZoneDelta('z1', 5);

      expect(result.status).toBe('error');
      expect(apiCall.patch).not.toHaveBeenCalled();
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

  describe('single-event WS handlers', () => {
    beforeEach(() => {
      equalizerStore.filters = [FILTER('eq_band_00', 31)];
    });

    it('filter_changed arrives as a raw event (wired with on(), not parsedOn)', () => {
      equalizerStore.handleFilterChanged({ data: { id: 'eq_band_00', freq: 1000, gain: 5.5 } });

      expect(equalizerStore.filters[0].gain).toBe(5.5);
      expect(equalizerStore.filters[0].displayName).toBe('1k');
    });

    it('filter_changed tolerates an event with no data', () => {
      expect(() => equalizerStore.handleFilterChanged({})).not.toThrow();
    });

    it('compressor_changed merges the validated payload, keeping untouched fields', () => {
      equalizerStore.handleCompressorChanged({ enabled: true, threshold: -15 });

      expect(equalizerStore.compressor).toMatchObject({
        enabled: true, threshold: -15, ratio: 4, attack: 10,
      });
    });

    it('loudness_changed merges the validated payload', () => {
      equalizerStore.handleLoudnessChanged({ enabled: true, low_boost: 8 });

      expect(equalizerStore.loudness).toMatchObject({ enabled: true, low_boost: 8, high_boost: 5 });
    });

    it('enabled_changed flips the effects flag, and ignores an event without one', () => {
      equalizerStore.handleEnabledChanged({ data: { enabled: false } });
      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(false);

      equalizerStore.handleEnabledChanged({ data: {} });
      expect(equalizerStore.isEqualizerEffectsEnabled).toBe(false);
    });

    it('state_changed records the CamillaDSP state and drives isConnected', () => {
      equalizerStore.handleStateChanged({ state: 'running' });
      expect(equalizerStore.isConnected).toBe(true);

      equalizerStore.handleStateChanged({ state: 'disconnected' });
      expect(equalizerStore.isConnected).toBe(false);
    });

    it('levels falls back to silence when the monitor reports unavailable', () => {
      equalizerStore.handleLevelsChanged({ available: true, output_peak: [-12, -14] });
      expect(equalizerStore.outputPeak).toEqual([-12, -14]);

      equalizerStore.handleLevelsChanged({ available: false, output_peak: [-12, -14] });
      expect(equalizerStore.outputPeak).toEqual([-80, -80]);
    });

    it('crossover_changed ignores a payload with no zone', () => {
      expect(() => equalizerStore.handleZoneCrossoverChanged({})).not.toThrow();
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
      apiCall.get.mockResolvedValueOnce(ok({
        clients: {
          [LOCAL_MAC]: { mac_id: LOCAL_MAC, name: 'Milo', online: true, is_local: true },
          [REMOTE_MAC]: { mac_id: REMOTE_MAC, name: 'Kitchen', online: true },
        },
        zones: {},
      }));

      await equalizerStore.loadTargets();

      expect(apiCall.get).toHaveBeenCalledWith('/api/multiroom/state', expect.anything());
      expect(equalizerStore.selectedTarget).toBe(LOCAL_MAC);
    });
  });

  describe('preset edit tracking', () => {
    /** Load a target sitting on the "jazz" preset, gains matching that preset. */
    async function loadOnJazzPreset() {
      apiCall.get.mockImplementation(async (url) => {
        if (url.endsWith('/presets')) {
          return ok({ presets: [{ id: 'jazz', gains: [4, 0] }] });
        }
        return ok({
          state: 'running',
          filters: [
            { id: 'eq_band_00', freq: 31, gain: 4, q: 1.41, type: 'Peaking' },
            { id: 'eq_band_01', freq: 63, gain: 0, q: 1.41, type: 'Peaking' },
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
  });
});
