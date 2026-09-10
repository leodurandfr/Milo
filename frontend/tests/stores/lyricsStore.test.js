// frontend/tests/stores/lyricsStore.test.js
/**
 * syncOffsetMs is what lines up the highlight with the sound. Its sign is the
 * whole point: multiroom plays every chunk `buffer_ms` AFTER the source wrote
 * it, so the offset must be negative. It was positive once, which put the lyrics
 * ~1.2 s ahead of the room on the default preset.
 *
 * Pins the store half only — that the offset is negative, tracks the configured
 * buffer, stays out of direct mode, survives a failed read, is asked for again
 * when multiroom appears under an open view, and follows a buffer the settings
 * page applies over the top of that view (which closes nothing, so no edge
 * fires and a re-read would hit a restarting snapserver). The single site that applies
 * it (`LyricsContent.vue`, `currentPosition + syncOffsetMs + RENDER_LEAD_MS`,
 * the lead paying for the 800ms crossfade) is not covered: reaching it means
 * mounting the component, which this suite does not do.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useLyricsStore } from '@/stores/lyricsStore';
import { nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSnapcastStore } from '@/stores/snapcastStore';
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

  it('adopts a buffer the settings page applied, without re-reading', async () => {
    apiCall.get.mockResolvedValueOnce(serverConfig(180));
    await store.loadSyncOffset();
    expect(store.syncOffsetMs).toBe(-180);

    // The settings modal opens over the view rather than closing it, so no
    // (open x multiroom) edge fires and nothing would re-read. Re-reading is
    // also the wrong move here — the PUT restarts snapserver, so the GET would
    // answer config: null. The real snapcast store is driven, not stubbed.
    const snapcast = useSnapcastStore();
    snapcast.serverConfig.buffer_ms = 1500;
    apiCall.get.mockClear();
    apiCall.put.mockResolvedValueOnce(ok({ status: 'success' }));
    await snapcast.applyServerConfig();

    await vi.waitFor(() => expect(store.syncOffsetMs).toBe(-1500));
    expect(apiCall.get).not.toHaveBeenCalled();
  });

  it('asks the backend when the apply failed, never the edit buffer', async () => {
    apiCall.get.mockResolvedValueOnce(serverConfig(180));
    await store.loadSyncOffset();

    // The route is not atomic: it restarts snapserver on the new buffer_ms and
    // only then 502s if the local snapclient refuses ("Configuration saved
    // but … did not restart"). So a failure says nothing about which buffer is
    // live, and the edit buffer must never be adopted on this path — the
    // backend is asked, and here it reports the value step 4 did take.
    const snapcast = useSnapcastStore();
    snapcast.serverConfig.buffer_ms = 1500;
    apiCall.put.mockResolvedValueOnce(fail('Configuration saved but snapclient did not restart', 502));
    apiCall.get.mockResolvedValueOnce(serverConfig(1500));
    await snapcast.applyServerConfig();

    await vi.waitFor(() => expect(store.syncOffsetMs).toBe(-1500));
  });

  it('keeps the last buffer when the failed apply never reached the server', async () => {
    apiCall.get.mockResolvedValueOnce(serverConfig(180));
    await store.loadSyncOffset();

    // Same 502, other cause: the config was rejected before step 4, so the
    // server still runs the old buffer and says so.
    const snapcast = useSnapcastStore();
    snapcast.serverConfig.buffer_ms = 1500;
    apiCall.put.mockResolvedValueOnce(fail('Snapserver config update failed', 502));
    apiCall.get.mockResolvedValueOnce(serverConfig(180));
    await snapcast.applyServerConfig();
    await nextTick();

    expect(store.syncOffsetMs).toBe(-180);
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
