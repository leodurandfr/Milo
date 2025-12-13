<!-- frontend/src/components/dsp/AdvancedDsp.vue -->
<!-- Advanced DSP controls: Compressor, Loudness, Delay -->
<template>
  <div class="advanced-dsp">
    <!-- Section Tabs -->
    <div class="section-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="tab-button"
        :class="{ active: activeTab === tab.id }"
        @click="activeTab = tab.id"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- Tab Content -->
    <div class="tab-content">
      <!-- Compressor -->
      <div v-if="activeTab === 'compressor'" class="section compressor-section">
        <div class="section-header">
          <span class="section-title">{{ $t('dsp.compressor.title', 'Compressor') }}</span>
          <Toggle v-model="dspStore.compressor.enabled" @change="handleCompressorToggle" />
        </div>

        <div v-if="dspStore.compressor.enabled" class="section-controls">
          <div class="control-row">
            <label>{{ $t('dsp.compressor.threshold', 'Threshold') }}</label>
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
            <label>{{ $t('dsp.compressor.ratio', 'Ratio') }}</label>
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
            <label>{{ $t('dsp.compressor.attack', 'Attack') }}</label>
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
            <label>{{ $t('dsp.compressor.release', 'Release') }}</label>
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
            <label>{{ $t('dsp.compressor.makeup', 'Makeup Gain') }}</label>
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
          <span class="section-title">{{ $t('dsp.loudness.title', 'Loudness') }}</span>
          <Toggle v-model="dspStore.loudness.enabled" @change="handleLoudnessToggle" />
        </div>

        <div v-if="dspStore.loudness.enabled" class="section-controls">
          <div class="control-row">
            <label>{{ $t('dsp.loudness.lowBoost', 'Bass Boost') }}</label>
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
            <label>{{ $t('dsp.loudness.highBoost', 'Treble Boost') }}</label>
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
          <span class="section-title">{{ $t('dsp.delay.title', 'Channel Delay') }}</span>
        </div>

        <div class="section-controls">
          <div class="control-row">
            <label>{{ $t('dsp.delay.left', 'Left Channel') }}</label>
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
            <label>{{ $t('dsp.delay.right', 'Right Channel') }}</label>
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

      <!-- Volume -->
      <div v-if="activeTab === 'volume'" class="section volume-section">
        <div class="section-header">
          <span class="section-title">{{ $t('dsp.volume.title', 'DSP Volume') }}</span>
          <Toggle
            :model-value="dspStore.dspVolume.mute"
            @change="handleMuteToggle"
          />
        </div>

        <div class="section-controls">
          <div class="control-row">
            <label>{{ $t('dsp.volume.level', 'Level') }}</label>
            <RangeSlider
              :model-value="dspStore.dspVolume.main"
              :min="-60"
              :max="0"
              :step="1"
              value-unit=" dB"
              :disabled="dspStore.dspVolume.mute"
              @update:model-value="handleVolumeInput"
              @change="handleVolumeChange"
            />
          </div>

          <div class="volume-display text-mono" :class="{ muted: dspStore.dspVolume.mute }">
            {{ dspStore.dspVolume.mute ? $t('dsp.volume.muted', 'Muted') : `${dspStore.dspVolume.main} dB` }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';

const dspStore = useDspStore();

const activeTab = ref('compressor');

const tabs = computed(() => [
  { id: 'compressor', label: 'Compressor' },
  { id: 'loudness', label: 'Loudness' },
  { id: 'delay', label: 'Delay' },
  { id: 'volume', label: 'Volume' }
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

// === VOLUME ===
let volumeThrottleTimer = null;

function handleVolumeInput(value) {
  dspStore.dspVolume.main = value; // Optimistic update

  if (volumeThrottleTimer) clearTimeout(volumeThrottleTimer);
  volumeThrottleTimer = setTimeout(() => {
    dspStore.updateDspVolume(value);
  }, 50);
}

function handleVolumeChange(value) {
  if (volumeThrottleTimer) clearTimeout(volumeThrottleTimer);
  dspStore.updateDspVolume(value);
}

async function handleMuteToggle(muted) {
  await dspStore.updateDspMute(muted);
}
</script>

<style scoped>
.advanced-dsp {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.section-tabs {
  display: flex;
  gap: var(--space-02);
  padding: var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
}

.tab-button {
  flex: 1;
  padding: var(--space-02) var(--space-03);
  border: none;
  border-radius: var(--radius-04);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.tab-button:hover {
  color: var(--color-text);
}

.tab-button.active {
  background: var(--color-brand);
  color: var(--color-text-inverse);
}

.tab-content {
  padding: var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
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
  font-weight: 600;
  color: var(--color-text);
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
  font-size: 13px;
  color: var(--color-text-secondary);
}

.volume-display {
  text-align: center;
  font-size: 14px;
  color: var(--color-text-secondary);
  padding: var(--space-02);
  background: var(--color-background);
  border-radius: var(--radius-03);
  transition: all var(--transition-fast);
}

.volume-display.muted {
  color: var(--color-text-light);
  background: var(--color-background-strong);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .section-tabs {
    padding: var(--space-01);
  }

  .tab-button {
    padding: var(--space-02);
    font-size: 13px;
  }

  .tab-content {
    padding: var(--space-02);
  }
}
</style>
