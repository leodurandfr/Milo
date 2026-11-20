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

// An analysis starts only over a config the server reported.
async function loadReadyConfig(store) {
  apiCall.get.mockResolvedValueOnce(ok({
    status: 'success',
    config: { buffer_ms: 300, codec: 'flac', chunk_ms: 40, snapclient_buffer_time: 80 },
    capabilities: { codecs: [], presets: [] },
  }));
  await store.loadServerConfig();
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
    await loadReadyConfig(store);
    apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));
    await store.startCalibration();
    store.handleCalibrationEvent(event('calibration_progress', { stage: 'probing' }));

    const second = await store.startCalibration();

    expect(second).toBe(false);
    expect(apiCall.post).toHaveBeenCalledTimes(1);
  });

  it('clears the running flag when the request itself is refused', async () => {
    await loadReadyConfig(store);
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

  it('staging a proposal is what makes the apply button appear', async () => {
    // The panel shows one button whose label is the answer: a run that changes
    // nothing leaves "Analyse" in place, and a run that proposes something
    // turns it into "Apply". `hasServerConfigChanges` is that switch, so
    // staging has to flip it and writing nothing must not.
    apiCall.get.mockResolvedValueOnce(ok({
      status: 'success',
      config: { buffer_ms: 300, codec: 'flac', chunk_ms: 40, snapclient_buffer_time: 80 },
      capabilities: { codecs: [], presets: [] },
    }));
    await store.loadServerConfig();
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
    const applied = ok({
      status: 'success',
      config: { buffer_ms: 300, codec: 'flac', chunk_ms: 40, snapclient_buffer_time: 80 },
      capabilities: { codecs: [], presets: [] },
    });
    apiCall.get.mockResolvedValueOnce(applied);
    await store.loadServerConfig();
    store.applyPreset({ config: { buffer_ms: 180, codec: 'pcm' } });
    apiCall.get.mockResolvedValueOnce(applied);

    await store.loadServerConfig();

    expect(store.serverConfig.buffer_ms).toBe(180);
    expect(store.hasServerConfigChanges).toBe(true);
  });

  it('takes the server values when nothing is staged', async () => {
    apiCall.get.mockResolvedValueOnce(ok({
      status: 'success',
      config: { buffer_ms: 300, codec: 'flac', chunk_ms: 40, snapclient_buffer_time: 80 },
      capabilities: { codecs: [], presets: [] },
    }));

    await store.loadServerConfig();

    expect(store.serverConfig.buffer_ms).toBe(300);
    expect(store.hasServerConfigChanges).toBe(false);
  });
});

describe('snapcastStore — leaving the panel drops what was never applied', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    store = useSnapcastStore();
  });

  async function loadApplied() {
    apiCall.get.mockResolvedValueOnce(ok({
      status: 'success',
      config: { buffer_ms: 300, codec: 'flac', chunk_ms: 40, snapclient_buffer_time: 80 },
      capabilities: { codecs: [], presets: [] },
    }));
    await store.loadServerConfig();
  }

  it('restores the applied configuration when a proposal is abandoned', async () => {
    // Running the analysis and closing the panel without pressing Apply left
    // the proposal on the sliders — and its Apply button under them — for the
    // next visit, which reads as a setting the unit is running.
    await loadApplied();
    store.handleCalibrationEvent(event('calibration_result', PROPOSAL));
    store.stageCalibrationResult();

    await store.discardUnappliedTuning();

    expect(store.serverConfig.buffer_ms).toBe(300);
    expect(store.serverConfig.codec).toBe('flac');
    expect(store.hasServerConfigChanges).toBe(false);
  });

  // Leaving while the PUT is in flight — snapserver restarts, so the apply
  // lasts seconds. The buffer must follow the outcome instead of being pulled
  // out from under the request, and either outcome has to settle it: a discard
  // that simply stood down left a refused apply staged for good.
  async function discardOverAnApply(outcome) {
    await loadApplied();
    store.applyPreset({ config: PROPOSAL.config });
    let finishPut;
    apiCall.put.mockReturnValueOnce(new Promise((resolve) => { finishPut = resolve; }));
    const applying = store.applyServerConfig();

    const discarding = store.discardUnappliedTuning();
    finishPut(outcome);
    await Promise.all([applying, discarding]);
  }

  it('forgets the measurements behind a proposal nobody applied', async () => {
    // The cards describe the proposal: left standing they report a network
    // measurement over a configuration the unit does not run. The backend holds
    // the result too, for the refetch a backgrounded tab needs — and that
    // refetch runs on every panel open, so dropping it here alone changes
    // nothing.
    await loadApplied();
    store.handleCalibrationEvent(event('calibration_result', PROPOSAL));
    store.stageCalibrationResult();

    await store.discardUnappliedTuning();

    expect(store.calibration.result).toBeNull();
    expect(apiCall.delete).toHaveBeenCalledWith(
      '/api/routing/snapcast/calibration', expect.anything()
    );
  });

  it('keeps the measurements of an analysis the unit is running', async () => {
    // Applied, the result is what the sliders show and what the "measured
    // values" line reads — forgetting it there would delete the one thing that
    // says where those numbers came from.
    apiCall.get.mockResolvedValueOnce(ok({
      status: 'success',
      config: { ...PROPOSAL.config, sampleformat: '48000:32:2' },
      capabilities: { codecs: [], presets: [] },
    }));
    await store.loadServerConfig();
    store.handleCalibrationEvent(event('calibration_result', PROPOSAL));

    await store.discardUnappliedTuning();

    expect(store.calibration.result).toEqual(PROPOSAL);
    expect(apiCall.delete).not.toHaveBeenCalled();
  });

  it('does not report a failure to a panel opened afresh', async () => {
    // The message answers an action someone took while watching the analysis
    // run. It is held locally on purpose, across every resync — but leaving is
    // not a resync.
    await loadApplied();
    store.handleCalibrationEvent(
      event('calibration_failed', { reason: 'probe_failed', detail: 'Bureau: timed out' })
    );

    await store.discardUnappliedTuning();

    expect(store.calibration.error).toBeNull();
    expect(store.calibration.detail).toBeNull();
    expect(apiCall.delete).not.toHaveBeenCalled();
  });

  it('keeps the values the apply landed', async () => {
    await discardOverAnApply(ok({ status: 'success' }));

    expect(store.serverConfig.buffer_ms).toBe(180);
    expect(store.hasServerConfigChanges).toBe(false);
  });

  it('drops them when the apply is refused', async () => {
    await discardOverAnApply(fail('snapserver did not restart', 502));

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
      status: 'success',
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
      status: 'success',
      config: { buffer_ms: 180, chunk_ms: 20, codec: 'pcm',
                sampleformat: '48000:32:2', snapclient_buffer_time: 60 },
      capabilities: { codecs: [], presets: [] },
    }));
    await store.loadServerConfig();

    store.applyPreset({ config: { buffer_ms: 300 } });

    expect(store.hasServerConfigChanges).toBe(true);
  });

  it('a finished run leaves no start time for the next one to inherit', () => {
    store.handleCalibrationEvent(event('calibration_progress', { stage: 'probing', expected_seconds: 32 }));
    store.handleCalibrationEvent(event('calibration_result', PROPOSAL));
    expect(store.calibration.startedAt).toBe(0);
  });
});


describe('snapcastStore — a config the server did not report is never written', () => {
  // The server answers `config: null` with `status: "error"` when snapserver is
  // down or its Server.GetStatus timed out on a busy Pi. Read as a config, the
  // form showed 1000/20/flac/80 as the running values and an Apply sent them —
  // over snapserver.conf and every satellite's buffer.
  let store;

  const UNAVAILABLE = ok({
    status: 'error',
    config: null,
    capabilities: { codecs: ['flac'], presets: [] },
    error: 'Snapcast server did not report its configuration',
  });
  const APPLIED = ok({
    status: 'success',
    config: { buffer_ms: 300, codec: 'flac', chunk_ms: 40,
              sampleformat: '48000:32:2', snapclient_buffer_time: 120 },
    capabilities: { codecs: ['flac'], presets: [] },
  });

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    store = useSnapcastStore();
  });

  it.each([
    ['an error answer', UNAVAILABLE],
    // The shape the route used to answer a timed-out read with: no status, an
    // empty config every schema default would have filled in.
    ['an empty config with no status', ok({ config: {}, capabilities: { codecs: [], presets: [] } })],
    ['a failed request', fail('backend unreachable', 502)],
  ])('reads %s as unavailable and sends nothing', async (_, answer) => {
    apiCall.get.mockResolvedValueOnce(answer);

    await store.loadServerConfig();
    store.applyPreset({ config: { buffer_ms: 180, codec: 'pcm', chunk_ms: 20 } });
    const sent = await store.applyServerConfig();

    expect(store.serverConfigStatus).toBe('unavailable');
    expect(store.hasServerConfigChanges).toBe(false);
    expect(sent).toBe(false);
    expect(apiCall.put).not.toHaveBeenCalled();
  });

  it('holds a staged change across a failed read, and never sends placeholder values', async () => {
    apiCall.get.mockResolvedValueOnce(APPLIED);
    await store.loadServerConfig();
    store.applyPreset({ config: { buffer_ms: 180, codec: 'flac', chunk_ms: 40 } });

    apiCall.get.mockResolvedValueOnce(UNAVAILABLE);
    await store.loadServerConfig();
    expect(store.serverConfigStatus).toBe('unavailable');
    expect(await store.applyServerConfig()).toBe(false);

    apiCall.get.mockResolvedValueOnce(APPLIED);
    await store.loadServerConfig();
    apiCall.put.mockResolvedValueOnce(ok({ status: 'success' }));
    await store.applyServerConfig();

    expect(store.serverConfigStatus).toBe('ready');
    const [, body] = apiCall.put.mock.calls[0];
    expect(body.config.buffer_ms).toBe(180);
    // A field the user did not touch comes from a server read (120), never
    // from the placeholder (80).
    expect(body.config.snapclient_buffer_time).toBe(120);
  });

  it('a read that fails while a write is in flight leaves the change and its Apply standing', async () => {
    // Snapserver restarts under the PUT, so a panel reload mid-apply answers no
    // config. The write was allowed over one the server reported; once it is
    // taken, the config written is the one the server runs.
    apiCall.get.mockResolvedValueOnce(APPLIED);
    await store.loadServerConfig();
    store.applyPreset({ config: { buffer_ms: 180, codec: 'flac', chunk_ms: 40 } });
    let finishPut;
    apiCall.put.mockReturnValueOnce(new Promise((resolve) => { finishPut = resolve; }));
    const applying = store.applyServerConfig();

    apiCall.get.mockResolvedValueOnce(UNAVAILABLE);
    await store.loadServerConfig();
    expect(store.serverConfigStatus).toBe('ready');
    expect(store.hasServerConfigChanges).toBe(true);

    finishPut(ok({ status: 'success' }));
    expect(await applying).toBe(true);
    expect(store.serverConfigStatus).toBe('ready');
    expect(store.hasServerConfigChanges).toBe(false);
  });

  it('starts no analysis over a config the server did not report', async () => {
    // Its proposal would land in the placeholder with no Apply to show for it,
    // and the first read that succeeded would replace it.
    apiCall.get.mockResolvedValueOnce(UNAVAILABLE);
    await store.loadServerConfig();

    expect(await store.startCalibration()).toBe(false);
    expect(apiCall.post).not.toHaveBeenCalled();
    expect(store.calibration.running).toBe(false);
  });

  it('a read superseded by a newer one does not decide the status', async () => {
    let finishFirst;
    apiCall.get.mockReturnValueOnce(new Promise((resolve) => { finishFirst = resolve; }));
    const first = store.loadServerConfig();
    apiCall.get.mockResolvedValueOnce(APPLIED);
    await store.loadServerConfig();

    finishFirst(fail('aborted', 0));
    await first;

    expect(store.serverConfigStatus).toBe('ready');
  });
});
