// frontend/tests/stores/systemStore.test.js
/**
 * The SSH / account password panel spans two views: System, which reads the
 * SSH state when it mounts, and its password sub-view, which saves. Going back
 * while a save is in flight mounts System mid-save; if its read reached the
 * backend first it answered "factory password" and the warning stayed up until
 * the next visit, after a password had been set.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useSystemStore } from '@/stores/systemStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const SSH_OPEN_CHANGED = { status: 'success', data: { enabled: true, active: true, password_is_default: false } };

describe('systemStore SSH panel', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useSystemStore();
  });

  it('holds a read issued during a password save until the save has answered', async () => {
    let answerSave;
    apiCall.post.mockReturnValueOnce(new Promise(resolve => { answerSave = resolve; }));
    apiCall.get.mockResolvedValue(ok(SSH_OPEN_CHANGED));

    const saving = store.setPassword('correct horse');
    const reading = store.loadSsh();
    await Promise.resolve();

    expect(apiCall.get).not.toHaveBeenCalled();

    answerSave(ok({ status: 'success', data: { password_is_default: false } }));
    await Promise.all([saving, reading]);

    expect(apiCall.get).toHaveBeenCalledWith('/api/system/ssh', expect.any(Object));
    expect(store.ssh.passwordIsDefault).toBe(false);
  });

  it('keeps the factory warning when the save fails', async () => {
    apiCall.post.mockResolvedValueOnce(fail('helper refused'));

    expect(await store.setPassword('correct horse')).toBe(false);
    expect(store.ssh.passwordIsDefault).toBe(true);
  });

  it('re-reads the state when the SSH switch is refused', async () => {
    apiCall.put.mockResolvedValueOnce(fail('unit refused'));
    apiCall.get.mockResolvedValueOnce(ok(SSH_OPEN_CHANGED));

    expect(await store.setSshEnabled(false)).toBe(false);
    expect(store.ssh.enabled).toBe(true);
  });
});

/**
 * The missing-card banner is dismissable because a user may have picked the
 * card in the wizard while waiting for the board. GET /api/system/status keeps
 * naming it, and systemStore is replayed on every tab return and reconnect, so
 * a dismissal the store does not remember comes back at the next phone unlock.
 */
describe('systemStore missing audio card', () => {
  let store;

  const status = (card) => ok({ status: 'success', data: { hostname_conflict: false, audio_card_missing: card } });

  beforeEach(() => {
    resetApiCallMock();
    store = useSystemStore();
  });

  it('a dismissed missing-card banner stays dismissed across a resync', async () => {
    apiCall.get.mockResolvedValue(status('HiFiBerry Amp2'));
    await store.resync();
    expect(store.audioCardMissing).toBe('HiFiBerry Amp2');

    store.dismissAudioCardMissing();
    expect(store.audioCardMissing).toBe(null);

    await store.resync();
    expect(store.audioCardMissing).toBe(null);
  });

  it('the card coming back, then going missing again, re-arms the banner', async () => {
    apiCall.get.mockResolvedValue(status('HiFiBerry Amp2'));
    await store.resync();
    store.dismissAudioCardMissing();

    apiCall.get.mockResolvedValue(status('IQaudio DAC+'));
    await store.resync();
    expect(store.audioCardMissing).toBe('IQaudio DAC+');

    store.dismissAudioCardMissing();
    apiCall.get.mockResolvedValue(status(null));
    await store.resync();
    expect(store.audioCardMissing).toBe(null);

    apiCall.get.mockResolvedValue(status('IQaudio DAC+'));
    await store.resync();
    expect(store.audioCardMissing).toBe('IQaudio DAC+');
  });

  it('a dismissal ends once another card is reported, so the first one shows again', async () => {
    apiCall.get.mockResolvedValue(status('HiFiBerry Amp2'));
    await store.resync();
    store.dismissAudioCardMissing();

    apiCall.get.mockResolvedValue(status('IQaudio DAC+'));
    await store.resync();
    apiCall.get.mockResolvedValue(status('HiFiBerry Amp2'));
    await store.resync();
    expect(store.audioCardMissing).toBe('HiFiBerry Amp2');
  });
});

/**
 * A unit set up over its recovery AP and not joined to its network since opens
 * the wizard on its Network step. The hostname-conflict event is applied
 * through the same path as the status read and carries no such key: read as
 * "not waiting", it would send the wizard back to its first step.
 */
describe('systemStore setup waiting for a link', () => {
  beforeEach(() => {
    resetApiCallMock();
  });

  it('is left as read by a hostname-conflict event', async () => {
    const store = useSystemStore();
    apiCall.get.mockResolvedValue(ok({ status: 'success', data: { hostname_conflict: false, setup_awaiting_network: true } }));
    await store.fetchStatus();

    store.handleConflictEvent({ data: { hostname_conflict: false, advertised_name: 'milo.local' } });

    expect(store.setupAwaitingNetwork).toBe(true);
  });
});

/**
 * The files the backend set aside are kept there until the owner closes the
 * banner naming them: a boot's set-asides happen before any page listens, so
 * the resync is how a page learns of them, and a read that failed must say so
 * or serverSync never asks again — the banner would never come.
 */
describe('systemStore set-aside notices', () => {
  const NOTICE_A = { id: 'a', file: '/var/lib/milo/settings.json', kept_as: 'settings.json.x.bad', reason: 'not JSON', at: 1 };
  const NOTICE_B = { id: 'b', file: '/var/lib/milo/radio_data.json', kept_as: 'radio_data.json.x.bad', reason: 'not JSON', at: 2 };

  const answers = ({ notices }) => (url) => Promise.resolve(url === '/api/system/notices'
    ? notices
    : ok({ status: 'success', data: { hostname_conflict: false, audio_card_missing: null } }));

  beforeEach(() => {
    resetApiCallMock();
  });

  it('a resync reads the notices, and fails when they could not be read', async () => {
    const store = useSystemStore();
    apiCall.get.mockImplementation(answers({ notices: ok({ status: 'success', data: { notices: [NOTICE_A] } }) }));
    expect(await store.resync()).toBe(true);
    expect(store.notices).toEqual([NOTICE_A]);

    apiCall.get.mockImplementation(answers({ notices: fail('backend restarting', 502) }));
    expect(await store.resync()).toBe(false);
    expect(store.notices).toEqual([NOTICE_A]);
  });

  it('closing the banner acknowledges every notice it named', async () => {
    const store = useSystemStore();
    store.applyNotices([NOTICE_A, NOTICE_B]);
    apiCall.delete.mockResolvedValue(ok({ status: 'success' }));

    await store.acknowledgeNotices();

    expect(apiCall.delete.mock.calls.map(([url]) => url))
      .toEqual(['/api/system/notices/a', '/api/system/notices/b']);
    expect(store.notices).toEqual([]);
    expect(apiCall.get).not.toHaveBeenCalled();
  });

  it('the announcement of one acknowledgement does not bring back the others in flight', async () => {
    const store = useSystemStore();
    store.applyNotices([NOTICE_A, NOTICE_B]);
    const shownDuring = [];
    apiCall.delete.mockImplementation((url) => {
      // The backend announces each acknowledgement with what is left.
      if (url.endsWith('/a')) store.handleNoticesEvent({ data: { notices: [NOTICE_B] } });
      shownDuring.push(store.notices.map(({ id }) => id));
      return Promise.resolve(ok({ status: 'success' }));
    });

    await store.acknowledgeNotices();
    store.handleNoticesEvent({ data: { notices: [] } });

    expect(shownDuring).toEqual([[], []]);
    expect(store.notices).toEqual([]);
  });

  it('an acknowledgement refused for another reason than a 404 reads the list back', async () => {
    const store = useSystemStore();
    store.applyNotices([NOTICE_A, NOTICE_B]);
    apiCall.delete
      .mockResolvedValueOnce(fail('already acknowledged elsewhere', 404))
      .mockResolvedValueOnce(fail('card full', 500));
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', data: { notices: [NOTICE_B] } }));

    await store.acknowledgeNotices();

    expect(store.notices).toEqual([NOTICE_B]);
  });
});
