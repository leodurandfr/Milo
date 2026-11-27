<!-- frontend/src/components/equalizer/LevelMeters.vue -->
<!-- Stereo output level meters with real-time monitoring -->
<template>
  <SectionCard :title="t('equalizer.meters.title')">
    <!-- Output meters -->
    <div class="stereo-meters">
      <LevelMeter
        :level="signalLeft"
        :output-level="outputLeft"
        :min="meterMin"
        :max="meterMax"
        label="L"
        :show-peak="true"
      />
      <LevelMeter
        :level="signalRight"
        :output-level="outputRight"
        :min="meterMin"
        :max="meterMax"
        label="R"
        :show-peak="true"
      />
    </div>
  </SectionCard>
</template>

<script setup>
import { computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useEqualizerStore } from '@/stores/equalizerStore';
import SectionCard from '@/components/ui/SectionCard.vue';
import LevelMeter from './LevelMeter.vue';
import { useTimer } from '@/composables/useTimer';

const props = defineProps({
  clientIds: {
    type: Array,
    default: () => []  // Empty until registry loads and auto-selects
  }
});

const { t } = useI18n();
const equalizerStore = useEqualizerStore();
const timer = useTimer();

// Levels arrive over WS (`equalizer`/`levels`, ~10 Hz) while this keepalive is
// re-posted; the backend stops sampling ~15 s after the meters unmount.
const KEEPALIVE_INTERVAL = 5000;

// Fixed metering range in dBFS. The bars draw the music before the volume: after
// it, a room listened to at -46 dB peaks near -55 dBFS and fills a tenth of the
// bar whatever plays. The warning colors read what reaches the DAC.
const meterMin = -60;
const meterMax = 0;

function channel(levels, index) {
  return Array.isArray(levels) && levels.length > index ? levels[index] : levels?.[0] ?? meterMin;
}

const signalLeft = computed(() => channel(equalizerStore.signalPeak, 0));
const signalRight = computed(() => channel(equalizerStore.signalPeak, 1));
const outputLeft = computed(() => channel(equalizerStore.outputPeak, 0));
const outputRight = computed(() => channel(equalizerStore.outputPeak, 1));

function keepalive() {
  equalizerStore.keepLevelsMonitorAlive(props.clientIds);
}

onMounted(() => {
  keepalive();
  timer.setInterval(keepalive, KEEPALIVE_INTERVAL); // auto-cleared on unmount
});

// Re-arm immediately when the set of clients it aggregates changes — a mute
// toggled included — instead of waiting for the next interval. Keyed on the set
// itself, so a volume broadcast that changes nothing re-arms nothing.
watch(() => {
  const ids = equalizerStore.meterClientIds(props.clientIds);
  return ids === null ? 'muted' : ids.join(',');
}, keepalive);
</script>

<style scoped>
.stereo-meters {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}
</style>
