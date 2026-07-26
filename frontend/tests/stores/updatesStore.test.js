// frontend/tests/stores/updatesStore.test.js
/**
 * updatesStore mirrors program/satellite update state, fed by `programs.*` WS
 * deltas and reconciled from GET /api/programs.
 *
 * What is pinned here is the resync gate: App.vue resyncs every delta store on
 * reconnect and on every tab return, and this one is loaded lazily by
 * UpdateManager. Each refetch costs an installed-version probe per program on
 * the Pi, so resyncing a store no view has opened is pure cost.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
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
});
