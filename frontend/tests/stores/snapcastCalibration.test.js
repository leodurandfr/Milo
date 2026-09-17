// frontend/tests/stores/snapcastCalibration.test.js
/**
 * The automatic Snapcast analysis, driven through the store's own handlers.
 *
 * Two properties carry this feature and neither is visible from the component:
 * the analysis must never write the configuration itself, and its state must
 * survive a tab that was backgrounded across a run.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { useSnapcastStore } from '@/stores/snapcastStore';

const PROPOSAL = {
  config: { buffer_ms: 180, codec: 'pcm', chunk_ms: 20, snapclient_buffer_time: 60 },
  predicted_latency_ms: 180,
  limiting_client: 'Canapé',
  reasons: [['chunk_tight', {}]],
  measurements: [{ mac_id: 'aa', name: 'Canapé', link: 'ethernet', rtt_max_ms: 0.4, loss_pct: 0 }],
  assumed: [],
};

function event(type, data) {
  return { category: 'routing', type, data };
}

describe('snapcastStore — automatic analysis', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    store = useSnapcastStore();
  });

  it('never writes the configuration when a proposal arrives', async () => {
    const before = { ...store.serverConfig };

    store.handleCalibrationEvent(event('calibration_result', PROPOSAL));

    expect(apiCall.put).not.toHaveBeenCalled();
    expect(store.serverConfig).toEqual(before);
    expect(store.calibration.result).toEqual(PROPOSAL);
  });

  it('loads the proposal into the edit buffer only when asked', () => {
    store.handleCalibrationEvent(event('calibration_result', PROPOSAL));

    store.stageCalibrationResult();

    expect(store.serverConfig.buffer_ms).toBe(180);
    expect(store.serverConfig.codec).toBe('pcm');
    expect(store.serverConfig.snapclient_buffer_time).toBe(60);
    expect(apiCall.put).not.toHaveBeenCalled();
  });

  it('refuses to start a second run while one is measuring', async () => {
    apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));
    await store.startCalibration();
    store.handleCalibrationEvent(event('calibration_progress', { stage: 'probing' }));

    const second = await store.startCalibration();

    expect(second).toBe(false);
    expect(apiCall.post).toHaveBeenCalledTimes(1);
  });

  it('clears the running flag when the request itself is refused', async () => {
    apiCall.post.mockResolvedValueOnce(fail('conflict', 409));

    const started = await store.startCalibration();

    expect(started).toBe(false);
    expect(store.calibration.running).toBe(false);
  });

  it('keeps the failing speaker so the message can name it', () => {
    store.handleCalibrationEvent(
      event('calibration_failed', { reason: 'probe_failed', detail: 'Bureau: timed out' })
    );

    expect(store.calibration.running).toBe(false);
    expect(store.calibration.error).toBe('probe_failed');
    expect(store.calibration.detail).toContain('Bureau');
  });

  it('recovers a run it never saw start', async () => {
    // Progress and result are WS deltas and deltas are never replayed: a tab
    // backgrounded across a run comes back with nothing unless resync refetches.
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', running: true, result: null }));

    await store.resync();

    expect(store.calibration.running).toBe(true);
  });

  it('recovers a proposal produced while it was away', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', running: false, result: PROPOSAL }));

    await store.resync();

    expect(store.calibration.running).toBe(false);
    expect(store.calibration.result.predicted_latency_ms).toBe(180);
  });
});

describe('snapcastStore — analysis state across a refocus', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    store = useSnapcastStore();
  });

  it('keeps a failure message when the tab comes back', async () => {
    // resync() runs on every reconnect and refocus. The backend keeps no record
    // of a failed run, so a resync that cleared the error left the Auto tab
    // blank where an explanation had been.
    store.handleCalibrationEvent({
      category: 'routing', type: 'calibration_failed',
      data: { reason: 'probe_failed', detail: 'Bureau: timed out' },
    });
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', running: false, result: null }));

    await store.resync();

    expect(store.calibration.error).toBe('probe_failed');
    expect(store.calibration.detail).toContain('Bureau');
  });

  it('drops the stale failure once a run produces a proposal', async () => {
    store.handleCalibrationEvent({
      category: 'routing', type: 'calibration_failed', data: { reason: 'probe_failed' },
    });
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', running: false, result: PROPOSAL }));

    await store.resync();

    expect(store.calibration.error).toBeNull();
    expect(store.calibration.result).toEqual(PROPOSAL);
  });

  it('staging a proposal is what makes the apply button appear', () => {
    // The panel shows one button whose label is the answer: a run that changes
    // nothing leaves "Analyse" in place, and a run that proposes something
    // turns it into "Apply". `hasServerConfigChanges` is that switch, so
    // staging has to flip it and writing nothing must not.
    expect(store.hasServerConfigChanges).toBe(false);

    store.applyPreset({ config: { buffer_ms: 1234 } });

    expect(store.hasServerConfigChanges).toBe(true);
    expect(store.serverConfig.buffer_ms).toBe(1234);
  });
});

describe('snapcastStore — a staged change survives a reload', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    store = useSnapcastStore();
  });

  it('keeps what the user staged when the panel refetches', async () => {
    // loadServerConfig() runs whenever the multiroom panel reloads its data,
    // which includes right after an apply because snapserver restarts.
    // Overwriting the edit buffer there made a staged proposal — and the Apply
    // button with it — disappear on its own, with nothing written.
    store.applyPreset({ config: { buffer_ms: 180, codec: 'pcm' } });
    apiCall.get.mockResolvedValueOnce(ok({
      config: { buffer_ms: 300, codec: 'flac', chunk_ms: 40, snapclient_buffer_time: 80 },
      capabilities: { codecs: [], presets: [] },
    }));

    await store.loadServerConfig();

    expect(store.serverConfig.buffer_ms).toBe(180);
    expect(store.hasServerConfigChanges).toBe(true);
  });

  it('takes the server values when nothing is staged', async () => {
    apiCall.get.mockResolvedValueOnce(ok({
      config: { buffer_ms: 300, codec: 'flac', chunk_ms: 40, snapclient_buffer_time: 80 },
      capabilities: { codecs: [], presets: [] },
    }));

    await store.loadServerConfig();

    expect(store.serverConfig.buffer_ms).toBe(300);
    expect(store.hasServerConfigChanges).toBe(false);
  });
});

describe('snapcastStore — the apply button only means a real change', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    store = useSnapcastStore();
  });

  it('ignores the order the keys happen to be in', async () => {
    // The placeholder lists its keys in one order and the API answers in
    // another. Compared with JSON.stringify, a configuration identical in
    // every field read as a pending change and lit Apply over nothing.
    apiCall.get.mockResolvedValueOnce(ok({
      config: { chunk_ms: 20, snapclient_buffer_time: 60, codec: 'pcm',
                sampleformat: '48000:32:2', buffer_ms: 180 },
      capabilities: { codecs: [], presets: [] },
    }));
    await store.loadServerConfig();

    store.applyPreset({ config: { buffer_ms: 180, codec: 'pcm', chunk_ms: 20,
                                  snapclient_buffer_time: 60 } });

    expect(store.hasServerConfigChanges).toBe(false);
  });

  it('still sees a change when one field really differs', async () => {
    apiCall.get.mockResolvedValueOnce(ok({
      config: { buffer_ms: 180, chunk_ms: 20, codec: 'pcm',
                sampleformat: '48000:32:2', snapclient_buffer_time: 60 },
      capabilities: { codecs: [], presets: [] },
    }));
    await store.loadServerConfig();

    store.applyPreset({ config: { buffer_ms: 300 } });

    expect(store.hasServerConfigChanges).toBe(true);
  });
});
