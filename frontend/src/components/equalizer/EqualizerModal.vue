<!-- frontend/src/components/equalizer/EqualizerModal.vue -->
<template>
  <div class="equalizer-modal">
    <NavigationHeader ref="navHeaderRef" :title="t('equalizer.title')">
      <template #actions>
        <Toggle :modelValue="equalizerStore.isEqualizerEffectsEnabled"
          :disabled="equalizerStore.isTogglingEnabled" @change="handleEqualizerToggle" />
      </template>
    </NavigationHeader>

    <div class="transition-wrapper">
      <Transition name="fade-slide" @before-leave="onBeforeLeave" @enter="onEnter" @after-leave="onAfterLeave">
        <!-- State 1: Equalizer disabled -->
        <MessageContent v-if="!equalizerStore.isEqualizerEffectsEnabled" key="disabled" icon="equalizer"
          :title="t('equalizer.effects_disabled')" />

        <!-- State 2: Equalizer enabled but loading/connecting -->
        <MessageContent v-else-if="!equalizerStore.isConnected" key="loading" :loading="true" :loading-delay="0"
          :title="t('equalizer.connecting')" />

        <!-- State 3: Equalizer connected - controls -->
        <div v-else key="controls" class="controls-content">
          <!-- Section 1: Zones (tabs) -->
          <ItemSelector ref="zoneTabsRef" />

          <!-- Section 2: 10 Bands Equalizer with presets dropdown -->
          <SettingsSection>
            <template #header>
              <div class="eq-header">
                <div class="eq-header__title">
                  <h2 class="heading-2">{{ t('equalizer.equalizer.title') }}</h2>
                  <span v-if="selectedZoneName" class="eq-header__subtitle text-mono-medium">{{ selectedZoneName }}</span>
                </div>
                <Button v-if="equalizerStore.isPresetEdited" variant="brand" size="small"
                  @click="handleSaveCustomPreset">
                  {{ t('equalizer.presets.save') }}
                </Button>
                <Dropdown :model-value="currentPresetValue" :options="presetOptions"
                  :display-override="presetDisplayOverride" :placeholder="t('equalizer.selectPreset')"
                  size="small" class="eq-header__dropdown"
                  @update:model-value="handlePresetChange" />
              </div>
            </template>
            <ParametricEQ :filters="equalizerStore.filters" :filters-loaded="equalizerStore.filtersLoaded"
              :is-mobile="isMobile" @update:filter="handleFilterUpdate"
              @change="handleFilterChange" />
          </SettingsSection>

          <!-- Section 3: Loudness -->
          <ToggleSection :title="t('equalizer.loudness.title')" :enabled="equalizerStore.loudness.enabled"
            @change="handleLoudnessToggle">
            <div class="effect-controls">
              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.loudness.lowBoost') }}</label>
                <RangeSlider :model-value="equalizerStore.loudness.low_boost" :min="0" :max="15" :step="0.5"
                  value-unit=" dB" @update:model-value="(v) => equalizerStore.loudness.low_boost = v"
                  @change="handleLoudnessChange('low_boost', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.loudness.highBoost') }}</label>
                <RangeSlider :model-value="equalizerStore.loudness.high_boost" :min="0" :max="15" :step="0.5"
                  value-unit=" dB" @update:model-value="(v) => equalizerStore.loudness.high_boost = v"
                  @change="handleLoudnessChange('high_boost', $event)" />
              </div>
            </div>
          </ToggleSection>

          <!-- Section 4: Compressor -->
          <ToggleSection :title="t('equalizer.compressor.title')" :enabled="equalizerStore.compressor.enabled"
            @change="handleCompressorToggle">
            <div class="effect-controls">
              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.ratio') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.ratio" :min="1" :max="20" :step="0.5"
                  value-unit=":1" @update:model-value="(v) => equalizerStore.compressor.ratio = v"
                  @change="handleCompressorChange('ratio', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.threshold') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.threshold" :min="-60" :max="0" :step="1"
                  value-unit=" dB" @update:model-value="(v) => equalizerStore.compressor.threshold = v"
                  @change="handleCompressorChange('threshold', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.attack') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.attack" :min="0.1" :max="100" :step="0.1"
                  value-unit=" ms" @update:model-value="(v) => equalizerStore.compressor.attack = v"
                  @change="handleCompressorChange('attack', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.release') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.release" :min="10" :max="1000" :step="10"
                  value-unit=" ms" @update:model-value="(v) => equalizerStore.compressor.release = v"
                  @change="handleCompressorChange('release', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.makeup') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.makeup_gain" :min="0" :max="30" :step="0.5"
                  value-unit=" dB" @update:model-value="(v) => equalizerStore.compressor.makeup_gain = v"
                  @change="handleCompressorChange('makeup_gain', $event)" />
              </div>
            </div>
          </ToggleSection>

          <!-- Section 5: Mono -->
          <ToggleSection :title="t('equalizer.mono.title')" :enabled="equalizerStore.mono"
            @change="handleMonoToggle" />

          <!-- Level Meters -->
          <LevelMeters :client-ids="selectedClientIds" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, inject, watch, onMounted, onUnmounted } from 'vue';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useI18n } from '@/services/i18n';
import { useViewTransition } from '@/composables/useViewTransition';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Button from '@/components/ui/Button.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import ItemSelector from './ItemSelector.vue';
import ParametricEQ from './ParametricEQ.vue';
import LevelMeters from './LevelMeters.vue';

const { t } = useI18n();
const equalizerStore = useEqualizerStore();

// Inject modal refs (same pattern as SettingsModal)
const modalContentRef = inject('modalContentRef', null);
const modalSetNavHeight = inject('modalSetNavHeight', null);

const isMobile = ref(false);
const zoneTabsRef = ref(null);
// Persistent header — faded (not popped) when a scroll-reset state change crosses its height.
const navHeaderRef = ref(null);

// Scroll-aware crossfade transitions (same composable as SettingsModal)
const { prepareNavigation, onBeforeLeave, onEnter, onAfterLeave } = useViewTransition({
  scrollElRef: modalContentRef,
  pendingScrollRestore: ref(null),
  setNavHeight: modalSetNavHeight,
  headerRef: navHeaderRef,
});

// Detect content key changes and prepare transition before Vue patches the DOM
const contentKey = computed(() => {
  if (!equalizerStore.isEqualizerEffectsEnabled) return 'disabled';
  if (!equalizerStore.isConnected) return 'loading';
  return 'controls';
});

watch(contentKey, () => {
  prepareNavigation();
}, { flush: 'pre' });

// Selected zone/client name from ZoneTabs component
const selectedZoneName = computed(() => {
  return zoneTabsRef.value?.selectedZoneName ?? '';
});

// Selected client IDs for level meters aggregation
const selectedClientIds = computed(() => {
  return zoneTabsRef.value?.selectedClientIds ?? [];
});

// === EQUALIZER TOGGLE ===
async function handleEqualizerToggle(enabled) {
  await equalizerStore.toggleEqualizerEffectsEnabled(enabled);
}

// === PRESETS ===
const presetOptions = computed(() => {
  const options = [{ label: t('equalizer.presets.custom'), value: 'custom' }];

  // Boost/reducer pairs: group together, sorted by the boost label
  const pairSortKey = {
    bass_boost: 'bass_boost', bass_reducer: 'bass_boost',
    treble_boost: 'treble_boost', treble_reducer: 'treble_boost',
  };

  const items = equalizerStore.builtinPresets.map(preset => {
    const label = t(`equalizer.presets.${preset.id}`);
    const sortAnchor = pairSortKey[preset.id];
    return {
      label,
      value: preset.id,
      sortKey: sortAnchor ? t(`equalizer.presets.${sortAnchor}`) : label,
      subOrder: preset.id.endsWith('_reducer') ? 1 : 0,
    };
  });

  items.sort((a, b) => {
    const cmp = a.sortKey.localeCompare(b.sortKey, undefined, { sensitivity: 'base' });
    return cmp !== 0 ? cmp : a.subOrder - b.subOrder;
  });

  items.forEach(({ label, value }) => options.push({ label, value }));
  return options;
});

const currentPresetValue = computed(() => {
  if (equalizerStore.isCustomMode && equalizerStore.activePreset !== 'custom') {
    return 'custom';
  }
  return equalizerStore.activePreset || 'flat';
});

// When preset is edited, override dropdown display to show "Edited"
const presetDisplayOverride = computed(() => {
  if (equalizerStore.isPresetEdited) {
    return t('equalizer.presets.edited');
  }
  return null;
});

// === MOBILE DETECTION ===
function updateMobileStatus() {
  const aspectRatio = window.innerWidth / window.innerHeight;
  isMobile.value = aspectRatio <= 4 / 3;
}

// === FILTER UPDATES ===
function handleFilterUpdate({ id, field, value }) {
  equalizerStore.updateFilter(id, field, value);
}

function handleFilterChange({ id }) {
  equalizerStore.finalizeFilterUpdate(id);
}

// === PRESET HANDLING ===
async function handlePresetChange(value) {
  if (!value) return;
  await equalizerStore.loadPreset(value);
}

async function handleSaveCustomPreset() {
  await equalizerStore.saveCustomPreset();
}

// === LOUDNESS ===
async function handleLoudnessToggle(enabled) {
  await equalizerStore.updateLoudness({ enabled });
}

async function handleLoudnessChange(field, value) {
  await equalizerStore.updateLoudness({ [field]: value, enabled: equalizerStore.loudness.enabled });
}

// === COMPRESSOR ===
async function handleCompressorToggle(enabled) {
  await equalizerStore.updateCompressor({ enabled });
}

async function handleCompressorChange(field, value) {
  await equalizerStore.updateCompressor({ [field]: value, enabled: equalizerStore.compressor.enabled });
}

// === MONO ===
async function handleMonoToggle(enabled) {
  await equalizerStore.updateMono(enabled);
}

// === LIFECYCLE ===
onMounted(async () => {
  updateMobileStatus();
  window.addEventListener('resize', updateMobileStatus);

  equalizerStore.initializeFilters();

  // Targets first: every EQ read is addressed to the selected target, which
  // outlives the modal and may name a client that no longer exists.
  await equalizerStore.loadTargets();

  // One loader for the whole record, master toggle included. Loading it even
  // when bypassed costs one request on a panel this template replaces with the
  // "disabled" message anyway, and it is what keeps the toggle honest.
  await equalizerStore.loadStatus();
});

onUnmounted(() => {
  window.removeEventListener('resize', updateMobileStatus);
  equalizerStore.cleanup();
});
</script>

<style scoped>
.equalizer-modal {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

/* View stack: leaving + entering views share one grid cell, so the box reserves
   max(leaving, entering) height intrinsically (cf. .settings-modal). */
.transition-wrapper {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
}

/* Both views occupy the single stack cell during the cross-fade. align-self:start
   keeps each at its natural height so the height delta stays measurable. */
:deep(.fade-slide-enter-active),
:deep(.fade-slide-leave-active) {
  grid-row: 1;
  grid-column: 1;
  align-self: start;
}

/* Cross-fade: entering content appears after leaving starts fading */
:deep(.fade-slide-enter-active) {
  transition-delay: 100ms;
}

.controls-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

/* EQ section header layout */
.eq-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-02);
}

.eq-header__title {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}
.eq-header__subtitle {
  color: var(--color-text-secondary);
}

.eq-header__dropdown {
  max-width: 256px;
}

/* Loudness / Compressor controls grid */
.effect-controls {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
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
  .eq-header {
    flex-wrap: wrap;
  }

  .eq-header__dropdown {
    flex: 0 0 100%;
    order: 3;
  }

  .eq-header__dropdown {
    max-width: none;
  }

  .effect-controls {
    grid-template-columns: 1fr;
  }
}
</style>
