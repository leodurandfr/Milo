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
