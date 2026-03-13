<!-- frontend/src/components/setup/SetupWizard.vue -->
<template>
  <div class="setup-wizard">
    <div class="setup-card">
      <!-- Welcome step (step 0): no header, centered content -->
      <div v-if="currentStep === 0" class="setup-card__body setup-card__body--welcome">
        <WelcomeStep />
      </div>

      <!-- Steps 1-4: header + scrollable body -->
      <template v-else>
        <div class="setup-card__header">
          <button class="setup-card__back text-mono" @click="prevStep">
            <SvgIcon name="caretLeft" :size="20" />
            {{ currentStep === 4 ? t('setup.back') : t('setup.stepLabel', { n: currentStep }) }}
          </button>
          <h2 class="heading-2">{{ stepTitle }}</h2>
          <StepIndicator :current="currentStep - 1" :total="4" />
        </div>

        <div class="setup-card__body">
          <LanguageStep v-if="currentStep === 1" v-model="wizardState.language" />

          <AudioStep v-else-if="currentStep === 2" v-model="wizardState.audioId" :audio-cards="audioCards" />

          <ScreenStep v-else-if="currentStep === 3" v-model="wizardState.screenType" :screens="screens" />

          <SummaryStep v-else-if="currentStep === 4" :language-code="wizardState.language"
            :language-label="selectedLanguageLabel" :audio-label="selectedAudioLabel"
            :screen-label="selectedScreenLabel" :is-rebooting="isRebooting" :error="error" />
        </div>
      </template>

      <!-- Unified footer (absolute positioned, all steps) -->
      <div class="setup-card__footer">
        <Button v-if="currentStep === 0" variant="brand" @click="nextStep">
          {{ t('setup.welcome.getStarted') }}
        </Button>

        <Button v-else-if="currentStep <= 3" variant="brand" @click="nextStep">
          {{ t('setup.continue') }}
        </Button>

        <Button v-else :variant="applyButtonVariant" :loading="isApplying" :disabled="isRebooting"
          @click="handleApply">
          {{ applyButtonLabel }}
        </Button>
      </div>
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
import SvgIcon from '@/components/ui/SvgIcon.vue';

const { t, getAvailableLanguages } = useI18n();
const { loadHardwareConfig } = useHardwareConfig();

const currentStep = ref(0);
const isApplying = ref(false);
const isRebooting = ref(false);
const confirmReboot = ref(false);
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

// Step titles from i18n
const stepTitles = {
  1: 'setup.language.title',
  2: 'setup.audio.title',
  3: 'setup.screen.title',
  4: 'setup.summary.title',
};

const stepTitle = computed(() => t(stepTitles[currentStep.value] || ''));

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

// Two-step confirm button (matches HardwareSettings pattern)
const applyButtonLabel = computed(() => {
  if (isRebooting.value) return t('setup.summary.rebooting');
  if (confirmReboot.value) return t('setup.summary.confirmReboot');
  return t('setup.summary.applyAndReboot');
});

const applyButtonVariant = computed(() => {
  if (confirmReboot.value) return 'outline';
  return 'brand';
});

function handleApply() {
  if (!confirmReboot.value) {
    confirmReboot.value = true;
    return;
  }
  applySetup();
}

function nextStep() {
  if (currentStep.value < 4) {
    currentStep.value++;
  }
}

function prevStep() {
  if (currentStep.value > 0) {
    currentStep.value--;
    confirmReboot.value = false;
  }
}

async function applySetup() {
  isApplying.value = true;
  confirmReboot.value = false;
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
  document.documentElement.classList.add('setup-active');
  const data = await loadHardwareConfig(true);
  if (data) {
    audioCards.value = data.options.audio_cards;
    screens.value = data.options.screens;
  }
});

onUnmounted(() => {
  document.documentElement.classList.remove('setup-active');
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
  background: var(--color-background);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-07);
}

.setup-card {
  position: relative;
  width: 100%;
  max-width: 380px;
  height: 100%;
  max-height: 640px;
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  outline: 1.5px solid var(--color-border);
  box-shadow: var(--shadow-03);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.setup-card__header {
  flex-shrink: 0;
  padding: var(--space-05) var(--space-05) var(--space-02);
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

/* Back button */
.setup-card__back {
  display: flex;
  align-items: center;
  gap: var(--space-01);
  color: var(--color-text-secondary);
  text-transform: uppercase;
  cursor: pointer;
  background: none;
  border: none;
  padding-bottom: var(--space-03);
  text-align: left;
}

.setup-card__body {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  min-height: 0;
  padding: 0 var(--space-05) calc(48px + var(--space-03) + var(--space-03));
}

/* Sticky gradient at top of scroll area */
.setup-card__body::before {
  content: '';
  display: block;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  height: var(--space-06);
  margin-bottom: calc(-1 * var(--space-06));
  background: linear-gradient(to bottom, var(--color-background-neutral), transparent);
  z-index: 1;
  pointer-events: none;
}

/* Step content fills available space so footer area is always clear */
.setup-card__body> :first-child {
  flex: 1;
  margin-top: var(--space-06);
}

/* Welcome variant: centered content, no gradient */
.setup-card__body--welcome {
  padding-top: var(--space-05);
}

.setup-card__body--welcome::before {
  display: none;
}

.setup-card__body--welcome> :first-child {
  margin-top: 0;
}

/* Footer: absolute positioned at bottom of card */
.setup-card__footer {
  position: absolute;
  bottom: calc(var(--space-03) + env(safe-area-inset-bottom, 0px));
  left: calc(var(--space-03) + env(safe-area-inset-left, 0px));
  right: calc(var(--space-03) + env(safe-area-inset-right, 0px));
  z-index: 2;
}

.setup-card__footer .btn {
  width: 100%;
}

@media (max-aspect-ratio: 4/3) {
  .setup-wizard {
    padding: 0;
    background: var(--color-background-neutral);
  }

  .setup-card {
    max-width: 100%;
    height: 100%;
    max-height: none;
    border-radius: 0;
    outline: none;
    box-shadow: none;
  }

  .setup-card__body {
    padding: 0 calc(var(--space-06) + env(safe-area-inset-right, 0px)) calc(48px + var(--space-06) + var(--space-03)) calc(var(--space-06) + env(safe-area-inset-left, 0px));
  }

  .setup-card__header {
    padding: calc(var(--space-06) + env(safe-area-inset-top, 0px)) calc(var(--space-06) + env(safe-area-inset-right, 0px)) var(--space-02) calc(var(--space-06) + env(safe-area-inset-left, 0px));
  }

  .setup-card__body--welcome {
    padding-top: calc(var(--space-05) + env(safe-area-inset-top, 0px));
  }

  .setup-card__footer {
    left: calc(var(--space-06) + env(safe-area-inset-left, 0px));
    right: calc(var(--space-06) + env(safe-area-inset-right, 0px));
    bottom: var(--space-06);
  }

  /* Force desktop button size in setup */
  .setup-card__footer :deep(.btn--medium) {
    height: auto;
    padding: 12px 16px;
    border-radius: var(--radius-04);
  }
}

/* PWA standalone: button flush to bottom (white body fills safe area) */
@media (max-aspect-ratio: 4/3) and (display-mode: standalone) {
  .setup-card__footer {
    bottom: 0;
  }
}
</style>

<!-- Unscoped: force white body/html background in PWA safe area during setup -->
<style>
.setup-active body,
.setup-active #app::before {
  background-color: var(--color-background-neutral) !important;
}
</style>
