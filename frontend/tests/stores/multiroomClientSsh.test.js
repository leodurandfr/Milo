// frontend/tests/stores/multiroomClientSsh.test.js
/**
 * A satellite's SSH panel spans two views, like the server's: the client panel
 * reads the state when it mounts, its password sub-view saves. Going back while
 * a save is in flight remounts the panel mid-save; if its read reached the
 * satellite first it answered "factory password" and the warning stayed up
 * after a password had been set. The wait is per satellite: a save on one
 * speaker must not hold the panel of another.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const KITCHEN = 'dc:a6:32:7e:d3:43';
const STUDY = 'd8:3a:dd:68:e7:e4';
const SSH_OPEN_CHANGED = { status: 'success', data: { enabled: true, active: true, password_is_default: false } };

describe('multiroomStore satellite SSH panel', () => {
  let store;
  let answerSave;

  beforeEach(() => {
    resetApiCallMock();
    store = useMultiroomStore();
    apiCall.post.mockReturnValueOnce(new Promise(resolve => { answerSave = resolve; }));
    apiCall.get.mockResolvedValue(ok(SSH_OPEN_CHANGED));
  });

  it('holds a read of the same satellite until its password save has answered', async () => {
    const saving = store.setClientPassword(KITCHEN, 'correct horse');
    const reading = store.fetchClientSsh(KITCHEN);
    await Promise.resolve();

    expect(apiCall.get).not.toHaveBeenCalled();

    answerSave(ok({ status: 'success', data: { password_is_default: false } }));
    const [, state] = await Promise.all([saving, reading]);

    expect(state.passwordIsDefault).toBe(false);
  });

  it('does not hold another satellite behind the save', async () => {
    const saving = store.setClientPassword(KITCHEN, 'correct horse');
    const state = await store.fetchClientSsh(STUDY);

    expect(apiCall.get).toHaveBeenCalledWith(
      `/api/multiroom/clients/${encodeURIComponent(STUDY)}/ssh`, expect.any(Object),
    );
    expect(state.enabled).toBe(true);

    answerSave(fail('helper refused'));
    expect(await saving).toBe(false);
  });
});
