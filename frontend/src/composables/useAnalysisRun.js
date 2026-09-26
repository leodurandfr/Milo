// frontend/src/composables/useAnalysisRun.js
/**
 * The panel side of a background analysis — the Snapcast one in the multiroom
 * panel, the Mac link one in the Mac panel: the progress bar, and the staging
 * of a result onto the panel's controls.
 *
 * `calibration` is the store's `{running, result, error, expectedSeconds,
 * startedAt}`; `start()` resolves true once the backend took the run, `stage()`
 * puts its result on the controls. Nothing here writes a setting: staging is
 * not applying, and each panel's Apply stays its resource's one write.
 */
import { ref, computed, watch } from 'vue';
import { useTimer } from '@/composables/useTimer';

// Capped: the bar may approach the end but only the result event completes it,
// so a slow network never shows a completed bar over a running analysis.
const PROGRESS_CEILING = 95;

// One second, matched by ProgressStrip's CSS transition. A shorter tick with
// the same transition is what made the bar advance in visible steps: the
// animation finished long before the next value arrived and the fill sat still.
export const PROGRESS_TICK_MS = 1000;

export function useAnalysisRun(calibration, { start, stage }) {
  // === PROGRESS ===

  const elapsedMs = ref(0);
  const timer = useTimer();
  let ticker = null;

  const progressPercent = computed(() => {
    const expected = calibration.value.expectedSeconds * 1000;
    if (!expected) return 0;
    return Math.min(PROGRESS_CEILING, (elapsedMs.value / expected) * 100);
  });

  const remainingSeconds = computed(() => {
    const expected = calibration.value.expectedSeconds;
    if (!expected) return 0;
    return Math.max(0, Math.round(expected - elapsedMs.value / 1000));
  });

  watch(() => calibration.value.running, (running) => {
    if (ticker) {
      timer.clear(ticker);
      ticker = null;
    }
    if (!running) return;
    const tick = () => { elapsedMs.value = Date.now() - (calibration.value.startedAt || Date.now()); };
    tick();
    ticker = timer.setInterval(tick, PROGRESS_TICK_MS);
  }, { immediate: true });

  // === STAGING ===

  // Only a run started here is staged. Without the flag, opening the panel
  // restored the last stored proposal and staged it — an hours-old measurement
  // on the controls and an Apply button for a change nobody asked for.
  //
  // Armed before the request and disarmed if it is refused. Armed only once
  // the POST answered, it could re-arm after the failure event had already
  // disarmed it — an immediate refusal travels over WS faster than the HTTP
  // response — and the next result anyone produced was staged here.
  const awaitingResult = ref(false);

  async function startAnalysis() {
    awaitingResult.value = true;
    if (!(await start())) awaitingResult.value = false;
  }

  watch(() => calibration.value.result, (result) => {
    if (result && awaitingResult.value) {
      awaitingResult.value = false;
      stage();
    }
  });

  // A run that ends without a proposal disarms too, or the flag outlives it and
  // claims the next result as this panel's request.
  watch(() => calibration.value.error, (error) => {
    if (error) awaitingResult.value = false;
  });

  return { progressPercent, remainingSeconds, startAnalysis };
}
