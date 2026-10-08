// frontend/tests/stores/macLinkStore.test.js
/**
 * The Mac link panel's store, driven through its own handlers.
 *
 * What breaks when these fail: the analysis writes a link nobody applied (and
 * rebuilds the Mac's audio device mid-song), Apply sends half a link and the
 * Mac's half is reset, or a proposal left alone comes back on the controls as
 * though the unit ran it.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { useMacLinkStore } from '@/stores/macLinkStore';
import { useSettingsStore } from '@/stores/settingsStore';

const APPLIED = {
  target_latency_ms: 70, latency_profile: 'gradual', frame_length_ms: 6,
  packet_length_ms: 3, fec_block_source: 10, fec_block_repair: 5, packet_interleaving: true,
};

const PROPOSAL = {
  config: { ...APPLIED, target_latency_ms: 30, frame_length_ms: 4, packet_interleaving: false },
  predicted_latency_ms: { current: 147, proposed: 62 },
  measurements: { mac_name: 'Mac mini', rtt_max_ms: 0.37, loss_pct: 0, mac_burst_ms: 11.6 },
  assumed: ['mac_burst'],
};

function event(type, data) {
  return { category: 'settings', type, data };
}

describe('macLinkStore', () => {
  let store;
  let settings;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    settings = useSettingsStore();
    settings.updateMacRocSettings(APPLIED);
    store = useMacLinkStore();
    store.syncDraft();
  });

  it('never writes the link when a proposal arrives', () => {
    store.handleCalibrationEvent(event('mac_calibration_result', PROPOSAL));

    expect(apiCall.put).not.toHaveBeenCalled();
    expect(store.hasChanges).toBe(false);
    expect(store.calibration.result).toEqual(PROPOSAL);
  });

  it('stages both halves of the proposal on the controls, and Apply sends all seven', async () => {
    store.handleCalibrationEvent(event('mac_calibration_result', PROPOSAL));
    store.stageCalibrationResult();
    expect(store.hasChanges).toBe(true);

    apiCall.put.mockResolvedValueOnce(ok({ status: 'success' }));
    await store.apply();

    const [url, body] = apiCall.put.mock.calls[0];
    expect(url).toBe('/api/settings/mac-roc');
    expect(body).toEqual(PROPOSAL.config);
    expect(store.hasChanges).toBe(false);
  });

  it('stages the backend defaults on Reset, writes nothing, and forgets the measurement', async () => {
    const DEFAULTS = { ...APPLIED, target_latency_ms: 50, frame_length_ms: 4, packet_interleaving: false };
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', defaults: DEFAULTS }));
    await store.loadCapabilities();
    store.handleCalibrationEvent(event('mac_calibration_result', PROPOSAL));
    expect(store.canReset).toBe(true);

    await store.resetDraft();

    expect(store.draft).toEqual(DEFAULTS);
    expect(store.canReset).toBe(false);
    expect(apiCall.put).not.toHaveBeenCalled();
    expect(store.calibration.result).toBeNull();
    expect(apiCall.delete.mock.calls[0][0]).toBe('/api/settings/mac-roc/calibration');
  });

  it('keeps the edit when Apply is refused', async () => {
    store.setDraftValue('target_latency_ms', 40);
    apiCall.put.mockResolvedValueOnce(fail('Unprocessable', 422));

    expect(await store.apply()).toBe(false);
    expect(store.hasChanges).toBe(true);
    expect(settings.macRocSettings.target_latency_ms).toBe(70);
  });

  it('refuses a second run and clears the flag when the start is refused', async () => {
    apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));
    await store.startCalibration();
    expect(await store.startCalibration()).toBe(false);
    expect(apiCall.post).toHaveBeenCalledTimes(1);

    setActivePinia(createPinia());
    const fresh = useMacLinkStore();
    apiCall.post.mockResolvedValueOnce(fail('conflict', 409));
    expect(await fresh.startCalibration()).toBe(false);
    expect(fresh.calibration.running).toBe(false);
    expect(fresh.calibration.error).toBe('start_failed');
  });

  it('recovers a run and its proposal from the refetch a backgrounded tab makes', async () => {
    apiCall.get.mockResolvedValueOnce(ok({
      status: 'success', running: false, result: PROPOSAL, expected_seconds: 62, elapsed_seconds: 0,
    }));
    await store.resync();
    expect(store.calibration.result).toEqual(PROPOSAL);

    apiCall.get.mockResolvedValueOnce(ok({
      status: 'success', running: true, result: null, expected_seconds: 62, elapsed_seconds: 30,
    }));
    await store.resync();
    expect(store.calibration.running).toBe(true);
    expect(Date.now() - store.calibration.startedAt).toBeGreaterThanOrEqual(30000);
  });

  it('keeps a failure and its detail until the next run', () => {
    store.handleCalibrationEvent(event('mac_calibration_failed', { reason: 'several_macs', detail: 'A, B' }));
    expect(store.calibration.error).toBe('several_macs');
    expect(store.calibration.detail).toBe('A, B');
  });

  it('leaving the panel drops an unapplied edit and proposal, backend included', async () => {
    store.handleCalibrationEvent(event('mac_calibration_result', PROPOSAL));
    store.stageCalibrationResult();
    apiCall.delete.mockResolvedValueOnce(ok({ status: 'success' }));

    await store.discardUnapplied();

    expect(store.hasChanges).toBe(false);
    expect(store.calibration.result).toBeNull();
    expect(apiCall.delete).toHaveBeenCalledWith('/api/settings/mac-roc/calibration', expect.anything());
  });

  it('keeps a proposal that is what the unit now runs', async () => {
    store.handleCalibrationEvent(event('mac_calibration_result', PROPOSAL));
    settings.updateMacRocSettings(PROPOSAL.config);

    await store.discardUnapplied();

    expect(store.calibration.result).toEqual(PROPOSAL);
    expect(apiCall.delete).not.toHaveBeenCalled();
  });

  it('follows a link applied elsewhere while nothing is edited here', () => {
    const previous = { ...settings.macRocSettings };
    settings.updateMacRocSettings({ target_latency_ms: 100 });
    store.followApplied(previous);

    expect(store.draft.target_latency_ms).toBe(100);
    expect(store.hasChanges).toBe(false);
  });

  it('keeps an edit in progress when another device applies a link', () => {
    store.setDraftValue('frame_length_ms', 4);
    const previous = { ...settings.macRocSettings };
    settings.updateMacRocSettings({ target_latency_ms: 100 });
    store.followApplied(previous);

    expect(store.draft.frame_length_ms).toBe(4);
    expect(store.draft.target_latency_ms).toBe(70);
  });

  it('a finished run leaves no start time for the next one to inherit', () => {
    store.handleCalibrationEvent(event('mac_calibration_progress', { stage: 'measuring', expected_seconds: 62 }));
    store.handleCalibrationEvent(event('mac_calibration_result', PROPOSAL));
    expect(store.calibration.startedAt).toBe(0);

    store.handleCalibrationEvent(event('mac_calibration_progress', { stage: 'measuring' }));
    expect(Date.now() - store.calibration.startedAt).toBeLessThan(1000);
  });
});

