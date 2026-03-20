<!-- frontend/src/components/setup/SetupWizard.vue -->
<template>
  <div class="setup-wizard">
    <div class="setup-card">
      <!-- Header (steps 1+, outside transition) -->
      <div v-if="currentStep > 0" class="setup-card__header">
        <button class="setup-card__back text-mono" @click="prevStep">
          <SvgIcon name="caretLeft" :size="20" />
          {{ isSummaryStep ? t('setup.back') : t('setup.stepLabel', { n: stepIndex }) }}
        </button>
        <h2 class="heading-2">{{ stepTitle }}</h2>
        <StepIndicator :current="stepIndex - 1" :total="totalIndicatorSteps" />
      </div>

      <!-- Body with crossfade -->
      <Transition name="step-fade" mode="out-in">
        <div v-if="currentStep === 0" key="welcome" class="setup-card__body setup-card__body--welcome">
          <WelcomeStep />
        </div>

        <div v-else :key="currentStep" class="setup-card__body">
          <LanguageStep v-if="currentStep === 1" v-model="wizardState.language" />

          <ModeStep v-else-if="currentStep === 2" v-model="wizardState.mode" />

          <WifiStep v-else-if="currentStep === 3" v-model="wizardState.wifiSsid" :hotspot-active="settingsStore.hotspotActive" />

          <AudioStep v-else-if="currentStep === 4" v-model="wizardState.audioId" :audio-cards="audioCards" />

          <ScreenStep v-else-if="currentStep === 5" v-model="wizardState.screenType" :screens="screens" />

          <SummaryStep v-else-if="currentStep === 6" :mode-label="selectedModeLabel"
            :language-code="wizardState.language" :language-label="selectedLanguageLabel"
            :audio-label="selectedAudioLabel" :screen-label="selectedScreenLabel"
            :wifi-ssid="wizardState.wifiSsid" :is-rebooting="isRebooting" :error="error"
            :is-client="isClientMode" />
        </div>
      </Transition>

      <!-- Unified footer (absolute positioned, all steps) -->
      <div class="setup-card__footer">
        <Button v-if="currentStep === 0" variant="brand" @click="nextStep">
          {{ t('setup.welcome.getStarted') }}
        </Button>

        <Button v-else-if="currentStep === 3" variant="brand" :disabled="!wifiCountry" @click="nextStep">
          {{ wizardState.wifiSsid ? t('setup.continue') : t('setup.skip') }}
        </Button>

        <Button v-else-if="!isSummaryStep" variant="brand" @click="nextStep">
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
import { useSettingsStore } from '@/stores/settingsStore';
import { useWifi } from '@/composables/useWifi';
import axios from 'axios';
import { logger } from '@/services/logger';
import StepIndicator from './StepIndicator.vue';
import WelcomeStep from './WelcomeStep.vue';
import WifiStep from './WifiStep.vue';
import LanguageStep from './LanguageStep.vue';
import ModeStep from './ModeStep.vue';
import AudioStep from './AudioStep.vue';
import ScreenStep from './ScreenStep.vue';
import SummaryStep from './SummaryStep.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const { t } = useI18n();
const { loadHardwareConfig } = useHardwareConfig();
const settingsStore = useSettingsStore();
const { country: wifiCountry } = useWifi();

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
  mode: 'server',
  wifiSsid: null,
  language: i18n.getCurrentLanguage() || 'english',
  audioId: 'none',
  screenType: 'none',
});

const isClientMode = computed(() => wizardState.mode === 'client');

// Steps that are skipped in client mode (audio=4, screen=5)
const skippedSteps = computed(() => isClientMode.value ? new Set([4, 5]) : new Set());

// All steps: 0=welcome, 1=language, 2=mode, 3=wifi, 4=audio, 5=screen, 6=summary
const TOTAL_STEPS = 7;

// Ordered list of visible step indices
const visibleSteps = computed(() => {
  const steps = [];
  for (let i = 0; i < TOTAL_STEPS; i++) {
    if (!skippedSteps.value.has(i)) {
      steps.push(i);
    }
  }
  return steps;
});

// Position of current step within visible steps (1-based, excluding welcome)
const stepIndex = computed(() => {
  const idx = visibleSteps.value.indexOf(currentStep.value);
  return idx >= 0 ? idx : 1;
});

// Total indicator steps (excluding welcome)
const totalIndicatorSteps = computed(() => visibleSteps.value.length - 1);

const isSummaryStep = computed(() => currentStep.value === 6);

// Step titles from i18n
const stepTitles = {
  1: 'setup.language.title',
  2: 'setup.mode.title',
  3: 'setup.wifi.title',
  4: 'setup.audio.title',
  5: 'setup.screen.title',
  6: 'setup.summary.title',
};

const stepTitle = computed(() => t(stepTitles[currentStep.value] || ''));

// Computed labels for summary
const selectedModeLabel = computed(() =>
  t(isClientMode.value ? 'setup.mode.client' : 'setup.mode.server')
);

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

const { getAvailableLanguages } = useI18n();

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
  const idx = visibleSteps.value.indexOf(currentStep.value);
  if (idx < visibleSteps.value.length - 1) {
    currentStep.value = visibleSteps.value[idx + 1];
  }
}

function prevStep() {
  const idx = visibleSteps.value.indexOf(currentStep.value);
  if (idx > 0) {
    currentStep.value = visibleSteps.value[idx - 1];
    confirmReboot.value = false;
  }
}

async function applySetup() {
  isApplying.value = true;
  confirmReboot.value = false;
  error.value = null;

  try {
    await axios.post('/api/setup/complete', {
      mode: wizardState.mode,
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

/* Crossfade between steps (body only) */
.step-fade-enter-active,
.step-fade-leave-active {
  transition: opacity 200ms ease;
}

.step-fade-enter-from,
.step-fade-leave-to {
  opacity: 0;
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
