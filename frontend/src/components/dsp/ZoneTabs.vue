<!-- frontend/src/components/dsp/ZoneTabs.vue -->
<!-- Zone tabs + Volume DSP + Preset selector with Save/Reset buttons -->
<template>
  <div class="zone-section">
    <!-- Zone tabs (only if multiple targets) -->
    <Tabs
      v-if="targets.length > 1"
      v-model="selectedTargetLocal"
      :tabs="zoneTabs"
      size="small"
      @change="handleTargetChange"
    />

    <!-- Volume DSP Section -->
    <div class="volume-section">
      <div class="volume-header">
        <span class="volume-label text-mono">{{ $t('dsp.volume.title', 'Volume DSP') }}</span>
        <button
          v-press.light
          class="mute-button"
          :class="{ 'mute-button--active': dspStore.dspVolume.mute }"
          @click="handleMuteToggle"
        >
          <SvgIcon :name="dspStore.dspVolume.mute ? 'volumeOff' : 'volume'" :size="20" />
        </button>
      </div>
      <div class="volume-control">
        <RangeSlider
          :model-value="dspStore.dspVolume.main"
          :min="settingsStore.volumeLimits.min_db"
          :max="settingsStore.volumeLimits.max_db"
          :step="1"
          value-unit=" dB"
          :disabled="dspStore.dspVolume.mute"
          @update:model-value="handleVolumeInput"
          @change="handleVolumeChange"
        />
      </div>
    </div>

    <!-- Preset row: dropdown + Save + Reset -->
    <div class="preset-row">
      <span class="preset-label text-mono">{{ $t('dsp.preset', 'Preset') }}</span>

      <Dropdown
        v-model="currentPreset"
        :options="presetOptions"
        :placeholder="$t('dsp.selectPreset', '-- Select --')"
        :disabled="disabled"
        @change="handlePresetChange"
      />

      <IconButton
        icon="save"
        size="medium"
        :disabled="disabled || isSaving"
        @click="openSaveDialog"
      />

      <IconButton
        icon="reset"
        size="medium"
        :disabled="disabled || !currentPreset"
        @click="resetToPreset"
      />
    </div>

    <!-- Save Preset Dialog -->
    <PresetSaveDialog
      :is-open="showSaveDialog"
      :initial-name="saveDialogInitialName"
      :saving="isSaving"
      @close="showSaveDialog = false"
      @save="handleSavePreset"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useI18n } from '@/services/i18n';
import Tabs from '@/components/ui/Tabs.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import IconButton from '@/components/ui/IconButton.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import PresetSaveDialog from './PresetSaveDialog.vue';

const { t } = useI18n();
const dspStore = useDspStore();
const settingsStore = useSettingsStore();

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['targetChange', 'presetApplied']);

// Local state
const currentPreset = ref('');
const showSaveDialog = ref(false);
const isSaving = ref(false);
const selectedTargetLocal = ref(dspStore.selectedTarget);

// Default presets with gain values for 10 EQ bands
// Bands: 31Hz, 63Hz, 125Hz, 250Hz, 500Hz, 1kHz, 2kHz, 4kHz, 8kHz, 16kHz
const defaultPresets = computed(() => [
  {
    id: 'flat',
    label: t('dsp.quickPresets.flat', 'Flat'),
    gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
  },
  {
    id: 'bass_boost',
    label: t('dsp.quickPresets.bassBoost', 'Bass Boost'),
    gains: [6, 5, 3, 1, 0, 0, 0, 0, 0, 0]
  },
  {
    id: 'vocal',
    label: t('dsp.quickPresets.vocal', 'Vocal'),
    gains: [-2, -1, 0, 1, 2, 3, 2, 1, 0, 0]
  },
  {
    id: 'night',
    label: t('dsp.quickPresets.nightMode', 'Night'),
    gains: [-4, -2, 0, 1, 1, 1, 0, -1, -2, -3]
  }
]);

// Computed
const targets = computed(() => dspStore.availableTargets);
const userPresets = computed(() => dspStore.presets);

// Convert targets to Tabs format
const zoneTabs = computed(() =>
  targets.value.map(target => ({
    label: target.name,
    value: target.id,
    badge: isLinked(target.id) ? 'link' : undefined,
    disabled: !target.available
  }))
);

// Convert presets to Dropdown options format
const presetOptions = computed(() => {
  const options = [];

  // Default presets group
  defaultPresets.value.forEach(preset => {
    options.push({
      label: `${preset.label}`,
      value: `default:${preset.id}`
    });
  });

  // User presets group (if any)
  if (userPresets.value.length > 0) {
    userPresets.value.forEach(preset => {
      options.push({
        label: preset,
        value: `user:${preset}`
      });
    });
  }

  return options;
});

const saveDialogInitialName = computed(() => {
  if (currentPreset.value.startsWith('user:')) {
    return currentPreset.value.slice(5);
  }
  return '';
});

// Check if a client is linked to others
function isLinked(clientId) {
  return dspStore.isClientLinked(clientId);
}

// Handle target/zone change
async function handleTargetChange(targetId) {
  if (targetId !== dspStore.selectedTarget) {
    await dspStore.selectTarget(targetId);
    // Clear preset selection when switching zones
    currentPreset.value = '';
    emit('targetChange', targetId);
  }
}

// Handle preset selection change
async function handlePresetChange(value) {
  if (!value) return;

  if (value.startsWith('default:')) {
    // Apply default preset
    const presetId = value.slice(8);
    const preset = defaultPresets.value.find(p => p.id === presetId);
    if (preset) {
      await applyDefaultPreset(preset);
    }
  } else if (value.startsWith('user:')) {
    // Load user preset
    const presetName = value.slice(5);
    await dspStore.loadPreset(presetName);
  }

  emit('presetApplied', value);
}

// Apply a default preset (quick preset)
async function applyDefaultPreset(preset) {
  for (let i = 0; i < dspStore.filters.length && i < preset.gains.length; i++) {
    const filter = dspStore.filters[i];
    if (filter.gain !== preset.gains[i]) {
      dspStore.updateFilter(filter.id, 'gain', preset.gains[i]);
      await dspStore.finalizeFilterUpdate(filter.id);
    }
  }
}

// Reset to current preset
async function resetToPreset() {
  if (!currentPreset.value) return;

  if (currentPreset.value.startsWith('default:')) {
    const presetId = currentPreset.value.slice(8);
    const preset = defaultPresets.value.find(p => p.id === presetId);
    if (preset) {
      await applyDefaultPreset(preset);
    }
  } else if (currentPreset.value.startsWith('user:')) {
    const presetName = currentPreset.value.slice(5);
    await dspStore.loadPreset(presetName);
  }
}

// Open save dialog
function openSaveDialog() {
  showSaveDialog.value = true;
}

// Save preset
async function handleSavePreset(name) {
  isSaving.value = true;
  try {
    await dspStore.savePreset(name);
    currentPreset.value = 'user:' + name;
    showSaveDialog.value = false;
  } catch (error) {
    console.error('Error saving preset:', error);
  } finally {
    isSaving.value = false;
  }
}

// === VOLUME CONTROLS ===
let volumeThrottleTimer = null;

function handleVolumeInput(value) {
  dspStore.setClientDspVolume(dspStore.selectedTarget, value);

  if (volumeThrottleTimer) clearTimeout(volumeThrottleTimer);
  volumeThrottleTimer = setTimeout(() => {
    dspStore.updateDspVolume(value);
  }, 50);
}

function handleVolumeChange(value) {
  if (volumeThrottleTimer) clearTimeout(volumeThrottleTimer);
  dspStore.updateDspVolume(value);
}

async function handleMuteToggle() {
  await dspStore.updateDspMute(!dspStore.dspVolume.mute);
}

// Watch for active preset changes from store
// immediate: true ensures preset is restored when modal reopens
watch(() => dspStore.activePreset, (newPreset) => {
  if (newPreset && !currentPreset.value.startsWith('default:')) {
    currentPreset.value = 'user:' + newPreset;
  }
}, { immediate: true });

// Sync local target with store
watch(() => dspStore.selectedTarget, (newTarget) => {
  selectedTargetLocal.value = newTarget;
});
</script>

<style scoped>
.zone-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

/* Volume section */
.volume-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  padding: var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
}

.volume-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.volume-label {
  color: var(--color-text-secondary);
}

.mute-button {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast), var(--transition-press);
}

.mute-button:hover {
  background: var(--color-border);
  color: var(--color-text);
}

.mute-button--active {
  background: var(--color-brand);
  color: var(--color-text-contrast);
}

.mute-button--active:hover {
  background: var(--color-brand);
  color: var(--color-text-contrast);
}

.volume-control {
  display: flex;
  align-items: center;
}

/* Preset row */
.preset-row {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-02) var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
}

.preset-label {
  color: var(--color-text-secondary);
  white-space: nowrap;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .volume-section {
    padding: var(--space-02);
    gap: var(--space-01);
  }

  .mute-button {
    width: 32px;
    height: 32px;
  }

  .preset-row {
    padding: var(--space-01) var(--space-02);
    gap: var(--space-01);
  }
}
</style>
