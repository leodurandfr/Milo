<!-- frontend/src/components/system/PresenceDialog.vue -->
<!-- The gesture on the unit that a request reaching root waits for
     (presenceStore): which one, for what, and the time left. Drawn by the tab
     that asked, since the challenge's token is that tab's alone, and never
     with a code to type: the unit's own screen is a page that can be read
     from wherever it is open.
     The unit's own screen also draws a challenge another device asked for,
     without its gesture and with nothing to do but close: the knob under it is
     the one the owner turns for the volume, and a turn made while a stranger's
     challenge waits confirms it. And it says when the unit would wait for a
     power cut, which that screen goes down with: nothing was opened, so the
     phone or computer it sends the owner to can ask at once.
     Not a Modal: a Modal closes itself after two minutes without a touch, and
     a proof by power cut spends longer than that with nobody touching
     anything — closing would cancel the proof the unit is booting into. -->
<template>
  <div v-if="shown" class="presence-overlay" role="dialog" aria-modal="true" :aria-label="title">
    <div class="presence-card">
      <SvgIcon :name="icon" :size="48" color="var(--color-fill-soft)" />
      <p class="heading-2 presence-title">{{ title }}</p>
      <p class="text-body-medium presence-action">{{ actionLabel }}</p>
      <p v-if="details" class="text-body-medium presence-details">{{ details }}</p>
      <p v-if="timeLine" class="text-mono-medium presence-time">{{ timeLine }}</p>
      <Button variant="control" @click="close">
        {{ ended || foreign ? t('common.close') : t('system.presence.cancel') }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useTimer } from '@/composables/useTimer';
import { usePresenceStore } from '@/stores/presenceStore';
import { isKiosk } from '@/utils/kiosk';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import Button from '@/components/ui/Button.vue';

const { t, formatDuration } = useI18n();
const timer = useTimer();
const presence = usePresenceStore();
const { challenge: own, othersChallenge } = storeToRefs(presence);
// A power cut takes the socket down with the unit: while it is down, the
// countdown means nothing and the page waits for the unit instead.
const { showDisconnectedBanner } = useWebSocket();
const onTheUnit = isKiosk();

// A challenge another device asked for, closed on this screen: drawn again
// only for the next one.
const closedForeignId = ref(null);
const foreignChallenge = computed(() => {
  const theirs = othersChallenge.value;
  if (!onTheUnit || !theirs) return null;
  return theirs.id === closedForeignId.value ? null : theirs;
});

const challenge = computed(() => own.value || foreignChallenge.value);
const shown = computed(() => challenge.value !== null);
const foreign = computed(() => !own.value && foreignChallenge.value !== null);

const now = ref(Date.now());
let tick = null;
watch(() => challenge.value?.status === 'pending', (pending) => {
  timer.clear(tick);
  tick = null;
  if (pending) {
    now.value = Date.now();
    tick = timer.setInterval(() => { now.value = Date.now(); }, 1000);
  }
}, { immediate: true });

const expired = computed(() => challenge.value?.status === 'expired');
const elsewhere = computed(() => challenge.value?.status === 'elsewhere');
// Over: nothing left to cancel, only the dialog to close.
const ended = computed(() => expired.value || elsewhere.value);

const icon = computed(() => (challenge.value?.gesture === 'rotary' ? 'hardware' : 'power'));

const title = computed(() => {
  if (foreign.value) return t('system.presence.foreignTitle');
  if (expired.value) return t('system.presence.expiredTitle');
  if (elsewhere.value) return t('system.presence.elsewhereTitle');
  if (challenge.value?.status === 'confirmed') return t('system.presence.confirmed');
  return t('system.presence.title');
});

const actionLabel = computed(() => t(`system.presence.actions.${challenge.value.action}`));

const details = computed(() => {
  if (foreign.value) return t('system.presence.foreign');
  if (expired.value) return t('system.presence.expired');
  if (elsewhere.value) return t('system.presence.elsewhere');
  if (challenge.value?.status === 'confirmed') return null;
  // The wait the unit imposes after a challenge that ends unspent, for the
  // gesture text that sends the owner back to ask again.
  return t(`system.presence.gestures.${challenge.value.gesture}`, {
    time: formatDuration(challenge.value.cooldownS),
  });
});

const timeLine = computed(() => {
  if (challenge.value?.status !== 'pending') return null;
  if (showDisconnectedBanner.value) return t('system.presence.waiting');
  const seconds = Math.max(0, Math.ceil((challenge.value.deadline - now.value) / 1000));
  return t('system.presence.remaining', { time: formatDuration(seconds) });
});

function close() {
  if (foreign.value) closedForeignId.value = foreignChallenge.value.id;
  else if (ended.value) presence.dismiss();
  else presence.cancel();
}
</script>

<style scoped>
/* Above every Modal (the settings it was asked from), under the virtual
   keyboard and the banners. */
.presence-overlay {
  position: fixed;
  inset: 0;
  z-index: 5500;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: calc(var(--space-04) + var(--screen-inset));
  background: var(--color-overlay);
}

.presence-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  width: 100%;
  max-width: 480px;
  padding: var(--space-07) var(--space-06) var(--space-06);
  border-radius: var(--radius-06);
  background: var(--color-surface-solid);
  text-align: center;
}

.presence-title {
  margin: 0;
  color: var(--color-text);
}

.presence-action,
.presence-details,
.presence-time {
  margin: 0;
  color: var(--color-text-secondary);
}
</style>
