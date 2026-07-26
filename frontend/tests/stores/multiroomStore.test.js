// frontend/tests/stores/multiroomStore.test.js
/**
 * multiroomStore is the client/zone registry. Its state is delta-fed: every
 * WS event mutates a Map that a computed derives from, so the tests drive the
 * handlers and read the public computeds — the same path App.vue takes.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { i18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const CLIENT = (macId, extra = {}) => ({
  mac_id: macId,
  snapcast_id: macId,
  name: `Client ${macId}`,
  online: true,
  ...extra,
});

const clientEvent = (macId, client) => ({
  type: 'client_state_changed',
  data: client === undefined ? { mac_id: macId } : { mac_id: macId, client },
});

const zoneEvent = (zoneId, zone) => ({
  type: 'zone_changed',
  data: { zone_id: zoneId, zone },
});

describe('multiroomStore', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useMultiroomStore();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('client registry', () => {
    it('adds a client on client_state_changed', () => {
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a', { name: 'Living Room' })));

      expect(store.clientList).toHaveLength(1);
      expect(store.clientList[0]).toMatchObject({ mac_id: 'mac-a', name: 'Living Room', online: true });
    });

    it('updates an existing client in place', () => {
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a', { name: 'Old' })));
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a', { name: 'New', online: false })));

      expect(store.clientList).toHaveLength(1);
      expect(store.clientList[0]).toMatchObject({ name: 'New', online: false });
    });

    it('deletes the client when the event carries no client object', () => {
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a')));

      store.handleMultiroomEvent(clientEvent('mac-a'));

      expect(store.clientList).toHaveLength(0);
    });

    it('strips volume and mute — those live in volumeState, not the registry', () => {
      // Two sources of truth for volume is exactly the drift this guards against.
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a', { volume_db: -25, mute: true })));

      expect(store.clientList[0]).not.toHaveProperty('volume_db');
      expect(store.clientList[0]).not.toHaveProperty('mute');
    });

    it('ignores an unknown event type', () => {
      expect(() => store.handleMultiroomEvent({ type: 'something_else', data: {} })).not.toThrow();
      expect(store.clientList).toHaveLength(0);
    });

    it('isClientOnline reports false for an unknown client', () => {
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a', { online: false })));

      expect(store.isClientOnline('mac-a')).toBe(false);
      expect(store.isClientOnline('never-seen')).toBe(false);
    });
  });

  describe('clientList ordering', () => {
    it('puts the local client first, then online clients, then offline ones', () => {
      store.handleMultiroomEvent(clientEvent('mac-z', CLIENT('mac-z', { name: 'Zulu' })));
      store.handleMultiroomEvent(clientEvent('mac-off', CLIENT('mac-off', { name: 'Alpha', online: false })));
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a', { name: 'Bravo' })));
      store.handleMultiroomEvent(clientEvent('mac-local', CLIENT('mac-local', { name: 'Milo', is_local: true })));

      expect(store.clientList.map(c => c.name)).toEqual(['Milo', 'Bravo', 'Zulu', 'Alpha']);
    });

    it('sorts case-insensitively and falls back to the mac when unnamed', () => {
      store.handleMultiroomEvent(clientEvent('mac-b', CLIENT('mac-b', { name: 'apple' })));
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a', { name: 'Banana' })));
      store.handleMultiroomEvent(clientEvent('aa-nameless', CLIENT('aa-nameless', { name: undefined })));

      expect(store.clientList.map(c => c.name ?? c.mac_id)).toEqual(['aa-nameless', 'apple', 'Banana']);
    });
  });

  describe('zones', () => {
    it('creates and updates a zone', () => {
      store.handleMultiroomEvent(zoneEvent('z1', { id: 'z1', name: 'Living', client_ids: ['mac-a'] }));
      expect(store.zoneList).toHaveLength(1);

      store.handleMultiroomEvent(zoneEvent('z1', { id: 'z1', name: 'Living Extended', client_ids: ['mac-a', 'mac-b'] }));

      expect(store.zoneList).toHaveLength(1);
      expect(store.zoneList[0].name).toBe('Living Extended');
      expect(store.zoneList[0].client_ids).toHaveLength(2);
    });

    it('deletes the zone when the event carries a null zone', () => {
      store.handleMultiroomEvent(zoneEvent('z1', { id: 'z1', name: 'Living', client_ids: ['mac-a'] }));

      store.handleMultiroomEvent(zoneEvent('z1', null));

      expect(store.zoneList).toHaveLength(0);
    });

    it('resolves the zone of a member and leaves a standalone client alone', () => {
      store.handleMultiroomEvent(zoneEvent('z1', { id: 'z1', name: 'Living', client_ids: ['mac-a', 'mac-b'] }));

      expect(store.getZoneForClient('mac-a').id).toBe('z1');
      expect(store.getZoneForClient('mac-solo')).toBeNull();
      expect(store.getLinkedClientIds('mac-a')).toEqual(['mac-a', 'mac-b']);
      expect(store.getLinkedClientIds('mac-solo')).toEqual(['mac-solo']);
    });

    it('hasOnlineSubwoofer requires a subwoofer that is actually online', () => {
      store.handleMultiroomEvent(zoneEvent('z1', { id: 'z1', client_ids: ['mac-sub', 'mac-sat'] }));
      store.handleMultiroomEvent(clientEvent('mac-sat', CLIENT('mac-sat', { speaker_type: 'satellite' })));

      store.handleMultiroomEvent(clientEvent('mac-sub', CLIENT('mac-sub', { speaker_type: 'subwoofer', online: false })));
      expect(store.hasOnlineSubwoofer('z1')).toBe(false);

      store.handleMultiroomEvent(clientEvent('mac-sub', CLIENT('mac-sub', { speaker_type: 'subwoofer', online: true })));
      expect(store.hasOnlineSubwoofer('z1')).toBe(true);
    });

    it('hasOnlineSubwoofer is false for an unknown zone', () => {
      expect(store.hasOnlineSubwoofer('nope')).toBe(false);
    });
  });

  describe('pending clients', () => {
    it('registers and then removes a pending client', () => {
      store.handleMultiroomEvent({
        type: 'pending_client_changed',
        data: { action: 'registered', client: { mac_id: 'mac-new', name: 'New satellite' } },
      });
      expect(store.pendingClientList).toHaveLength(1);

      store.handleMultiroomEvent({
        type: 'pending_client_changed',
        data: { action: 'removed', mac_id: 'mac-new' },
      });

      expect(store.pendingClientList).toHaveLength(0);
    });

    it('clears the configuring flag when the pending client is removed', () => {
      store.handleMultiroomEvent({
        type: 'pending_client_changed',
        data: { action: 'registered', client: { mac_id: 'mac-new' } },
      });
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));
      store.configurePendingClient('mac-new', {});
      expect(store.isClientConfiguring('mac-new')).toBe(true);

      store.handleMultiroomEvent({
        type: 'pending_client_changed',
        data: { action: 'removed', mac_id: 'mac-new' },
      });

      expect(store.isClientConfiguring('mac-new')).toBe(false);
    });
  });

  describe('routing transitions', () => {
    it('tracks enabling and disabling as a transition', () => {
      store.handleRoutingEvent({ type: 'multiroom_enabling', data: {} });
      expect(store.isTransitioning).toBe(true);
      expect(store.transitionState).toBe('enabling');

      store.handleRoutingEvent({ type: 'multiroom_ready', data: {} });
      expect(store.isTransitioning).toBe(false);
      expect(store.transitionState).toBe('idle');
    });

    it('resolves the backend reason code to a localized message', async () => {
      // The backend sends a stable code; every consumer must show the same text.
      await i18n.loadTranslations('english');

      store.handleRoutingEvent({ type: 'multiroom_error', data: { reason: 'enable_failed' } });

      expect(store.transitionState).toBe('error');
      expect(store.transitionError).toBe('Could not enable multiroom');
    });

    it('leaves the message empty for an unmapped reason', () => {
      store.handleRoutingEvent({ type: 'multiroom_error', data: { reason: 'who_knows' } });

      expect(store.transitionState).toBe('error');
      expect(store.transitionError).toBe('');
    });

    it('gives up after 15s when no terminal event arrives', () => {
      vi.useFakeTimers();

      store.handleRoutingEvent({ type: 'multiroom_enabling', data: {} });
      vi.advanceTimersByTime(15000);

      expect(store.transitionState).toBe('error');
    });

    it('cancels the timeout once the transition completes', () => {
      vi.useFakeTimers();

      store.handleRoutingEvent({ type: 'multiroom_enabling', data: {} });
      store.handleRoutingEvent({ type: 'multiroom_ready', data: {} });
      vi.advanceTimersByTime(30000);

      expect(store.transitionState).toBe('idle');
    });

    it('resetTransition also cancels a pending timeout', () => {
      vi.useFakeTimers();

      store.handleRoutingEvent({ type: 'multiroom_disabling', data: {} });
      store.resetTransition();
      vi.advanceTimersByTime(30000);

      expect(store.transitionState).toBe('idle');
      expect(store.transitionError).toBe('');
    });
  });

  describe('fetchState', () => {
    it('replaces both maps and strips runtime fields', async () => {
      store.handleMultiroomEvent(clientEvent('stale', CLIENT('stale')));
      apiCall.get.mockResolvedValueOnce(ok({
        clients: { 'mac-a': { ...CLIENT('mac-a'), volume_db: -25, mute: false } },
        zones: { z1: { id: 'z1', name: 'Living', client_ids: ['mac-a'] } },
      }));

      await store.fetchState();

      expect(store.clientList.map(c => c.mac_id)).toEqual(['mac-a']);
      expect(store.clientList[0]).not.toHaveProperty('volume_db');
      expect(store.zoneList).toHaveLength(1);
      expect(store.isLoading).toBe(false);
    });

    it('keeps the current registry when the fetch fails', async () => {
      store.handleMultiroomEvent(clientEvent('mac-a', CLIENT('mac-a')));
      apiCall.get.mockResolvedValueOnce({ ok: false, data: null, error: { detail: 'boom', status: 500 } });

      await store.fetchState();

      expect(store.clientList).toHaveLength(1);
      expect(store.isLoading).toBe(false);
    });
  });

  describe('initialize', () => {
    /**
     * App.vue decides a `pending_client_changed` announces a *new* speaker with
     * `!pendingClients.has(mac)`, and acts on it by waking the screen and opening
     * Settings. A satellite re-registers every 15s and the backend rebroadcasts
     * action="registered" each time, so leaving the map empty at boot makes the
     * next heartbeat of a long-known satellite look brand new — once per page load.
     */
    it('populates pending clients, not just the registry', async () => {
      apiCall.get.mockImplementation(async (url) => {
        if (url === '/api/multiroom/state') {
          return ok({ clients: { 'mac-a': CLIENT('mac-a') }, zones: {} });
        }
        if (url === '/api/multiroom/pending-clients') {
          return ok({ clients: { 'mac-p': { mac_id: 'mac-p', ip: '192.168.1.9' } } });
        }
        return ok({});
      });

      await store.initialize();

      expect(store.clientList.map(c => c.mac_id)).toEqual(['mac-a']);
      expect(store.pendingClients.has('mac-p')).toBe(true);
      expect(store.isInitialized).toBe(true);
    });
  });
});
