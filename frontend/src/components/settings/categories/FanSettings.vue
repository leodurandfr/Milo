<!-- frontend/src/components/settings/categories/FanSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Live telemetry (InfoSettings-style cards) -->
    <SettingsSection :title="t('fanSettings.status')">
      <div class="fan-grid">
        <div class="fan-item">
          <span class="fan-label text-mono">{{ t('fanSettings.temperature') }}</span>
          <span class="fan-value text-mono">{{ tempDisplay }}</span>
        </div>
        <div class="fan-item">
          <span class="fan-label text-mono">{{ t('fanSettings.rpm') }}</span>
          <span class="fan-value text-mono">{{ fanStore.status.rpm }} {{ t('fanSettings.rpmUnit') }}</span>
        </div>
        <div class="fan-item fan-item-bar">
          <div class="fan-item-top">
            <span class="fan-label text-mono">{{ t('fanSettings.speed') }}</span>
            <span class="fan-value text-mono">{{ fanStore.status.pwm_percent }}%</span>
          </div>
          <div class="bar-container">
            <div class="bar-fill" :style="{ width: fanStore.status.pwm_percent + '%' }"></div>
          </div>
        </div>
      </div>
    </SettingsSection>

    <!-- Mode + curve (or disabled message) -->
    <SettingsSection :title="config.enabled ? t('fanSettings.mode') : ''">
      <p v-if="!config.enabled" class="fan-warning text-mono">{{ t('fanSettings.disabledNote') }}</p>

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

        <template v-if="config.mode === 'auto'">
          <div class="curve">
            <div class="curve__head">
              <span class="curve__col text-mono-small">{{ t('fanSettings.temperature') }}</span>
              <span class="curve__col text-mono-small">{{ t('fanSettings.speed') }}</span>
              <span class="curve__col-spacer"></span>
            </div>
            <div v-for="(point, i) in config.curve" :key="i" class="curve__point">
              <RangeSlider
                v-model="point.temp_c"
                :min="tempMin(i)"
                :max="tempMax(i)"
                :step="1"
                value-unit="°C"
                @change="saveCurve"
              />
              <RangeSlider
                v-model="point.percent"
                :min="0"
                :max="100"
                :step="5"
                value-unit="%"
                @change="saveCurve"
              />
              <IconButton
                icon="close"
                size="small"
                :class="{ 'curve__remove--hidden': config.curve.length <= 2 }"
                :aria-label="t('fanSettings.removePoint')"
                @click="removePoint(i)"
              />
            </div>
            <button v-if="config.curve.length < MAX_POINTS" class="curve__add text-mono" @click="addPoint">
              + {{ t('fanSettings.addPoint') }}
            </button>
          </div>
        </template>
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
import IconButton from '@/components/ui/IconButton.vue';

const MAX_POINTS = 6;

const { t } = useI18n();
const fanStore = useFanStore();
const timer = useTimer();

// Local copy for instant UI responsiveness (mirrors ScreenSettings pattern).
const config = reactive({
  enabled: true,
  mode: 'auto',
  manual_percent: 50,
  curve: [],
});

const modeOptions = computed(() => [
  { value: 'auto', label: t('fanSettings.modeAuto') },
  { value: 'manual', label: t('fanSettings.modeManual') },
]);

const tempDisplay = computed(() =>
  fanStore.status.temp_c ? `${fanStore.status.temp_c}°C` : '—'
);

function syncFromStore() {
  config.enabled = fanStore.config.enabled;
  config.mode = fanStore.config.mode;
  config.manual_percent = fanStore.config.manual_percent;
  config.curve = (fanStore.config.curve ?? []).map(p => ({ ...p }));
}

function save() {
  fanStore.updateConfig({
    enabled: config.enabled,
    mode: config.mode,
    manual_percent: config.manual_percent,
    curve: config.curve.map(p => ({ ...p })),
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

function saveCurve() {
  save();
}

// Keep curve temperatures strictly increasing: clamp each point between its
// neighbours (the backend rejects a non-increasing curve with 422).
function tempMin(i) {
  return i === 0 ? 20 : config.curve[i - 1].temp_c + 1;
}
function tempMax(i) {
  return i === config.curve.length - 1 ? 110 : config.curve[i + 1].temp_c - 1;
}

function addPoint() {
  const c = config.curve;
  if (c.length >= MAX_POINTS) return;
  // Insert in the widest temperature gap so the new point always has room.
  let bestI = 0;
  let bestGap = -1;
  for (let i = 0; i < c.length - 1; i++) {
    const gap = c[i + 1].temp_c - c[i].temp_c;
    if (gap > bestGap) { bestGap = gap; bestI = i; }
  }
  if (bestGap < 2) return; // no room to split anywhere
  c.splice(bestI + 1, 0, {
    temp_c: Math.round((c[bestI].temp_c + c[bestI + 1].temp_c) / 2),
    percent: Math.round((c[bestI].percent + c[bestI + 1].percent) / 2),
  });
  save();
}

function removePoint(i) {
  if (config.curve.length <= 2) return;
  config.curve.splice(i, 1);
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
  color: var(--color-brand);
}

/* Curve editor */
.curve {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.curve__head,
.curve__point {
  display: grid;
  grid-template-columns: 1fr 1fr var(--space-07);
  align-items: center;
  gap: var(--space-03);
}

.curve__col {
  color: var(--color-text-secondary);
}

.curve__remove--hidden {
  visibility: hidden;
}

.curve__add {
  align-self: flex-start;
  border: none;
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
  color: var(--color-text);
  padding: var(--space-02) var(--space-04);
  cursor: pointer;
  transition: var(--transition-press);
}

@media (max-aspect-ratio: 4/3) {
  .fan-grid {
    grid-template-columns: 1fr;
  }

  .curve__head {
    display: none;
  }

  .curve__point {
    gap: var(--space-02);
  }
}
</style>
