// frontend/tests/stores/multiroomClientSsh.test.js
/**
 * A satellite's SSH panel spans two views, like the server's: the client panel
 * reads the state when it mounts, its password sub-view saves. Going back while
 * a save is in flight remounts the panel mid-save; if its read reached the
 * satellite first it answered "factory password" and the warning stayed up
 * after a password had been set. The wait is per satellite: a save on one
 * speaker must not hold the panel of another.
 *
 * Opening a satellite's SSH and setting its password are root on the speaker,
 * so the server relays them only once a gesture on itself confirms them (a
 * 428 that names the challenge): sent through anything but the proof of
 * presence, both are refused for good and the panel says nothing of why.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { usePresenceStore } from '@/stores/presenceStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
vi.mock('@/utils/kiosk', () => ({ isKiosk: () => false }));

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

  it.each([
    ['opening SSH', 'put', 'satellite_ssh_open', (store) => store.setClientSsh(KITCHEN, true)],
    ['the password', 'post', 'satellite_password', (store) => store.setClientPassword(KITCHEN, 'correct horse')],
  ])('relays %s only with the token of the gesture the unit confirmed', async (_, verb, action, send) => {
    const view = (status) => ({ id: 'c1', action, gesture: 'rotary', status, remaining_s: 120, cooldown_s: 300 });
    apiCall[verb].mockReset();
    apiCall[verb]
      .mockResolvedValueOnce(fail({ challenge: view('pending'), token: 'token-of-c1', header: 'X-Milo-Presence' }, 428))
      .mockResolvedValueOnce(ok(SSH_OPEN_CHANGED));

    const pending = send(store);
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
    expect(apiCall[verb]).toHaveBeenCalledTimes(1);

    usePresenceStore().handleChallengeEvent({ data: { challenge: view('confirmed') } });
    expect(await pending).toBeTruthy();

    expect(apiCall[verb]).toHaveBeenCalledTimes(2);
    expect(apiCall[verb].mock.calls[1][2].headers).toEqual({ 'X-Milo-Presence': 'token-of-c1' });
  });
});
