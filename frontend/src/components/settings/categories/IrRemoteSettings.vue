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

    <!-- Disabled: invite the user to enable the feature from the header toggle. -->
    <MessageContent
      v-else-if="!settingsStore.irRemote.enabled"
      icon="infrared"
      :title="t('irRemoteSettings.disabledTitle')"
      :details="t('irRemoteSettings.disabledDetails')"
    />

    <!-- Paired: status card (shared with BT) — status, volume step and unpair. -->
    <RemoteStatusSection
      v-else-if="settingsStore.irRemote.paired"
      v-model="stepIrRemoteDb"
      :ok="true"
      :status-label="t('remoteControls.status.paired')"
      :step-label="t('irRemoteSettings.step')"
      :show-unpair="true"
      :unpair-label="t('irRemoteSettings.unpair')"
      :unpair-loading="unpairing"
      :unpair-click="handleUnpair"
      @step-change="updateSetting('ir-remote-steps', { step_ir_remote_db: $event })"
    />

    <!-- Not paired: pairing wizard. The user must press a key on the remote, so the
         centered message carries the icon + instructions + countdown the card can't. -->
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
    />
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { useTimer } from '@/composables/useTimer';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import RemoteStatusSection from '@/components/settings/categories/RemoteStatusSection.vue';

const emit = defineEmits(['open-hardware']);

const { t } = useI18n();
const { updateSetting } = useSettingsAPI();
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
// resolves, which flips to the paired view. The paired view IS the success
// confirmation.

const fsmState = ref('idle');           // 'idle' | 'waiting' | 'timeout' | 'unsupported' | 'error'
const errorMessage = ref('');
const remainingSeconds = ref(PAIRING_TIMEOUT_SECONDS);
let countdownTimer = null;

const messageIcon = computed(() => {
  switch (fsmState.value) {
    case 'idle':
    case 'waiting':
      return null;
    default:
      return 'stop';
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
    default:             return t('irRemoteSettings.wizard.retryCta');
  }
});

const primaryCtaVariant = computed(() => (fsmState.value === 'waiting' ? 'background-strong' : 'brand'));

const primaryCtaClick = computed(() => (fsmState.value === 'waiting' ? cancelPairing : startPairing));

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
    // Backend already set paired=true + broadcast status; the view re-renders to
    // the paired branch via the reactive `paired` flag. The paired view is the
    // success feedback, so we don't transition the FSM here.
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
</script>
