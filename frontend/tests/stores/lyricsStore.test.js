// frontend/tests/stores/lyricsStore.test.js
/**
 * syncOffsetMs is what lines up the highlight with the sound. Its sign is the
 * whole point: multiroom plays every chunk `buffer_ms` AFTER the source wrote
 * it, so the offset must be negative. It was positive once, which put the lyrics
 * ~1.2 s ahead of the room on the default preset.
 *
 * Pins the store half only — that the offset is negative, tracks the configured
 * buffer, stays out of direct mode, survives a failed read and is asked for
 * again when multiroom appears under an open view. The single site that applies
 * it (`LyricsContent.vue`, `currentPosition + syncOffsetMs`) is not covered:
 * reaching it means mounting the component, which this suite does not do.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useLyricsStore } from '@/stores/lyricsStore';
import { nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const serverConfig = (bufferMs) => ok({
  config: { buffer_ms: bufferMs, codec: 'flac', chunk_ms: 40, sampleformat: '48000:32:2' },
  capabilities: { codecs: ['flac'], presets: [] },
});

describe('lyricsStore — sync offset', () => {
  let store;
  let unified;

  beforeEach(() => {
    resetApiCallMock();
    store = useLyricsStore();
    unified = useUnifiedAudioStore();
    unified.systemState.multiroom_enabled = true;
  });

  it('holds the highlight back by the configured snapcast buffer', async () => {
    apiCall.get.mockResolvedValueOnce(serverConfig(700));
    await store.loadSyncOffset();

    expect(store.syncOffsetMs).toBeLessThan(0);
    expect(store.syncOffsetMs).toBe(-700);
  });

  it('follows the buffer instead of assuming one preset', async () => {
    apiCall.get.mockResolvedValueOnce(serverConfig(180));
    await store.loadSyncOffset();
    expect(store.syncOffsetMs).toBe(-180);

    apiCall.get.mockResolvedValueOnce(serverConfig(1500));
    await store.loadSyncOffset();
    expect(store.syncOffsetMs).toBe(-1500);
  });

  it('applies no offset in direct mode, and reads nothing', async () => {
    apiCall.get.mockResolvedValueOnce(serverConfig(700));
    await store.loadSyncOffset();

    unified.systemState.multiroom_enabled = false;
    expect(store.syncOffsetMs).toBe(0);

    apiCall.get.mockClear();
    await store.loadSyncOffset();
    expect(apiCall.get).not.toHaveBeenCalled();
  });

  it('reads the buffer when multiroom appears under an already-open view', async () => {
    // No metadata → loadLyrics() returns before any request, so the only call
    // this test can produce is the server-config one.
    unified.systemState.multiroom_enabled = false;
    store.open();
    expect(store.syncOffsetMs).toBe(0);

    apiCall.get.mockResolvedValueOnce(serverConfig(700));
    unified.systemState.multiroom_enabled = true;
    await nextTick();
    await vi.waitFor(() => expect(store.syncOffsetMs).toBe(-700));
  });

  it('keeps the last known buffer when the read fails', async () => {
    apiCall.get.mockResolvedValueOnce(serverConfig(700));
    await store.loadSyncOffset();

    // Snapserver restarting is exactly when this read fails.
    apiCall.get.mockResolvedValueOnce(fail('Snapcast server not available', 502));
    await store.loadSyncOffset();
    expect(store.syncOffsetMs).toBe(-700);
  });
});
