<!-- frontend/src/components/dsp/AdvancedDsp.vue -->
<!-- Advanced DSP controls: Compressor, Loudness, Delay -->
<template>
  <section class="settings-section">
    <div class="section-group">
      <!-- Section Title -->
      <h2 class="heading-2">
        {{ $t('dsp.advanced.title', 'Effets avancés') }}
        <span v-if="zoneName" class="zone-suffix">· {{ zoneName }}</span>
      </h2>

      <!-- Zone-wide settings hint -->
      <p v-if="zoneName" class="zone-hint text-mono-small">
        {{ $t('dsp.advanced.zoneHint', 'Ces paramètres s\'appliquent à tous les haut-parleurs de la zone.') }}
      </p>

      <!-- Section Tabs -->
      <Tabs
        v-model="activeTab"
        :tabs="tabsConfig"
        size="small"
      />

      <!-- Tab Content -->
      <div class="tab-content">
      <!-- Compressor -->
      <div v-if="activeTab === 'compressor'" class="section compressor-section">
        <div class="section-header">
          <span class="section-title text-mono">{{ $t('dsp.compressor.title', 'Compressor') }}</span>
          <Toggle v-model="dspStore.compressor.enabled" @change="handleCompressorToggle" />
        </div>

        <div v-if="dspStore.compressor.enabled" class="section-controls">
          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.compressor.threshold', 'Threshold') }}</label>
            <RangeSlider
              :model-value="dspStore.compressor.threshold"
              :min="-60"
              :max="0"
              :step="1"
              value-unit=" dB"
              @update:model-value="(v) => dspStore.compressor.threshold = v"
              @change="handleCompressorChange('threshold', $event)"
            />
          </div>

          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.compressor.ratio', 'Ratio') }}</label>
            <RangeSlider
              :model-value="dspStore.compressor.ratio"
              :min="1"
              :max="20"
              :step="0.5"
              value-unit=":1"
              @update:model-value="(v) => dspStore.compressor.ratio = v"
              @change="handleCompressorChange('ratio', $event)"
            />
          </div>

          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.compressor.attack', 'Attack') }}</label>
            <RangeSlider
              :model-value="dspStore.compressor.attack"
              :min="0.1"
              :max="100"
              :step="0.1"
              value-unit=" ms"
              @update:model-value="(v) => dspStore.compressor.attack = v"
              @change="handleCompressorChange('attack', $event)"
            />
          </div>

          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.compressor.release', 'Release') }}</label>
            <RangeSlider
              :model-value="dspStore.compressor.release"
              :min="10"
              :max="1000"
              :step="10"
              value-unit=" ms"
              @update:model-value="(v) => dspStore.compressor.release = v"
              @change="handleCompressorChange('release', $event)"
            />
          </div>

          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.compressor.makeup', 'Makeup Gain') }}</label>
            <RangeSlider
              :model-value="dspStore.compressor.makeup_gain"
              :min="0"
              :max="30"
              :step="0.5"
              value-unit=" dB"
              @update:model-value="(v) => dspStore.compressor.makeup_gain = v"
              @change="handleCompressorChange('makeup_gain', $event)"
            />
          </div>
        </div>
      </div>

      <!-- Loudness -->
      <div v-if="activeTab === 'loudness'" class="section loudness-section">
        <div class="section-header">
          <span class="section-title text-mono">{{ $t('dsp.loudness.title', 'Loudness') }}</span>
          <Toggle v-model="dspStore.loudness.enabled" @change="handleLoudnessToggle" />
        </div>

        <div v-if="dspStore.loudness.enabled" class="section-controls">
          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.loudness.lowBoost', 'Bass Boost') }}</label>
            <RangeSlider
              :model-value="dspStore.loudness.low_boost"
              :min="0"
              :max="15"
              :step="0.5"
              value-unit=" dB"
              @update:model-value="(v) => dspStore.loudness.low_boost = v"
              @change="handleLoudnessChange('low_boost', $event)"
            />
          </div>

          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.loudness.highBoost', 'Treble Boost') }}</label>
            <RangeSlider
              :model-value="dspStore.loudness.high_boost"
              :min="0"
              :max="15"
              :step="0.5"
              value-unit=" dB"
              @update:model-value="(v) => dspStore.loudness.high_boost = v"
              @change="handleLoudnessChange('high_boost', $event)"
            />
          </div>
        </div>
      </div>

      <!-- Delay -->
      <div v-if="activeTab === 'delay'" class="section delay-section">
        <div class="section-header">
          <span class="section-title text-mono">{{ $t('dsp.delay.title', 'Channel Delay') }}</span>
        </div>

        <div class="section-controls">
          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.delay.left', 'Left Channel') }}</label>
            <RangeSlider
              :model-value="dspStore.delay.left"
              :min="0"
              :max="50"
              :step="0.1"
              value-unit=" ms"
              @update:model-value="(v) => dspStore.delay.left = v"
              @change="handleDelayChange('left', $event)"
            />
          </div>

          <div class="control-row">
            <label class="text-mono-small">{{ $t('dsp.delay.right', 'Right Channel') }}</label>
            <RangeSlider
              :model-value="dspStore.delay.right"
              :min="0"
              :max="50"
              :step="0.1"
              value-unit=" ms"
              @update:model-value="(v) => dspStore.delay.right = v"
              @change="handleDelayChange('right', $event)"
            />
          </div>
        </div>
      </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useI18n } from '@/services/i18n';
import Tabs from '@/components/ui/Tabs.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';

const props = defineProps({
  zoneName: {
    type: String,
    default: ''
  }
});

const { t } = useI18n();
const dspStore = useDspStore();

const activeTab = ref('compressor');

// Tabs configuration for the Tabs component
const tabsConfig = computed(() => [
  { value: 'compressor', label: t('dsp.compressor.title', 'Compressor') },
  { value: 'loudness', label: t('dsp.loudness.title', 'Loudness') },
  { value: 'delay', label: t('dsp.delay.title', 'Delay') }
]);

// === COMPRESSOR ===
async function handleCompressorToggle(enabled) {
  await dspStore.updateCompressor({ enabled });
}

async function handleCompressorChange(field, value) {
  await dspStore.updateCompressor({ [field]: value });
}

// === LOUDNESS ===
async function handleLoudnessToggle(enabled) {
  await dspStore.updateLoudness({ enabled });
}

async function handleLoudnessChange(field, value) {
  await dspStore.updateLoudness({ [field]: value });
}

// === DELAY ===
async function handleDelayChange(channel, value) {
  await dspStore.updateDelay({ [channel]: value });
}
</script>

<style scoped>
/* Settings section pattern */
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.section-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.zone-suffix {
  color: var(--color-text-secondary);
  font-weight: normal;
}

.zone-hint {
  color: var(--color-text-secondary);
  margin: 0;
  font-style: italic;
}

.tab-content {
  display: flex;
  flex-direction: column;
}

.section {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: var(--space-02);
  border-bottom: 1px solid var(--color-border);
}

.section-title {
  color: var(--color-text-secondary);
}

.section-controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding-top: var(--space-02);
}

.control-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.control-row label {
  color: var(--color-text-secondary);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }
}
</style>
