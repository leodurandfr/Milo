<!-- frontend/src/components/settings/categories/FanSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Live telemetry (InfoSettings-style cards) -->
    <SettingsSection :title="t('fanSettings.status')">
      <div class="fan-grid">
        <div class="fan-item">
          <span class="fan-label text-mono-medium">{{ t('fanSettings.temperature') }}</span>
          <span class="fan-value text-mono-medium">{{ tempDisplay }}</span>
        </div>
        <div class="fan-item">
          <span class="fan-label text-mono-medium">{{ t('fanSettings.rpm') }}</span>
          <span class="fan-value text-mono-medium">{{ fanStore.status.rpm }} {{ t('fanSettings.rpmUnit') }}</span>
        </div>
        <div class="fan-item fan-item-bar">
          <div class="fan-item-top">
            <span class="fan-label text-mono-medium">{{ t('fanSettings.speed') }}</span>
            <span class="fan-value text-mono-medium">{{ fanStore.status.pwm_percent }}%</span>
          </div>
          <div class="bar-container">
            <div class="bar-fill" :style="{ width: fanStore.status.pwm_percent + '%' }"></div>
          </div>
        </div>
      </div>
    </SettingsSection>

    <!-- Mode + its value (or disabled message) -->
    <SettingsSection :title="config.enabled ? t('fanSettings.mode') : ''">
      <p v-if="!config.enabled" class="fan-warning text-mono-medium">{{ t('fanSettings.disabledNote') }}</p>

      <template v-else>
        <ButtonGroup :model-value="config.mode" :options="modeOptions" @change="setMode" />

        <SettingItem v-if="config.mode === 'manual'" :label="t('fanSettings.manualSpeed')">
          <RangeSlider
            v-model="config.manual_percent"
            :min="0"
            :max="100"
            :step="5"
            value-unit="%"
            @input="onManualInput"
            @change="onManualChange"
          />
        </SettingItem>

        <SettingItem v-if="config.mode === 'target'" :label="t('fanSettings.targetTemp')">
          <RangeSlider
            v-model="config.target_temp_c"
            :min="55"
            :max="76"
            :step="1"
            value-unit="°C"
            @change="onTargetChange"
          />
        </SettingItem>
      </template>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { reactive, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useFanStore } from '@/stores/fanStore';
import { useTimer } from '@/composables/useTimer';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';

const { t } = useI18n();
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
  fanStore.status.temp_c ? `${fanStore.status.temp_c}°C` : '—'
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

// Re-sync when the store config changes (WS event or header toggle).
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
/* Status grid — mirrors InfoSettings.vue */
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
  background: var(--color-background-strong);
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
  background: var(--color-background-medium-16);
  border-radius: 3px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: var(--color-background-contrast-32);
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
