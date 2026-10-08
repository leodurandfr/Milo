// frontend/src/stores/macLinkStore.js
/**
 * The Mac link panel: both halves of the ROC link (roc-recv on this unit, the
 * roc-vad sender Milo-Mac applies on the Mac), their edit buffer, and the
 * analysis that proposes them.
 *
 * The applied values live in settingsStore.macRocSettings, fed by /bulk and by
 * `settings/mac_roc_changed`; this store holds only what the panel is editing.
 * The analysis never writes: its result is staged into the buffer and the one
 * write is Apply, through PUT /api/settings/mac-roc.
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useSettingsStore } from './settingsStore';

const LINK_KEYS = [
  'target_latency_ms', 'latency_profile', 'frame_length_ms',
  'packet_length_ms', 'fec_block_source', 'fec_block_repair', 'packet_interleaving',
];

function pickLink(source) {
  return Object.fromEntries(LINK_KEYS.map((key) => [key, source[key]]));
}

function sameLink(a, b) {
  return LINK_KEYS.every((key) => a[key] === b[key]);
}

export const useMacLinkStore = defineStore('macLink', () => {
  const settingsStore = useSettingsStore();

  // What the panel may offer, as the backend's validators bound it. The panel
  // draws nothing without it, so a failed read is said, and retried on resync.
  const capabilities = ref(null);
  const capabilitiesFailed = ref(false);

  const draft = ref(pickLink(settingsStore.macRocSettings));
  const isApplying = ref(false);
  const hasChanges = computed(() => !sameLink(draft.value, settingsStore.macRocSettings));

  // `stage` and `result` arrive as WS deltas, never replayed — hence resync()
  // and this store's place in App.vue's deltaStores.
  const calibration = ref({
    running: false, stage: null, result: null, error: null, detail: null,
    expectedSeconds: 0, startedAt: 0,
  });

  async function loadCapabilities() {
    const result = await apiCall.get('/api/settings/mac-roc/capabilities', {
      category: 'mac',
      message: 'Error loading Mac link capabilities',
    });
    capabilitiesFailed.value = !(result.ok && result.data?.status === 'success');
    if (!capabilitiesFailed.value) capabilities.value = result.data;
    return !capabilitiesFailed.value;
  }

  /** The buffer follows the applied link while nothing is being edited. */
  function syncDraft() {
    draft.value = pickLink(settingsStore.macRocSettings);
  }

  /**
   * The applied link changed under the panel (another device applied one).
   * The controls follow it only if they still showed `previous` untouched.
   */
  function followApplied(previous) {
    if (previous && sameLink(draft.value, previous)) syncDraft();
  }

  function setDraftValue(key, value) {
    draft.value = { ...draft.value, [key]: value };
  }

  // Reset stages the backend's defaults like the analysis stages its proposal:
  // the controls move, and Apply stays the one write.
  const canReset = computed(() => Boolean(capabilities.value?.defaults)
    && !sameLink(draft.value, capabilities.value.defaults));

  // The measurement goes with it: kept, its card would go on advertising a
  // proposal the controls no longer hold, and come back on reopening.
  async function resetDraft() {
    if (!canReset.value) return;
    draft.value = pickLink(capabilities.value.defaults);
    const hadResult = Boolean(calibration.value.result);
    calibration.value = { ...calibration.value, result: null, error: null, detail: null };
    if (!hadResult) return;
    await apiCall.delete('/api/settings/mac-roc/calibration', {
      category: 'mac',
      message: 'Error clearing the Mac link analysis',
    });
  }

  async function apply() {
    if (!hasChanges.value || isApplying.value) return false;
    isApplying.value = true;
    const body = { ...draft.value };
    const result = await apiCall.put('/api/settings/mac-roc', body, {
      category: 'mac',
      message: 'Failed to apply the Mac link',
      checkStatus: true,
    });
    isApplying.value = false;
    if (result.ok) {
      // The broadcast says the same a moment later; the panel must not show
      // an Apply button over a link that was just applied.
      settingsStore.updateMacRocSettings(body);
      return true;
    }
    return false;
  }

  // === AUTOMATIC ANALYSIS ===

  async function startCalibration() {
    if (calibration.value.running) return false;
    calibration.value = {
      running: true, stage: 'measuring', result: null, error: null, detail: null,
      expectedSeconds: 0, startedAt: Date.now(),
    };
    const result = await apiCall.post('/api/settings/mac-roc/calibration', {}, {
      category: 'mac',
      message: 'Error starting the Mac link analysis',
    });
    if (!result.ok) {
      calibration.value = {
        ...calibration.value, running: false, stage: null, error: 'start_failed', startedAt: 0,
      };
      return false;
    }
    return true;
  }

  /** Whole-event handler for the three `settings/mac_calibration_*` deltas. */
  function handleCalibrationEvent(event) {
    const data = event?.data || {};
    if (event?.type === 'mac_calibration_progress') {
      calibration.value = {
        ...calibration.value,
        running: true,
        stage: data.stage,
        error: null,
        expectedSeconds: data.expected_seconds || calibration.value.expectedSeconds,
        startedAt: calibration.value.startedAt || Date.now(),
      };
    } else if (event?.type === 'mac_calibration_result') {
      calibration.value = {
        ...calibration.value, running: false, stage: null, result: data, error: null, startedAt: 0,
      };
    } else if (event?.type === 'mac_calibration_failed') {
      calibration.value = {
        ...calibration.value, running: false, stage: null, result: null, startedAt: 0,
        error: data.reason || 'probe_failed', detail: data.detail || null,
      };
    }
  }

  async function loadCalibration() {
    const result = await apiCall.get('/api/settings/mac-roc/calibration', {
      category: 'mac',
      message: 'Error loading the Mac link analysis',
    });
    if (result.ok && result.data?.status === 'success') {
      // A failure is held here only; the backend keeps no record of it.
      const running = Boolean(result.data.running);
      const recovered = result.data.result || null;
      const keepError = !running && !recovered ? calibration.value.error : null;
      const elapsed = Number(result.data.elapsed_seconds) || 0;
      calibration.value = {
        ...calibration.value,
        running,
        stage: running ? 'measuring' : null,
        result: recovered,
        error: keepError,
        detail: keepError ? calibration.value.detail : null,
        expectedSeconds: Number(result.data.expected_seconds) || calibration.value.expectedSeconds,
        startedAt: running ? Date.now() - elapsed * 1000 : 0,
      };
    }
    return Boolean(result.ok && result.data?.status === 'success');
  }

  /** Put the proposal on the controls. Written only by apply(). */
  function stageCalibrationResult() {
    const proposed = calibration.value.result?.config;
    if (!proposed) return false;
    draft.value = pickLink(proposed);
    return true;
  }

  /**
   * Leaving the panel with an edit or a proposal never applied: both outlive
   * the panel here, and came back on reopening as though the unit ran them.
   */
  async function discardUnapplied() {
    syncDraft();
    const proposed = calibration.value.result?.config;
    const keepResult = Boolean(proposed) && sameLink(proposed, settingsStore.macRocSettings);
    calibration.value = {
      ...calibration.value, error: null, detail: null,
      result: keepResult ? calibration.value.result : null,
    };
    if (keepResult || !proposed) return;
    await apiCall.delete('/api/settings/mac-roc/calibration', {
      category: 'mac',
      message: 'Error clearing the Mac link analysis',
    });
  }

  /** Delta-fed state healer — see App.vue's deltaStores. */
  async function resync() {
    const calibrated = await loadCalibration();
    const capable = capabilities.value ? true : await loadCapabilities();
    return calibrated && capable;
  }

  return {
    capabilities,
    capabilitiesFailed,
    draft,
    isApplying,
    hasChanges,
    calibration,
    loadCapabilities,
    syncDraft,
    followApplied,
    setDraftValue,
    canReset,
    resetDraft,
    apply,
    startCalibration,
    handleCalibrationEvent,
    loadCalibration,
    stageCalibrationResult,
    discardUnapplied,
    resync,
  };
});
