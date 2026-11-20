// frontend/tests/stores/presenceStore.test.js
/**
 * The proof of presence, from the tab that asked: a request the unit answers
 * 428 waits for the gesture on the unit, then goes again with the token the
 * 428 carried — and only then. Sent early, it opens nothing; never sent, the
 * owner makes the gesture for nothing; sent on another tab's confirmation, it
 * spends a proof that tab was waiting for. A power cut takes the socket down,
 * so the confirmation it proves reaches the page only through the resync.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ref } from 'vue';
import { usePresenceStore, GESTURES_HEADER } from '@/stores/presenceStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
const kiosk = ref(false);
vi.mock('@/utils/kiosk', () => ({ isKiosk: () => kiosk.value }));

const HEADER = 'X-Milo-Presence';

function challenge(status = 'pending', id = 'c1') {
  return { id, action: 'ssh_open', gesture: 'rotary', status, remaining_s: 120, cooldown_s: 300 };
}

/** A promise the test settles itself: an answer still on its way. */
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function refusal(id = 'c1', token = `token-of-${id}`) {
  return fail({ message: 'Confirm this on the unit itself', challenge: challenge('pending', id), token, header: HEADER }, 428);
}

const event = (view) => ({ data: { challenge: view } });

async function flush() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

describe('presenceStore', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    kiosk.value = false;
    store = usePresenceStore();
  });

  it('sends the request again with its token once the unit confirms, and not before', async () => {
    apiCall.put.mockResolvedValueOnce(refusal()).mockResolvedValueOnce(ok({ status: 'success' }));

    const pending = store.request('put', '/api/system/ssh', { enabled: true }, { category: 'system' });
    await flush();

    expect(store.challenge).toMatchObject({ id: 'c1', gesture: 'rotary', status: 'pending' });
    expect(apiCall.put).toHaveBeenCalledTimes(1);

    store.handleChallengeEvent(event(challenge('confirmed')));
    const result = await pending;

    expect(result.ok).toBe(true);
    expect(apiCall.put).toHaveBeenCalledTimes(2);
    expect(apiCall.put.mock.calls[1][2].headers).toEqual({ [HEADER]: 'token-of-c1' });
    expect(apiCall.put.mock.calls[0][2].headers).toBeUndefined();
    expect(store.challenge).toBeNull();
  });

  it("another tab's confirmation spends nothing here", async () => {
    apiCall.post.mockResolvedValueOnce(refusal('mine'));

    store.request('post', '/api/system/password', { password: 'correct horse' });
    await flush();
    store.handleChallengeEvent(event(challenge('confirmed', 'theirs')));
    await flush();

    expect(apiCall.post).toHaveBeenCalledTimes(1);
  });

  it('a challenge that ends unconfirmed answers nothing to show, and says so in the dialog', async () => {
    apiCall.post.mockResolvedValueOnce(refusal());
    const errorRef = ref(null);

    const pending = store.request('post', '/api/system/reset-setup', null, { errorRef });
    await flush();
    store.handleChallengeEvent(event(null));
    const result = await pending;

    expect(result).toEqual({ ok: false, data: null, error: null });
    expect(errorRef.value).toBeNull();
    expect(store.challenge.status).toBe('expired');
    store.dismiss();
    expect(store.challenge).toBeNull();
  });

  it('a confirmation made while the socket was down reaches the request through the resync', async () => {
    apiCall.put.mockResolvedValueOnce(refusal()).mockResolvedValueOnce(ok({ status: 'success' }));
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', data: { challenge: challenge('confirmed') } }));

    const pending = store.request('put', '/api/system/ssh', { enabled: true });
    await flush();
    await store.resync();

    expect((await pending).ok).toBe(true);
    expect(apiCall.get).toHaveBeenCalledWith('/api/system/presence', expect.any(Object));
  });

  it('cancelling tells the unit, with the token, and answers nothing', async () => {
    apiCall.post.mockResolvedValueOnce(refusal());

    const pending = store.request('post', '/api/programs/go-librespot/update', { target: 'upstream' });
    await flush();
    await store.cancel();

    expect((await pending).ok).toBe(false);
    expect(apiCall.delete).toHaveBeenCalledWith(
      '/api/system/presence', expect.objectContaining({ headers: { [HEADER]: 'token-of-c1' } }),
    );
    expect(store.challenge).toBeNull();
  });

  it("a cancel closes the dialog even when the unit's word of the end comes before the DELETE's answer", async () => {
    // The unit publishes the end, then answers the DELETE: read by a request
    // still waiting, that end would say "not confirmed" over a cancel.
    apiCall.post.mockResolvedValueOnce(refusal());
    const answer = deferred();
    apiCall.delete.mockReturnValueOnce(answer.promise);

    const pending = store.request('post', '/api/system/password', { password: 'correct horse' });
    await flush();
    const cancelling = store.cancel();
    store.handleChallengeEvent(event(null));
    answer.resolve(ok({ status: 'success' }));
    await cancelling;

    expect(await pending).toEqual({ ok: false, data: null, error: null });
    expect(store.challenge).toBeNull();
  });

  it("the challenge this tab's own request opens is never drawn as another device's", async () => {
    // The unit publishes a challenge before it answers the 428 that carries
    // its token: in between, the unit's own screen would say a stranger asked.
    const answer = deferred();
    apiCall.put.mockReturnValueOnce(answer.promise);

    const pending = store.request('put', '/api/system/ssh', { enabled: true });
    await flush();
    store.handleChallengeEvent(event(challenge('pending', 'c1')));

    expect(store.othersChallenge).toBeNull();

    answer.resolve(refusal('c1'));
    await flush();
    expect(store.othersChallenge).toBeNull();
    expect(store.challenge).toMatchObject({ id: 'c1', status: 'pending' });

    store.cancel();
    await pending;
    store.handleChallengeEvent(event(challenge('pending', 'theirs')));
    expect(store.othersChallenge).toMatchObject({ id: 'theirs' });
  });

  it("the unit's own screen says which gestures it can wait through", async () => {
    kiosk.value = true;
    apiCall.put.mockResolvedValueOnce(ok({ status: 'success' }));

    await store.request('put', '/api/system/ssh', { enabled: true });

    expect(apiCall.put.mock.calls[0][2].headers[GESTURES_HEADER]).toBeTruthy();
  });

  it('a gesture this screen cannot wait through sends the owner elsewhere, with nothing to wait for', async () => {
    kiosk.value = true;
    const errorRef = ref(null);
    apiCall.put.mockResolvedValueOnce(fail(
      { message: 'Confirm this from another device', action: 'ssh_open', gesture: 'power_cycle' }, 428,
    ));

    const result = await store.request('put', '/api/system/ssh', { enabled: true }, { errorRef });

    expect(result).toEqual({ ok: false, data: null, error: null });
    expect(errorRef.value).toBeNull();
    expect(store.challenge).toMatchObject({ action: 'ssh_open', gesture: 'power_cycle', status: 'elsewhere' });
    store.dismiss();
    expect(store.challenge).toBeNull();
  });

  it('a second request refused while the first waits leaves the first to finish', async () => {
    // Enter pressed twice on the password form: the unit refuses the second
    // (one challenge at a time), and the gesture still has to reach the first.
    apiCall.post
      .mockResolvedValueOnce(refusal())
      .mockResolvedValueOnce(fail('Another request is waiting for a gesture on the unit', 409))
      .mockResolvedValueOnce(ok({ status: 'success' }));

    const first = store.request('post', '/api/system/password', { password: 'correct horse' });
    await flush();
    const second = await store.request('post', '/api/system/password', { password: 'correct horse' });

    expect(second.ok).toBe(false);
    expect(store.challenge).toMatchObject({ id: 'c1', status: 'pending' });

    store.handleChallengeEvent(event(challenge('confirmed')));

    expect((await first).ok).toBe(true);
    expect(apiCall.post.mock.calls[2][2].headers).toEqual({ [HEADER]: 'token-of-c1' });
  });

  it("holds the unit's challenge whoever asked, for the unit's own screen", () => {
    store.handleChallengeEvent(event(challenge('pending', 'theirs')));

    expect(store.unitChallenge).toMatchObject({ id: 'theirs', action: 'ssh_open', status: 'pending' });
    expect(store.challenge).toBeNull();

    store.handleChallengeEvent(event(null));
    expect(store.unitChallenge).toBeNull();
  });

  it("any other refusal is the caller's, with the server's words", async () => {
    apiCall.put.mockResolvedValueOnce(fail('Set the device password before opening SSH', 409));
    const errorRef = ref(null);

    const result = await store.request('put', '/api/system/ssh', { enabled: true }, { errorRef });

    expect(result.ok).toBe(false);
    expect(errorRef.value).toBe('Set the device password before opening SSH');
    expect(store.challenge).toBeNull();
  });
});
