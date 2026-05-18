<!-- frontend/src/components/settings/categories/IrRemoteSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Hardware disabled: redirect to Hardware Settings -->
    <MessageContent
      v-if="!irHardwareEnabled"
      icon="hardware"
      :title="t('irRemoteSettings.hardwareDisabledTitle')"
      :details="t('irRemoteSettings.hardwareDisabledDetails')"
      :cta-label="t('irRemoteSettings.openHardwareSettings')"
      :cta-click="() => emit('open-hardware')"
    />

    <!-- Paired: regular settings -->
    <ToggleSection
      v-else-if="settingsStore.irRemote.paired"
      heading="3"
      :enabled="settingsStore.irRemote.enabled"
      @change="handleToggle"
    >
      <template #title>
        <span class="ir-remote-status">
          <span class="ir-remote-status__dot" />
          {{ t('remoteControls.status.paired') }}
        </span>
      </template>
      <template #actions>
        <Button
          class="unpair-button unpair-button--desktop"
          variant="background-strong"
          size="small"
          :loading="unpairing"
          :disabled="unpairing"
          @click="handleUnpair"
        >
          {{ t('irRemoteSettings.unpair') }}
        </Button>
      </template>

      <SettingItem :label="t('irRemoteSettings.step')">
        <RangeSlider
          v-model="stepIrRemoteDb"
          :min="1" :max="6" :step="1"
          value-unit=" dB"
          @input="debouncedUpdate('ir-remote-steps', 'ir-remote-steps', { step_ir_remote_db: $event })"
        />
      </SettingItem>

      <Button
        class="unpair-button unpair-button--mobile"
        variant="background-strong"
        size="small"
        :loading="unpairing"
        :disabled="unpairing"
        @click="handleUnpair"
      >
        {{ t('irRemoteSettings.unpair') }}
      </Button>
    </ToggleSection>

    <!-- Not paired: wizard -->
    <MessageContent
      v-else
      :loading="fsmState === 'waiting'"
      :loading-delay="0"
      :icon="messageIcon"
      :title="messageTitle"
      :details="messageDetails"
      :cta-label="primaryCtaLabel"
      :cta-variant="primaryCtaVariant"
      :cta-click="primaryCtaClick"
      :cta-loading="ctaLoading"
      :cta-secondary-label="secondaryCtaLabel"
      :cta-secondary-click="secondaryCtaClick"
    />
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { useTimer } from '@/composables/useTimer';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import Button from '@/components/ui/Button.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const emit = defineEmits(['open-hardware']);

const { t } = useI18n();
const { debouncedUpdate, clearAllTimers } = useSettingsAPI();
const settingsStore = useSettingsStore();
const { hardwareConfig } = useHardwareConfig();
const timer = useTimer();

const irHardwareEnabled = computed(
  () => hardwareConfig.value?.current?.ir_remote?.enabled !== false
);

const PAIRING_TIMEOUT_SECONDS = 15;

// === Paired view ===
const stepIrRemoteDb = ref(settingsStore.volumeSteps.step_ir_remote_db);
const unpairing = ref(false);

watch(
  () => settingsStore.volumeSteps.step_ir_remote_db,
  (value) => { stepIrRemoteDb.value = value; }
);

async function handleToggle(enabled) {
  await settingsStore.toggleIrRemote(enabled);
}

async function handleUnpair() {
  unpairing.value = true;
  try {
    await settingsStore.unpairIrRemote();
    // After unpair, the wizard re-appears via reactive `paired` flag.
    fsmState.value = 'idle';
  } finally {
    unpairing.value = false;
  }
}

// === Wizard (not-paired) view ===
//
// Local FSM: idle → waiting → (timeout | unsupported | error)
//
// Success has no FSM branch: when the backend captures a scancode it sets
// `paired = true` and broadcasts the status via WS before the HTTP response
// resolves, which flips the v-if to the paired view. The paired view IS the
// success confirmation.

const fsmState = ref('idle');           // 'idle' | 'waiting' | 'timeout' | 'unsupported' | 'error'
const errorMessage = ref('');
const remainingSeconds = ref(PAIRING_TIMEOUT_SECONDS);
let countdownTimer = null;

const ctaLoading = computed(() => fsmState.value === 'waiting');

const messageIcon = computed(() => {
  switch (fsmState.value) {
    case 'idle':
    case 'waiting':
      return null;
    case 'timeout':
    case 'unsupported':
    case 'error':
      return 'stop';
    default:
      return null;
  }
});

const messageTitle = computed(() => {
  switch (fsmState.value) {
    case 'idle':         return t('irRemoteSettings.wizard.idleTitle');
    case 'waiting':      return t('irRemoteSettings.wizard.waitingTitle');
    case 'timeout':      return t('irRemoteSettings.wizard.timeoutTitle');
    case 'unsupported':  return t('irRemoteSettings.wizard.unsupportedTitle');
    case 'error':        return t('irRemoteSettings.wizard.errorTitle');
    default:             return '';
  }
});

const messageDetails = computed(() => {
  switch (fsmState.value) {
    case 'idle':         return t('irRemoteSettings.wizard.idleDetails');
    case 'waiting':      return t('irRemoteSettings.wizard.waitingDetails', { seconds: remainingSeconds.value });
    case 'timeout':      return t('irRemoteSettings.wizard.timeoutDetails');
    case 'unsupported':  return t('irRemoteSettings.wizard.unsupportedDetails');
    case 'error':        return errorMessage.value || t('irRemoteSettings.wizard.errorDetails');
    default:             return '';
  }
});

const primaryCtaLabel = computed(() => {
  switch (fsmState.value) {
    case 'idle':         return t('irRemoteSettings.wizard.startCta');
    case 'waiting':      return t('irRemoteSettings.wizard.cancelCta');
    case 'timeout':
    case 'unsupported':
    case 'error':
      return t('irRemoteSettings.wizard.retryCta');
    default:             return null;
  }
});

const primaryCtaVariant = computed(() => {
  return fsmState.value === 'waiting' ? 'background-strong' : 'brand';
});

const primaryCtaClick = computed(() => {
  switch (fsmState.value) {
    case 'idle':         return startPairing;
    case 'waiting':      return cancelPairing;
    case 'timeout':
    case 'unsupported':
    case 'error':
      return startPairing;
    default:             return null;
  }
});

const secondaryCtaLabel = computed(() => null);
const secondaryCtaClick = computed(() => null);

function startCountdown() {
  remainingSeconds.value = PAIRING_TIMEOUT_SECONDS;
  stopCountdown();
  countdownTimer = timer.setInterval(() => {
    if (remainingSeconds.value > 0) {
      remainingSeconds.value -= 1;
    }
  }, 1000);
}

function stopCountdown() {
  if (countdownTimer) {
    timer.clear(countdownTimer);
    countdownTimer = null;
  }
}

async function startPairing() {
  errorMessage.value = '';
  fsmState.value = 'waiting';
  startCountdown();
  let result;
  try {
    result = await settingsStore.startIrRemotePairing();
  } catch (e) {
    stopCountdown();
    fsmState.value = 'error';
    errorMessage.value = e?.message || '';
    return;
  }
  stopCountdown();

  if (!result || result.status === 'error') {
    fsmState.value = 'error';
    errorMessage.value = result?.message || '';
    return;
  }
  if (result.status === 'success') {
    // Backend has already set paired=true + broadcast status; the parent
    // view re-renders to the paired branch via the reactive `paired` flag,
    // unmounting this wizard. We don't transition the FSM here — the
    // 'paired' view is the success feedback.
    return;
  }
  if (result.status === 'timeout') {
    fsmState.value = 'timeout';
    return;
  }
  if (result.status === 'unsupported') {
    fsmState.value = 'unsupported';
    return;
  }
  if (result.status === 'cancelled') {
    fsmState.value = 'idle';
    return;
  }
  // Unknown status: treat as error.
  fsmState.value = 'error';
}

async function cancelPairing() {
  await settingsStore.cancelIrRemotePairing();
  // The waiting request will resolve with status=cancelled and reset the FSM.
}

onMounted(() => {
  settingsStore.loadIrRemoteStatus();
});

onBeforeUnmount(() => {
  // If the user navigates away mid-pairing, abort the capture so the device
  // doesn't stay locked in pairing mode for the full timeout.
  if (fsmState.value === 'waiting') {
    settingsStore.cancelIrRemotePairing();
  }
  stopCountdown();
});

onUnmounted(() => {
  clearAllTimers();
});
</script>

<style scoped>
.ir-remote-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-02);
  vertical-align: top;
}

.ir-remote-status__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-success);
}

/* Desktop: button lives in the header actions slot. Mobile: full-width below the slider. */
.unpair-button--mobile {
  display: none;
}

@media (max-aspect-ratio: 4/3) {
  .unpair-button--desktop {
    display: none;
  }

  .unpair-button--mobile {
    display: flex;
    width: 100%;
    margin-top: var(--space-04);
  }
}
</style>
