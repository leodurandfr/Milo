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
          <span class="text-body">{{ t('network.ethernet') }}</span>
          <Badge class="connection-badge" :tone="status.ethernet.connected ? 'success' : 'neutral'">
            {{ status.ethernet.connected ? t('network.connected') : t('network.notConnected') }}
          </Badge>
        </div>

        <!-- WiFi row (always visible) -->
        <div class="connection-row connection-row--wifi">
          <WifiSignal :signal="wifiCardSignal" :size="24" />
          <span class="text-body connection-row__ssid">{{ wifiDisplaySsid || t('network.wifi') }}</span>
          <Badge class="connection-badge" :tone="wifiBadgeTone" :pulse="wifiBadgeTone === 'warning'">
            {{ wifiBadgeLabel }}
          </Badge>
        </div>
      </template>
    </div>

    <!-- Network selector (country + scan + password) -->
    <NetworkSelector
      :exclude-ssid="wifiDisplaySsid || undefined"
      :submit-action="hotspotActive ? 'save' : 'connect'"
    >
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
import { computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useNetwork, refreshWifiSignal } from '@/composables/useNetwork';
import { useTimer } from '@/composables/useTimer';
import { WIFI_SIGNAL_POLL_MS } from '@/constants/network';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';
import NetworkSelector from '@/components/network/NetworkSelector.vue';
import Badge from '@/components/ui/Badge.vue';
import Button from '@/components/ui/Button.vue';
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
});

// WiFi display: connected SSID or saved SSID (ready when ethernet takes over)
const wifiDisplaySsid = computed(() =>
  status.value.wifi.ssid || status.value.wifi.saved_ssid
);

const wifiCardSignal = computed(() => {
  if (status.value.wifi.connected) return status.value.wifi.signal;
  const ssid = wifiDisplaySsid.value;
  return ssid ? (networks.value.find(n => n.ssid === ssid)?.signal ?? null) : null;
});

// Disconnected stays neutral: at setup an unplugged cable is not an error.
const wifiBadgeTone = computed(() => {
  if (status.value.wifi.connected) return 'success';
  if (status.value.wifi.connecting) return 'warning';
  if (wifiDisplaySsid.value) return 'brand';
  return 'neutral';
});

const wifiBadgeLabel = computed(() => {
  if (status.value.wifi.connected) return t('network.connected');
  if (status.value.wifi.connecting) return t('network.connecting');
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
</style>
