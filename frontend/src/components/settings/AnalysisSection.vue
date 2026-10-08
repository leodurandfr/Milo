<!-- frontend/src/components/settings/AnalysisSection.vue -->
<!--
  The automatic-tuning section the multiroom and Mac panels share: a title with
  Start beside it, then the section opens on what the run is doing and, once it
  ends, on what it found. The two panels drew this block each their own way —
  a full-width button repeating the title, a strip, a note — and differed.

  Two reveals, one after the other: the progress while `running`, the results
  (the note, then the panel's own grid in the slot) once it is not. Both open
  in height from the top, so the section grows rather than its content jumping
  in. The results stay open for as long as the panel holds them.
-->
<template>
  <SettingsSection>
    <template #header>
      <SectionHeader :title="title">
        <template #actions>
          <Button variant="brand" size="small" :loading="running" :disabled="disabled || running"
            @click="emit('start')">
            {{ t('analysis.start') }}
          </Button>
        </template>
      </SectionHeader>
    </template>

    <!-- It stops short of full: only the result may finish it, so a slow
         network never shows a completed bar over a running analysis. -->
    <ProgressStrip :open="running" :percent="percent" :step-ms="stepMs"
      :label="running ? label : ''" :hint="running ? hint : ''" />

    <!-- A Transition rather than a class: the stores drop the result the
         moment a run starts, and a leave transition keeps the last rendering
         on screen while it folds, where a class would fold an empty box. -->
    <Transition name="analysis-results">
      <div v-if="resultsOpen" class="analysis-results">
        <div class="analysis-results__inner">
          <p v-if="note" class="text-body analysis-results__note">{{ note }}</p>
          <slot v-if="hasResults" />
        </div>
      </div>
    </Transition>
  </SettingsSection>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { PROGRESS_TICK_MS } from '@/composables/useAnalysisRun';
import Button from '@/components/ui/Button.vue';
import SettingsSection from './SettingsSection.vue';
import SectionHeader from './SectionHeader.vue';
import ProgressStrip from './ProgressStrip.vue';

const props = defineProps({
  title: { type: String, required: true },
  running: { type: Boolean, default: false },
  /** Start refused for a reason other than a run in flight (an Apply pending). */
  disabled: { type: Boolean, default: false },
  percent: { type: Number, default: 0 },
  label: { type: String, default: '' },
  hint: { type: String, default: '' },
  stepMs: { type: Number, default: PROGRESS_TICK_MS },
  /** The line said once a run ended: why it failed, or what to do next. */
  note: { type: String, default: '' },
  /** Whether the slot has a measurement to show. */
  hasResults: { type: Boolean, default: false },
});

const emit = defineEmits(['start']);

const { t } = useI18n();

const resultsOpen = computed(() => !props.running && Boolean(props.note || props.hasResults));
</script>

<style scoped>
/* ProgressStrip's reveal: grid-rows + opacity, and a negative margin that
   cancels the section's row gap while collapsed. */
.analysis-results {
  display: grid;
  grid-template-rows: 1fr;
}

.analysis-results-enter-active,
.analysis-results-leave-active {
  transition:
    grid-template-rows var(--transition-fast),
    opacity var(--transition-fast),
    margin-top var(--transition-fast);
}

.analysis-results-enter-from,
.analysis-results-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: calc(-1 * var(--space-04));
}

.analysis-results__inner {
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.analysis-results__note {
  color: var(--color-text-secondary);
  margin: 0;
}
</style>
