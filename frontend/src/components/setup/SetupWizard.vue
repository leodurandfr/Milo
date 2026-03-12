<!-- frontend/src/components/setup/SetupWizard.vue -->
<template>
  <div class="setup-wizard">
    <div class="setup-wizard__content">
      <!-- Step content -->
      <WelcomeStep v-if="currentStep === 0" @next="nextStep" />

      <LanguageStep
        v-else-if="currentStep === 1"
        v-model="wizardState.language"
      />

      <AudioStep
        v-else-if="currentStep === 2"
        v-model="wizardState.audioId"
        :audio-cards="audioCards"
      />

      <ScreenStep
        v-else-if="currentStep === 3"
        v-model="wizardState.screenType"
        :screens="screens"
      />

      <SummaryStep
        v-else-if="currentStep === 4"
        :language-label="selectedLanguageLabel"
        :audio-label="selectedAudioLabel"
        :screen-label="selectedScreenLabel"
        :is-applying="isApplying"
        :is-rebooting="isRebooting"
        :error="error"
        @apply="applySetup"
      />
    </div>

    <!-- Navigation (steps 1-4: back + next/apply) -->
    <div class="setup-wizard__footer">
      <div v-if="currentStep >= 1 && !isRebooting" class="setup-wizard__nav">
        <Button variant="background-strong" @click="prevStep">
          {{ t('common.back') }}
        </Button>
        <Button v-if="currentStep <= 3" variant="brand" @click="nextStep">
          {{ t('setup.next') }}
        </Button>
      </div>

      <StepIndicator :current="currentStep" :total="5" />
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue';
import { useI18n, i18n } from '@/services/i18n';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import axios from 'axios';
import { logger } from '@/services/logger';
import StepIndicator from './StepIndicator.vue';
import WelcomeStep from './WelcomeStep.vue';
import LanguageStep from './LanguageStep.vue';
import AudioStep from './AudioStep.vue';
import ScreenStep from './ScreenStep.vue';
import SummaryStep from './SummaryStep.vue';
import Button from '@/components/ui/Button.vue';

const { t, getAvailableLanguages } = useI18n();
const { loadHardwareConfig } = useHardwareConfig();

const currentStep = ref(0);
const isApplying = ref(false);
const isRebooting = ref(false);
const error = ref(null);
let pollIntervalId = null;

// Hardware options from API
const audioCards = ref([]);
const screens = ref([]);

// Wizard local state (not persisted until apply)
const wizardState = reactive({
  language: i18n.getCurrentLanguage() || 'english',
  audioId: 'none',
  screenType: 'none',
});

// Computed labels for summary
const selectedLanguageLabel = computed(() => {
  const lang = getAvailableLanguages().find(l => l.code === wizardState.language);
  return lang?.name || wizardState.language;
});

const selectedAudioLabel = computed(() => {
  const card = audioCards.value.find(c => c.value === wizardState.audioId);
  return card?.label || wizardState.audioId;
});

const selectedScreenLabel = computed(() => {
  const screen = screens.value.find(s => s.value === wizardState.screenType);
  return screen?.label || wizardState.screenType;
});

function nextStep() {
  if (currentStep.value < 4) {
    currentStep.value++;
  }
}

function prevStep() {
  if (currentStep.value > 0) {
    currentStep.value--;
  }
}

async function applySetup() {
  isApplying.value = true;
  error.value = null;

  try {
    await axios.post('/api/setup/complete', {
      language: wizardState.language,
      audio_id: wizardState.audioId,
      screen_type: wizardState.screenType,
    });

    isApplying.value = false;
    isRebooting.value = true;

    // Two-phase polling: wait for backend to go DOWN, then wait for it to come back UP
    let pollCount = 0;
    const maxPolls = 60;
    let backendWentDown = false;

    pollIntervalId = setInterval(async () => {
      pollCount++;
      if (pollCount > maxPolls) {
        clearInterval(pollIntervalId);
        pollIntervalId = null;
        isRebooting.value = false;
        error.value = 'Reboot timed out. Please refresh the page.';
        return;
      }
      try {
        await axios.get('/api/ping', { timeout: 2000 });
        if (backendWentDown) {
          // Backend is back up after reboot
          clearInterval(pollIntervalId);
          pollIntervalId = null;
          window.location.reload();
        }
      } catch {
        // Backend unreachable — reboot has begun
        backendWentDown = true;
      }
    }, 3000);
  } catch (err) {
    logger.error('setup', 'Setup wizard failed', err);
    isApplying.value = false;
    error.value = err.response?.data?.detail || 'Setup failed. Please try again.';
  }
}

onMounted(async () => {
  const data = await loadHardwareConfig(true);
  if (data) {
    audioCards.value = data.options.audio_cards;
    screens.value = data.options.screens;
  }
});

onUnmounted(() => {
  if (pollIntervalId) {
    clearInterval(pollIntervalId);
    pollIntervalId = null;
  }
});
</script>

<style scoped>
.setup-wizard {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: var(--color-background-neutral);
  display: flex;
  flex-direction: column;
  padding: var(--space-06);
}

.setup-wizard__content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  min-height: 0;
}

.setup-wizard__footer {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-04);
  padding-top: var(--space-04);
}

.setup-wizard__nav {
  display: flex;
  gap: var(--space-03);
}
</style>
