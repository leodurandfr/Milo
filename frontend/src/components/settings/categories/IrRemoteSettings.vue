<!-- frontend/src/components/settings/categories/IrRemoteSettings.vue -->
<template>
  <SectionStack>
    <!-- Hardware disabled: redirect to Hardware Settings -->
    <MessageContent
      v-if="!irHardwareEnabled"
      icon="remoteControl"
      :title="t('irRemoteSettings.hardwareDisabledTitle')"
      :details="t('irRemoteSettings.hardwareDisabledDetails')"
      :cta-label="t('irRemoteSettings.openHardwareSettings')"
      :cta-click="() => emit('open-hardware')"
    />

    <!-- Disabled: invite the user to enable the feature from the header toggle. -->
    <MessageContent
      v-else-if="!settingsStore.irRemote.enabled"
      icon="remoteControl"
      :title="t('irRemoteSettings.disabledTitle')"
      :details="t('irRemoteSettings.disabledDetails')"
    />

    <!-- Paired: status card (shared with BT) — status, volume step and unpair. -->
    <RemoteStatusSection
      v-else-if="settingsStore.irRemote.paired"
      v-model="stepIrRemoteDb"
      :ok="settingsStore.irRemote.listening"
      :status-label="settingsStore.irRemote.listening
        ? t('remoteControls.status.paired')
        : t('remoteControls.status.receiverDown')"
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
      icon="remoteControl"
      :title="messageTitle"
      :details="messageDetails"
      :cta-label="primaryCtaLabel"
      :cta-variant="primaryCtaVariant"
      :cta-click="primaryCtaClick"
    />

    <!-- The header switch refused; a pairing, a Cancel or an Unpair that
         failed, its reason folded under the sentence. -->
    <NoticeBox v-if="toggleRefusal !== null" :detail="toggleRefusal">{{ t('settings.writeRefused') }}</NoticeBox>
    <NoticeBox v-if="pairingFailed" :detail="actionRefusal">{{ t('irRemoteSettings.wizard.errorDetails') }}</NoticeBox>
    <NoticeBox v-else-if="actionRefusal !== null" :detail="actionRefusal">{{ t('settings.actionFailed') }}</NoticeBox>

    <SettingsRefusal :detail="refusal" />
  </SectionStack>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { useTimer } from '@/composables/useTimer';
import SectionStack from '@/components/ui/SectionStack.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import RemoteStatusSection from '@/components/settings/categories/RemoteStatusSection.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import SettingsRefusal from '@/components/settings/SettingsRefusal.vue';

defineProps({
  // The server's detail when the header's switch (SettingsModal) was refused.
  toggleRefusal: { type: String, default: null }
});

const emit = defineEmits(['open-hardware']);

const { t, formatUnit } = useI18n();
const settingsStore = useSettingsStore();
const { hardwareConfig } = useHardwareConfig();
const timer = useTimer();

const irHardwareEnabled = computed(
  () => hardwareConfig.value?.current?.ir_remote?.enabled !== false
);

const PAIRING_TIMEOUT_SECONDS = 15;

// === Paired view ===
const stepIrRemoteDb = ref(settingsStore.volumeSteps.step_ir_remote_db);
// The step slider holds its own copy, which a refused write puts back.
const { updateSetting, refusal } = useSettingsAPI({
  resync: () => { stepIrRemoteDb.value = settingsStore.volumeSteps.step_ir_remote_db; }
});
const unpairing = ref(false);
// The server's detail for the last pairing, Cancel or Unpair that failed: the
// backend's own `message` for a capture it reports as failed, the HTTP error's
// detail otherwise.
const actionRefusal = ref(null);

watch(
  () => settingsStore.volumeSteps.step_ir_remote_db,
  (value) => { stepIrRemoteDb.value = value; }
);

async function handleUnpair() {
  unpairing.value = true;
  actionRefusal.value = null;
  try {
    await settingsStore.unpairIrRemote({ errorRef: actionRefusal });
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
// A failed capture is said under the wizard, the one way every settings failure is.
const pairingFailed = computed(() => fsmState.value === 'error');
const showsWizard = computed(
  () => irHardwareEnabled.value && settingsStore.irRemote.enabled && !settingsStore.irRemote.paired
);
// The failure belongs to the wizard: once another view replaces it (the remote
// switched off, the receiver gone, a pairing made elsewhere), it goes with it.
watch(showsWizard, (shown) => {
  if (!shown && fsmState.value === 'error') {
    fsmState.value = 'idle';
    actionRefusal.value = null;
  }
});
const remainingSeconds = ref(PAIRING_TIMEOUT_SECONDS);
let countdownTimer = null;

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
    case 'waiting':      return t('irRemoteSettings.wizard.waitingDetails', { time: formatUnit(remainingSeconds.value, 's') });
    case 'timeout':      return t('irRemoteSettings.wizard.timeoutDetails');
    case 'unsupported':  return t('irRemoteSettings.wizard.unsupportedDetails');
    case 'error':        return '';
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

const primaryCtaVariant = computed(() => (fsmState.value === 'waiting' ? 'control' : 'brand'));

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
  actionRefusal.value = null;
  fsmState.value = 'waiting';
  startCountdown();
  // A refusal sets actionRefusal: the store answers with a verdict, never a throw.
  const result = await settingsStore.startIrRemotePairing({ errorRef: actionRefusal });
  stopCountdown();

  if (!result || result.status === 'error') {
    fsmState.value = 'error';
    if (result?.message) actionRefusal.value = result.message;
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
  actionRefusal.value = null;
  await settingsStore.cancelIrRemotePairing({ errorRef: actionRefusal });
  // The waiting request will resolve with status=cancelled and reset the FSM.
}

onMounted(() => {
  settingsStore.loadIrRemoteStatus();
});

onBeforeUnmount(() => {
  // If the user navigates away mid-pairing, abort the capture so the device
  // doesn't stay locked in pairing mode for the full timeout.
  if (fsmState.value === 'waiting') {
    // No errorRef: nobody is in front of this view any more to read a refusal.
    settingsStore.cancelIrRemotePairing();
  }
  stopCountdown();
});
</script>
