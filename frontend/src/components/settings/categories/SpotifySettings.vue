<!-- frontend/src/components/settings/categories/SpotifySettings.vue -->
<!--
  Spotify Connect screen. go-librespot needs no login (zeroconf handoff from the
  phone), so the only thing to configure here is playback: the crossfade the
  daemon reads from its config.yml.

  That file is parsed once, at process start, hence the two-step shape: the
  value is stored immediately (and picked up by the next start on its own),
  while a sticky button offers the restart that makes it audible right now.
-->
<template>
  <SettingsContainer>
    <!-- Off is stored as a 0 duration, like the auto-stop delay: one value, the
         toggle and the slider being two views of it. -->
    <ToggleSection
      :title="t('spotifySettings.crossfade')"
      :enabled="crossfadeEnabled"
      @change="handleCrossfadeToggle"
    >
      <SettingItem :label="t('spotifySettings.crossfadeDuration')">
        <RangeSlider
          v-model="crossfadeSeconds"
          :min="1"
          :max="12"
          :step="1"
          value-unit=" s"
          @change="handleCrossfadeChange"
        />
      </SettingItem>
    </ToggleSection>

    <!-- Only offered while the daemon is running: with Spotify stopped there is
         nothing to restart, the stored value applies at its next start. -->
    <Button
      v-if="needsRestart"
      variant="brand"
      size="medium"
      class="apply-button-sticky"
      :disabled="isApplying"
      @click="applyNow"
    >
      {{ isApplying ? t('spotifySettings.restarting') : t('spotifySettings.restartToApply') }}
    </Button>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const { t } = useI18n();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();
const { updateSetting } = useSettingsAPI();

const MS_PER_SECOND = 1000;
const DEFAULT_SECONDS = 6;

const crossfadeSeconds = ref(0);
// Remembers the last audible duration so toggling OFF then ON restores it.
const lastCrossfadeSeconds = ref(DEFAULT_SECONDS);
// What the running daemon plays with, as far as this screen can know: the
// stored value when it opened. Re-based only by an explicit restart — never by
// the WS echo of our own write, which would hide the button we just earned.
const appliedSeconds = ref(0);
const isApplying = ref(false);

const crossfadeEnabled = computed(() => crossfadeSeconds.value !== 0);
const spotifyRunning = computed(() => unifiedStore.systemState.active_source === 'spotify');
const needsRestart = computed(() => spotifyRunning.value && crossfadeSeconds.value !== appliedSeconds.value);

function save(applyNow) {
  return updateSetting('spotify-settings', {
    crossfade_duration: crossfadeSeconds.value * MS_PER_SECOND,
    apply_now: applyNow
  });
}

// Named wrapper: @change hands the slider value as first argument, which would
// land in `applyNow` and restart the daemon on every drag.
function handleCrossfadeChange() {
  lastCrossfadeSeconds.value = crossfadeSeconds.value;
  return save(false);
}

function handleCrossfadeToggle(enabled) {
  if (!enabled && crossfadeSeconds.value > 0) {
    lastCrossfadeSeconds.value = crossfadeSeconds.value;
  }
  crossfadeSeconds.value = enabled ? lastCrossfadeSeconds.value : 0;
  return save(false);
}

async function applyNow() {
  if (isApplying.value) return;
  isApplying.value = true;
  try {
    await save(true);
    appliedSeconds.value = crossfadeSeconds.value;
  } finally {
    isApplying.value = false;
  }
}

function readStored(ms) {
  crossfadeSeconds.value = Math.round(ms / MS_PER_SECOND);
  if (crossfadeSeconds.value > 0) {
    lastCrossfadeSeconds.value = crossfadeSeconds.value;
  }
}

// Follow the stored value (another device, or a resync) without touching the
// applied baseline.
watch(() => settingsStore.spotifySettings.crossfade_duration, readStored);

onMounted(() => {
  readStored(settingsStore.spotifySettings.crossfade_duration);
  appliedSeconds.value = crossfadeSeconds.value;
});
</script>

<style scoped>
.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}
</style>
