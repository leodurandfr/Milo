<!-- frontend/src/components/settings/categories/dsp/AdvancedDsp.vue -->
<!-- Advanced DSP controls: Compressor, Loudness, Delay -->
<template>
  <div class="advanced-dsp">
    <!-- Loudness Section -->
    <section class="settings-section">
      <div class="effect-header">
        <h2 class="heading-2">{{ $t('dsp.loudness.title', 'Loudness') }}</h2>
        <Toggle :model-value="dspStore.loudness.enabled" @change="handleLoudnessToggle" />
      </div>

      <div class="effect-controls" :class="{ 'disabled': !dspStore.loudness.enabled }">
        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.loudness.lowBoost', 'Bass Boost') }}</label>
          <RangeSlider :model-value="dspStore.loudness.low_boost" :min="0" :max="15" :step="0.5" value-unit=" dB"
            :disabled="!dspStore.loudness.enabled" @update:model-value="(v) => dspStore.loudness.low_boost = v"
            @change="handleLoudnessChange('low_boost', $event)" />
        </div>

        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.loudness.highBoost', 'Treble Boost') }}</label>
          <RangeSlider :model-value="dspStore.loudness.high_boost" :min="0" :max="15" :step="0.5" value-unit=" dB"
            :disabled="!dspStore.loudness.enabled" @update:model-value="(v) => dspStore.loudness.high_boost = v"
            @change="handleLoudnessChange('high_boost', $event)" />
        </div>
      </div>
    </section>

    <!-- Compressor Section -->
    <section class="settings-section">
      <div class="effect-header">
        <h2 class="heading-2">{{ $t('dsp.compressor.title', 'Compressor') }}</h2>
        <Toggle :model-value="dspStore.compressor.enabled" @change="handleCompressorToggle" />
      </div>

      <div class="effect-controls" :class="{ 'disabled': !dspStore.compressor.enabled }">
        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.compressor.ratio', 'Ratio') }}</label>
          <RangeSlider :model-value="dspStore.compressor.ratio" :min="1" :max="20" :step="0.5" value-unit=":1"
            :disabled="!dspStore.compressor.enabled" @update:model-value="(v) => dspStore.compressor.ratio = v"
            @change="handleCompressorChange('ratio', $event)" />
        </div>

        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.compressor.threshold', 'Threshold') }}</label>
          <RangeSlider :model-value="dspStore.compressor.threshold" :min="-60" :max="0" :step="1" value-unit=" dB"
            :disabled="!dspStore.compressor.enabled" @update:model-value="(v) => dspStore.compressor.threshold = v"
            @change="handleCompressorChange('threshold', $event)" />
        </div>

        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.compressor.attack', 'Attack') }}</label>
          <RangeSlider :model-value="dspStore.compressor.attack" :min="0.1" :max="100" :step="0.1" value-unit=" ms"
            :disabled="!dspStore.compressor.enabled" @update:model-value="(v) => dspStore.compressor.attack = v"
            @change="handleCompressorChange('attack', $event)" />
        </div>

        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.compressor.release', 'Release') }}</label>
          <RangeSlider :model-value="dspStore.compressor.release" :min="10" :max="1000" :step="10" value-unit=" ms"
            :disabled="!dspStore.compressor.enabled" @update:model-value="(v) => dspStore.compressor.release = v"
            @change="handleCompressorChange('release', $event)" />
        </div>

        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.compressor.makeup', 'Makeup Gain') }}</label>
          <RangeSlider :model-value="dspStore.compressor.makeup_gain" :min="0" :max="30" :step="0.5" value-unit=" dB"
            :disabled="!dspStore.compressor.enabled" @update:model-value="(v) => dspStore.compressor.makeup_gain = v"
            @change="handleCompressorChange('makeup_gain', $event)" />
        </div>
      </div>
    </section>



    <!-- Delay Section -->
    <section class="settings-section">
      <div class="effect-header">
        <h2 class="heading-2">{{ $t('dsp.delay.title', 'Delay') }}</h2>
        <Toggle :model-value="dspStore.delay.enabled" @change="handleDelayToggle" />
      </div>

      <div class="effect-controls" :class="{ 'disabled': !dspStore.delay.enabled }">
        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.delay.left', 'Left Channel') }}</label>
          <RangeSlider :model-value="dspStore.delay.left" :min="0" :max="50" :step="0.1" value-unit=" ms"
            :disabled="!dspStore.delay.enabled" @update:model-value="(v) => dspStore.delay.left = v"
            @change="handleDelayChange('left', $event)" />
        </div>

        <div class="control-item">
          <label class="text-mono-small">{{ $t('dsp.delay.right', 'Right Channel') }}</label>
          <RangeSlider :model-value="dspStore.delay.right" :min="0" :max="50" :step="0.1" value-unit=" ms"
            :disabled="!dspStore.delay.enabled" @update:model-value="(v) => dspStore.delay.right = v"
            @change="handleDelayChange('right', $event)" />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { useDspStore } from '@/stores/dspStore';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';

const dspStore = useDspStore();

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
async function handleDelayToggle(enabled) {
  await dspStore.updateDelay({ enabled });
}

async function handleDelayChange(channel, value) {
  await dspStore.updateDelay({ [channel]: value });
}
</script>

<style scoped>
.advanced-dsp {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.effect-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.effect-controls {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
  transition: opacity var(--transition-fast);
}

.effect-controls.disabled {
  opacity: 0.4;
  pointer-events: none;
}

.control-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.control-item label {
  color: var(--color-text-secondary);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .effect-controls {
    grid-template-columns: 1fr;
  }
}
</style>
