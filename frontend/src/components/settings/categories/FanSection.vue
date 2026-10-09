<!-- frontend/src/components/settings/categories/FanSection.vue -->
<template>
  <!-- Not a ToggleSection: the telemetry and the off-note stay readable while
       the fan is off, which a collapsing section would hide. -->
  <SectionCard>
    <template #header>
      <div class="fan-header">
        <h2 class="heading-2">{{ t('settings.fan') }}</h2>
        <Toggle :model-value="config.enabled" @change="setEnabled" />
      </div>
    </template>

    <div class="fan-grid">
      <div class="fan-item">
        <span class="fan-label text-mono-medium">{{ t('fanSettings.temperature') }}</span>
        <span class="fan-value text-mono-medium">{{ tempDisplay }}</span>
      </div>
      <div class="fan-item">
        <span class="fan-label text-mono-medium">{{ t('fanSettings.rpm') }}</span>
        <span class="fan-value text-mono-medium">{{ formatNumber(fanStore.status.rpm) }}&nbsp;{{ t('fanSettings.rpmUnit') }}</span>
      </div>
      <div class="fan-item fan-item-bar">
        <div class="fan-item-top">
          <span class="fan-label text-mono-medium">{{ t('fanSettings.speed') }}</span>
          <span class="fan-value text-mono-medium">{{ formatUnit(fanStore.status.pwm_percent, '%') }}</span>
        </div>
        <div class="bar-container">
          <div class="bar-fill" :style="{ width: fanStore.status.pwm_percent + '%' }"></div>
        </div>
      </div>
    </div>

    <p v-if="!config.enabled" class="fan-warning text-mono-medium">{{ t('fanSettings.disabledNote', { temperature: formatUnit(85, '°C') }) }}</p>

    <template v-else>
      <ButtonGroup :model-value="config.mode" :options="modeOptions" @change="setMode" />

      <RangeSlider v-if="config.mode === 'manual'" :label="t('fanSettings.manualSpeed')"
        v-model="config.manual_percent"
        :min="0"
        :max="100"
        :step="5"
        unit="%"
        @input="onManualInput"
        @change="onManualChange"
      />

      <RangeSlider v-if="config.mode === 'target'" :label="t('fanSettings.targetTemp')"
        v-model="config.target_temp_c"
        :min="55"
        :max="76"
        :step="1"
        unit="°C"
        @change="onTargetChange"
      />
    </template>
  </SectionCard>
</template>

<script setup>
import { reactive, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useFanStore } from '@/stores/fanStore';
import { useTimer } from '@/composables/useTimer';
import SectionCard from '@/components/ui/SectionCard.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import Toggle from '@/components/ui/Toggle.vue';

const { t, formatNumber, formatUnit } = useI18n();
const fanStore = useFanStore();
const timer = useTimer();

// Local copy for instant UI responsiveness (mirrors ScreenSettings pattern).
const config = reactive({
  enabled: true,
  mode: 'target',
  manual_percent: 50,
  target_temp_c: 65,
});

const modeOptions = computed(() => [
  { value: 'target', label: t('fanSettings.modeTarget') },
  { value: 'manual', label: t('fanSettings.modeManual') },
]);

const tempDisplay = computed(() =>
  fanStore.status.temp_c ? formatUnit(fanStore.status.temp_c, '°C') : '—'
);

function syncFromStore() {
  config.enabled = fanStore.config.enabled;
  config.mode = fanStore.config.mode;
  config.manual_percent = fanStore.config.manual_percent;
  config.target_temp_c = fanStore.config.target_temp_c;
}

function save() {
  fanStore.updateConfig({
    enabled: config.enabled,
    mode: config.mode,
    manual_percent: config.manual_percent,
    target_temp_c: config.target_temp_c,
  });
}

// Applies at once, like every fan control: the fan is driven live and is
// outside Matériel's apply-and-reboot set.
function setEnabled(enabled) {
  config.enabled = enabled;
  save();
}

function setMode(mode) {
  config.mode = mode;
  save();
}

// Throttle live manual-speed previews so dragging doesn't flood /test.
let previewThrottled = false;
let pendingPreview = null;
function onManualInput(value) {
  if (!previewThrottled) {
    fanStore.testSpeed(value);
    previewThrottled = true;
    timer.setTimeout(() => {
      previewThrottled = false;
      if (pendingPreview !== null) {
        fanStore.testSpeed(pendingPreview);
        pendingPreview = null;
      }
    }, 150);
  } else {
    pendingPreview = value;
  }
}

function onManualChange(value) {
  config.manual_percent = value;
  save();
}

// No live /test preview here — a temperature setpoint has no instant effect.
function onTargetChange(value) {
  config.target_temp_c = value;
  save();
}

// Re-sync when the store config changes (WS event from another client).
watch(() => fanStore.config, syncFromStore, { deep: true });

onMounted(() => {
  syncFromStore();
  fanStore.loadStatus();
  // Keep telemetry live while the page is open (the backend only pushes
  // fan_status_changed while it drives the fan).
  timer.setInterval(() => fanStore.refreshTelemetry(), 3000);
});
</script>

<style scoped>
.fan-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* Status grid — mirrors SystemInfoSection.vue */
.fan-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.fan-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-inset);
}

.fan-item-bar {
  flex-direction: column;
  gap: var(--space-02);
  grid-column: 1 / -1;
}

.fan-item-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  width: 100%;
}

.fan-label {
  color: var(--color-text-secondary);
}

.fan-value {
  color: var(--color-text);
  text-align: right;
}

.bar-container {
  width: 100%;
  height: 6px;
  background: var(--color-fill-faint);
  border-radius: 3px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: var(--color-text-secondary);
  border-radius: 3px;
  transition: width var(--transition-normal);
}

.fan-warning {
  color: var(--color-warning);
}

@media (max-aspect-ratio: 4/3) {
  .fan-grid {
    grid-template-columns: 1fr;
  }
}
</style>
