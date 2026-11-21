<!-- frontend/src/components/setup/NetworkStep.vue -->
<template>
  <div class="wifi-step">
    <div class="connection-section">
      <template v-if="loading">
        <!-- Skeleton rows -->
        <div class="connection-row">
          <div class="skeleton-icon shimmer"></div>
          <div class="skeleton-text-line shimmer" style="width: 70px"></div>
          <div class="skeleton-text-line shimmer connection-badge-skeleton"></div>
        </div>
        <div class="connection-row connection-row--wifi">
          <div class="skeleton-icon shimmer"></div>
          <div class="skeleton-text-line shimmer" style="width: 50px"></div>
          <div class="skeleton-text-line shimmer connection-badge-skeleton"></div>
        </div>
      </template>

      <template v-else>
        <!-- Ethernet row -->
        <div class="connection-row">
          <SvgIcon name="network" :size="24"
            :color="status.ethernet.connected ? 'var(--color-text-secondary)' : 'var(--color-text-tertiary)'" />
          <span class="text-body-medium">{{ t('network.ethernet') }}</span>
          <Badge class="connection-badge" :tone="status.ethernet.connected ? 'success' : 'neutral'">
            {{ status.ethernet.connected ? t('network.connected') : t('network.notConnected') }}
          </Badge>
        </div>

        <!-- WiFi row (always visible) -->
        <div class="connection-row connection-row--wifi">
          <WifiSignal :signal="wifiCardSignal" :size="24" />
          <span class="text-body-medium connection-row__ssid">{{ wifiDisplaySsid || t('network.wifi') }}</span>
          <Badge class="connection-badge" :tone="wifiBadgeTone" :pulse="status.wifi.connecting">
            {{ wifiBadgeLabel }}
          </Badge>
        </div>
      </template>
    </div>

    <!-- The wizard went through once, and the unit did not join the network saved -->
    <NoticeBox v-if="joinFailedSsid" kind="warning">{{ t('setup.wifi.joinFailed', { ssid: joinFailedSsid }) }}</NoticeBox>

    <!-- A browser on the open setup AP sends the key it types in clear; the kiosk types it locally -->
    <p v-if="showEthernetHint" class="text-body-small ethernet-hint">{{ t('setup.wifi.ethernetHint') }}</p>

    <!-- Network selector (country + scan + password). The network saved stays
         in the list: a key mistyped over the AP is typed again there. -->
    <NetworkSelector :submit-action="hotspotActive ? 'save' : 'connect'" @saved="triedSsid = ''">
      <template #action="{ network, password, connecting, connect, save }">
        <Button variant="brand" :loading="connecting"
          :disabled="connecting || (network.security && !password)"
          @click="hotspotActive ? save() : connect()">
          {{ connecting
            ? (hotspotActive ? t('network.saving') : t('network.connecting'))
            : (hotspotActive ? t('network.save') : t('network.connect')) }}
        </Button>
      </template>
    </NetworkSelector>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useNetwork, refreshWifiSignal } from '@/composables/useNetwork';
import { useTimer } from '@/composables/useTimer';
import { WIFI_SIGNAL_POLL_MS } from '@/constants/network';
import { isKiosk } from '@/utils/kiosk';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';
import NetworkSelector from '@/components/network/NetworkSelector.vue';
import Badge from '@/components/ui/Badge.vue';
import Button from '@/components/ui/Button.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const { t } = useI18n();
const timer = useTimer();

// Status display state — selection state lives inside NetworkSelector.
const { status, networks, loading, initialize } = useNetwork();

const emit = defineEmits(['update:modelValue']);

const props = defineProps({
  modelValue: {
    type: String,
    default: null,
  },
  hotspotActive: {
    type: Boolean,
    default: false,
  },
  // The wizard was sent once and the unit has joined no network since.
  awaitingNetwork: {
    type: Boolean,
    default: false,
  },
});

// The network saved when the step opened: the one the unit tried at its last
// start and could not join. Read once, so a network saved here since is not
// named in its place; cleared by a save here, the same SSID with its key typed
// again included, since what failed is no longer what the unit will try.
const triedSsid = ref(null);
watch(loading, (busy) => {
  if (!busy && triedSsid.value === null) triedSsid.value = status.value.wifi.saved_ssid || '';
}, { immediate: true });

const joinFailedSsid = computed(() => {
  if (!props.awaitingNetwork || !triedSsid.value) return null;
  if (status.value.wifi.connected || status.value.ethernet.connected) return null;
  return triedSsid.value;
});

const showEthernetHint = computed(() =>
  props.hotspotActive && !isKiosk() && !loading.value && !status.value.ethernet.connected
);

// WiFi display: connected SSID or saved SSID (ready when ethernet takes over)
const wifiDisplaySsid = computed(() =>
  status.value.wifi.ssid || status.value.wifi.saved_ssid
);

const wifiCardSignal = computed(() => {
  if (status.value.wifi.connected) return status.value.wifi.signal;
  const ssid = wifiDisplaySsid.value;
  return ssid ? (networks.value.find(n => n.ssid === ssid)?.signal ?? null) : null;
});

// The network shown is the one the unit could not join: not "Ready".
const wifiJoinFailed = computed(() =>
  !!joinFailedSsid.value && wifiDisplaySsid.value === joinFailedSsid.value
);

// Disconnected stays neutral: at setup an unplugged cable is not an error.
const wifiBadgeTone = computed(() => {
  if (status.value.wifi.connected) return 'success';
  if (status.value.wifi.connecting) return 'warning';
  if (wifiJoinFailed.value) return 'warning';
  if (wifiDisplaySsid.value) return 'brand';
  return 'neutral';
});

const wifiBadgeLabel = computed(() => {
  if (status.value.wifi.connected) return t('network.connected');
  if (status.value.wifi.connecting) return t('network.connecting');
  if (wifiJoinFailed.value) return t('network.notConnected');
  if (wifiDisplaySsid.value) return t('network.ready');
  return t('network.notConnected');
});

// Emit connection identifier: wifi SSID or 'ethernet' — wizard disables CTA until truthy
watch(() => [status.value.ethernet.connected, status.value.wifi.connected, status.value.wifi.saved_ssid], () => {
  if (status.value.wifi.connected) {
    emit('update:modelValue', status.value.wifi.ssid || 'wifi');
  } else if (status.value.wifi.saved_ssid) {
    emit('update:modelValue', status.value.wifi.saved_ssid);
  } else if (status.value.ethernet.connected) {
    emit('update:modelValue', 'ethernet');
  } else {
    emit('update:modelValue', null);
  }
}, { immediate: true });

onMounted(() => {
  initialize();
  // The WiFi row is on screen for this step's whole life, so the arc reads
  // itself for exactly that long — useTimer stops it on unmount.
  timer.setInterval(refreshWifiSignal, WIFI_SIGNAL_POLL_MS);
});
</script>

<style scoped>
.wifi-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

/* Connection status card (grouped ethernet + wifi) */
.connection-section {
  background: var(--color-inset);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
}

.connection-row {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.connection-row--wifi {
  margin-top: var(--space-03);
  padding-top: var(--space-03);
  border-top: 1px solid var(--color-border);
}

/* Connection row skeletons */
.skeleton-icon {
  width: 24px;
  height: 24px;
  border-radius: var(--radius-02);
  flex-shrink: 0;
}

.connection-badge-skeleton {
  margin-left: auto;
  width: 64px;
  height: 26px;
  border-radius: var(--radius-full);
}

.connection-row__ssid {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.connection-badge {
  margin-left: auto;
}

.ethernet-hint {
  color: var(--color-text-secondary);
}
</style>
