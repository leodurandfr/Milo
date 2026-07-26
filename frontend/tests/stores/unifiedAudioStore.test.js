// frontend/tests/stores/unifiedAudioStore.test.js
/**
 * unifiedAudioStore is the central audio mirror: every source's state reaches
 * the UI through it. These tests cover the logic it owns — schema-guarded
 * ingestion of WS payloads, the stale-position guard, and the volume-bar
 * lifecycle — not the URLs of its pass-through actions.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

/** A full_state envelope as broadcast by the backend `system.state_changed`. */
const fullStateEvent = (fullState) => ({ data: { full_state: fullState } });

const VALID_FULL_STATE = {
  active_source: 'spotify',
  source_state: 'active',
  transitioning: false,
  metadata: { title: 'Test Song', position: 1000, duration: 200000 },
  multiroom_enabled: true,
  equalizer_effects_enabled: true,
};

describe('unifiedAudioStore', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useUnifiedAudioStore();
  });

  describe('updateState — full_state ingestion', () => {
    it('applies a valid full_state to systemState', () => {
      store.updateState(fullStateEvent(VALID_FULL_STATE));

      expect(store.systemState.active_source).toBe('spotify');
      expect(store.systemState.source_state).toBe('active');
      expect(store.systemState.multiroom_enabled).toBe(true);
      expect(store.systemState.metadata.title).toBe('Test Song');
    });

    it('leaves state untouched when the event carries no full_state', () => {
      store.updateState(fullStateEvent(VALID_FULL_STATE));
      store.updateState({ data: {} });

      expect(store.systemState.active_source).toBe('spotify');
    });

    it('coerces an unknown active_source to none rather than dropping the update', () => {
      // SystemStateSchema uses .catch() defaults: an unrecognised source must not
      // freeze the UI on a stale source_state (see schemas/api.js).
      store.updateState(fullStateEvent({ ...VALID_FULL_STATE, active_source: 'gramophone' }));

      expect(store.systemState.active_source).toBe('none');
      expect(store.systemState.source_state).toBe('active');
    });

    it('replaces metadata wholesale so a field cleared by the backend disappears', () => {
      store.updateState(fullStateEvent(VALID_FULL_STATE));
      store.updateState(fullStateEvent({ ...VALID_FULL_STATE, metadata: { title: 'Next Song' } }));

      expect(store.systemState.metadata.title).toBe('Next Song');
      expect(store.systemState.metadata.position).toBeUndefined();
    });

    it('stamps positionTimestamp only when the position value actually changes', () => {
      store.updateState(fullStateEvent(VALID_FULL_STATE));
      const first = store.positionTimestamp;
      expect(first).toBeGreaterThan(0);

      // Same position → the reading is not fresher, the stamp must not move.
      store.updateState(fullStateEvent(VALID_FULL_STATE));
      expect(store.positionTimestamp).toBe(first);

      store.updateState(fullStateEvent({
        ...VALID_FULL_STATE,
        metadata: { ...VALID_FULL_STATE.metadata, position: 2000 },
      }));
      expect(store.positionTimestamp).toBeGreaterThanOrEqual(first);
      expect(store.systemState.metadata.position).toBe(2000);
    });
  });

  describe('updatePosition — stale-source guard', () => {
    beforeEach(() => {
      store.updateState(fullStateEvent(VALID_FULL_STATE));
    });

    it('applies a position_update coming from the active source', () => {
      store.updatePosition({ source: 'spotify', position: 5000, duration: 200000 });

      expect(store.systemState.metadata.position).toBe(5000);
      expect(store.systemState.metadata.duration).toBe(200000);
    });

    it('ignores a position_update from a source that is no longer active', () => {
      // During a transition the previous source can still emit; applying it would
      // rewind the progress bar of the source that just took over.
      store.updatePosition({ source: 'radio', position: 999999, duration: 1 });

      expect(store.systemState.metadata.position).toBe(1000);
      expect(store.systemState.metadata.duration).toBe(200000);
    });
  });

  describe('handleVolumeEvent', () => {
    const volumeEvent = (state, extra = {}) => ({ data: { state, ...extra } });

    const MULTIROOM_STATE = {
      mode: 'multiroom',
      global_volume_db: -27.5,
      global_mute: false,
      volume_control: true,
      any_volume_control: true,
      clients: {
        'dc:a6:32:7e:d3:43': { volume_db: -25, offset_db: 0, mute: false, available: true },
      },
      zones: {
        'zone-uuid-123': {
          id: 'zone-uuid-123',
          name: 'Living Room',
          client_ids: ['dc:a6:32:7e:d3:43'],
          average_volume_db: -27.5,
          all_muted: false,
        },
      },
    };

    afterEach(() => {
      vi.useRealTimers();
    });

    it('mirrors clients and zones from the broadcast state', () => {
      store.handleVolumeEvent(volumeEvent(MULTIROOM_STATE));

      expect(store.volumeState.mode).toBe('multiroom');
      expect(store.volumeState.clients['dc:a6:32:7e:d3:43'].volume_db).toBe(-25);
      expect(store.volumeState.zones['zone-uuid-123'].average_volume_db).toBe(-27.5);
    });

    it('fills schema defaults for client fields the backend omitted', () => {
      store.handleVolumeEvent(volumeEvent({
        ...MULTIROOM_STATE,
        clients: { 'dc:a6:32:7e:d3:43': { volume_db: -25 } },
      }));

      const client = store.volumeState.clients['dc:a6:32:7e:d3:43'];
      expect(client.offset_db).toBe(0);
      expect(client.mute).toBe(false);
      expect(client.available).toBe(true);
    });

    it('drops a client map that fails validation without wiping the rest of the state', () => {
      store.handleVolumeEvent(volumeEvent(MULTIROOM_STATE));
      store.handleVolumeEvent(volumeEvent({
        ...MULTIROOM_STATE,
        global_volume_db: -10,
        clients: { 'dc:a6:32:7e:d3:43': { volume_db: 'loud' } },
      }));

      // .catch({}) on the clients record: the bad map is replaced by {}, but the
      // scalar fields of the same event still apply.
      expect(store.volumeState.clients).toEqual({});
      expect(store.volumeState.global_volume_db).toBe(-10);
    });

    it('updates step_mobile_db when the event carries one', () => {
      store.handleVolumeEvent(volumeEvent(MULTIROOM_STATE, { step_mobile_db: 5.0 }));

      expect(store.volumeState.step_mobile_db).toBe(5.0);
    });

    it('shows the volume bar and auto-hides it after 3s', () => {
      vi.useFakeTimers();

      store.handleVolumeEvent(volumeEvent(MULTIROOM_STATE, { show_bar: true }));
      expect(store.showVolumeBar).toBe(true);

      vi.advanceTimersByTime(2999);
      expect(store.showVolumeBar).toBe(true);
      vi.advanceTimersByTime(1);
      expect(store.showVolumeBar).toBe(false);
    });

    it('keeps the volume bar hidden when show_bar is false', () => {
      store.handleVolumeEvent(volumeEvent(MULTIROOM_STATE, { show_bar: false }));

      expect(store.showVolumeBar).toBe(false);
    });

    it('restarts the auto-hide countdown on a second event', () => {
      vi.useFakeTimers();

      store.handleVolumeEvent(volumeEvent(MULTIROOM_STATE, { show_bar: true }));
      vi.advanceTimersByTime(2000);
      store.handleVolumeEvent(volumeEvent(MULTIROOM_STATE, { show_bar: true }));

      vi.advanceTimersByTime(2000);
      expect(store.showVolumeBar).toBe(true);
      vi.advanceTimersByTime(1000);
      expect(store.showVolumeBar).toBe(false);
    });

    it('hideVolumeBar cancels the pending auto-hide', () => {
      vi.useFakeTimers();

      store.handleVolumeEvent(volumeEvent(MULTIROOM_STATE, { show_bar: true }));
      store.hideVolumeBar();
      expect(store.showVolumeBar).toBe(false);

      // The cancelled timer must not fire and re-hide (or flip) the bar later.
      vi.advanceTimersByTime(5000);
      expect(store.showVolumeBar).toBe(false);
    });
  });

  describe('sendCommand', () => {
    it('posts the generic control envelope { command, data }', async () => {
      // The /api/audio/control/{source} envelope is the command contract shared by
      // Family A sources and Milo-Mac — its shape is worth pinning.
      await store.sendCommand('spotify', 'play', { track_id: '123' });

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/audio/control/spotify',
        { command: 'play', data: { track_id: '123' } },
        expect.objectContaining({ checkStatus: true }),
      );
    });

    it('defaults data to an empty object', async () => {
      await store.sendCommand('spotify', 'pause');

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/audio/control/spotify',
        { command: 'pause', data: {} },
        expect.anything(),
      );
    });

    it('records commandError on failure so App.vue can surface it', async () => {
      apiCall.post.mockResolvedValueOnce(fail('Source not running'));

      const result = await store.sendCommand('spotify', 'play');

      expect(result).toBe(false);
      expect(store.commandError).toEqual({ source: 'spotify', command: 'play' });
    });

    it('leaves commandError untouched on success', async () => {
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      const result = await store.sendCommand('spotify', 'play');

      expect(result).toBe(true);
      expect(store.commandError).toBeNull();
    });
  });

  describe('disconnectSource', () => {
    it('routes bluetooth through the generic control endpoint', async () => {
      // The dedicated /api/bluetooth router was retired; Family A commands all
      // travel over /api/audio/control/{source}.
      await store.disconnectSource('bluetooth');

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/audio/control/bluetooth',
        { command: 'disconnect', data: {} },
        expect.anything(),
      );
    });

    it('is a no-op for "none"', async () => {
      const result = await store.disconnectSource('none');

      expect(result).toBe(false);
      expect(apiCall.post).not.toHaveBeenCalled();
    });
  });
});
