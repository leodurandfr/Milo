// frontend/tests/composables/useAnalysisRun.test.js
/**
 * The panel side of a background analysis, shared by the multiroom and Mac
 * panels: which result a panel stages onto its controls, and the bar.
 *
 * What breaks when these fail: a panel stages a proposal nobody in front of it
 * asked for — an Apply button over values the unit does not run — or the bar
 * reads "done" while the analysis still measures.
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useAnalysisRun } from '@/composables/useAnalysisRun';

function idle() {
  return { running: false, result: null, error: null, expectedSeconds: 0, startedAt: 0 };
}

function mountRun(start) {
  const calibration = ref(idle());
  const stage = vi.fn();
  let api;
  const Host = defineComponent({
    setup() {
      api = useAnalysisRun(calibration, { start, stage });
      return () => h('div');
    },
  });
  mount(Host);
  return { calibration, stage, api };
}

describe('useAnalysisRun', () => {
  it('stages the result of a run started here, once', async () => {
    const { calibration, stage, api } = mountRun(async () => true);
    await api.startAnalysis();
    calibration.value = { ...calibration.value, result: { config: {} } };
    await nextTick();
    calibration.value = { ...calibration.value, result: { config: { other: 1 } } };
    await nextTick();
    expect(stage).toHaveBeenCalledTimes(1);
  });

  it('never stages a result it did not ask for', async () => {
    const { calibration, stage } = mountRun(async () => true);
    calibration.value = { ...calibration.value, result: { config: {} } };
    await nextTick();
    expect(stage).not.toHaveBeenCalled();
  });

  it('a refused start leaves nothing armed', async () => {
    const { calibration, stage, api } = mountRun(async () => false);
    await api.startAnalysis();
    calibration.value = { ...calibration.value, result: { config: {} } };
    await nextTick();
    expect(stage).not.toHaveBeenCalled();
  });

  it('a failure that beats the answer to the start disarms for good', async () => {
    let answer;
    const { calibration, stage, api } = mountRun(() => new Promise((resolve) => { answer = resolve; }));
    const starting = api.startAnalysis();
    calibration.value = { ...calibration.value, error: 'no_mac' };
    await nextTick();
    answer(true);
    await starting;

    calibration.value = { ...calibration.value, error: null, result: { config: {} } };
    await nextTick();
    expect(stage).not.toHaveBeenCalled();
  });

  it('the bar stops short of the end until the result arrives', async () => {
    const { calibration, api } = mountRun(async () => true);
    calibration.value = { ...idle(), running: true, expectedSeconds: 62, startedAt: Date.now() - 100_000 };
    await nextTick();
    expect(api.progressPercent.value).toBe(95);
    expect(api.remainingSeconds.value).toBe(0);
  });
});
