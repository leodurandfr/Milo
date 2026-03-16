<!-- frontend/src/components/settings/categories/NetworkSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Current status -->
    <SettingsSection>
      <div class="network-status">
        <div class="network-status__info">
          <span class="heading-3">{{ statusHeading }}</span>
          <span class="text-mono network-status__detail">
            <template v-if="status.connected && status.connection_type === 'wifi'">
              <SignalDots :signal="status.signal" />
              <span class="network-status__ip">{{ status.ip_address }}</span>
            </template>
            <template v-else-if="status.connected && status.connection_type === 'ethernet'">
              <span class="network-status__ip">{{ status.ip_address }}</span>
            </template>
            <template v-else-if="!status.saved_ssid">
              {{ t('network.noConnection') }}
            </template>
          </span>
        </div>
        <span class="network-status__badge text-mono-small" :class="statusBadgeClass">
          {{ statusBadgeLabel }}
        </span>
      </div>
    </SettingsSection>

    <!-- Saved networks -->
    <SettingsSection v-if="knownNetworks.length > 0 || loading">
      <template #header>
        <SectionHeader :title="t('network.savedNetworks')" />
      </template>

      <!-- Skeletons -->
      <template v-if="loading && knownNetworks.length === 0">
        <div v-for="i in (savedSsids.size || 1)" :key="'sk-known-' + i" class="network-skeleton">
          <div class="skeleton-text-line shimmer" style="width: 120px"></div>
          <div class="skeleton-text-line shimmer" style="width: 40px"></div>
        </div>
      </template>

      <!-- Network list -->
      <div v-for="network in knownNetworks" :key="'known-' + network.ssid" class="network-item"
        :class="{ 'network-item--active': network.in_use }" @click="selectNetwork(network)">
        <div class="network-item__row">
          <span class="heading-3 network-item__ssid">{{ network.ssid }}</span>
          <div class="network-item__meta">
            <SignalDots :signal="network.signal" />
            <button v-if="!network.in_use" v-press type="button" class="network-item__forget text-mono-small"
              @click.stop="forgetNetwork(network.ssid)">
              {{ t('network.forget') }}
            </button>
            <span v-if="network.in_use" class="network-item__connected text-mono-small">{{ t('network.connected') }}</span>
          </div>
        </div>

        <!-- Expand: password + connect (only for non-connected known networks) -->
        <div v-if="selectedSsid === network.ssid && !network.in_use" class="network-item__expand" @click.stop>
          <InputText v-if="network.security" v-model="password" type="password"
            :placeholder="t('network.password')" @submit="connectToNetwork(network, t)" />
          <Button variant="brand" :loading="connecting" :disabled="connecting || (network.security && !password)"
            @click="connectToNetwork(network, t)">
            {{ connecting ? t('network.connecting') : t('network.connect') }}
          </Button>
          <span v-if="connectError" class="network-error text-mono-small">{{ connectError }}</span>
        </div>
      </div>
    </SettingsSection>

    <!-- Other networks -->
    <SettingsSection>
      <template #header>
        <SectionHeader :title="t('network.otherNetworks')">
          <template #actions>
            <Button variant="background-strong" size="small" left-icon="arrowsClockwise"
              :loading="scanning" :disabled="scanning"
              @click="scanNetworks">
              {{ t('network.refresh') }}
            </Button>
          </template>
        </SectionHeader>
      </template>

      <!-- Skeletons -->
      <template v-if="loading && otherNetworks.length === 0">
        <div v-for="i in 4" :key="'sk-other-' + i" class="network-skeleton">
          <div class="skeleton-text-line shimmer" :style="{ width: (80 + i * 20) + 'px' }"></div>
          <div class="skeleton-text-line shimmer" style="width: 40px"></div>
        </div>
      </template>

      <!-- Empty state -->
      <div v-if="!loading && otherNetworks.length === 0" class="network-empty text-mono">
        {{ t('network.noNetworks') }}
      </div>

      <!-- Network list -->
      <div v-for="network in otherNetworks" :key="'other-' + network.ssid" class="network-item"
        @click="selectNetwork(network)">
        <div class="network-item__row">
          <div class="network-item__ssid-row">
            <span class="heading-3 network-item__ssid">{{ network.ssid }}</span>
            <span v-if="network.security" class="network-item__lock text-mono-small">{{ network.security }}</span>
          </div>
          <SignalDots :signal="network.signal" />
        </div>

        <!-- Expand: password + connect -->
        <div v-if="selectedSsid === network.ssid" class="network-item__expand" @click.stop>
          <InputText v-if="network.security" v-model="password" type="password"
            :placeholder="t('network.password')" @submit="connectToNetwork(network, t)" />
          <Button variant="brand" :loading="connecting" :disabled="connecting || (network.security && !password)"
            @click="connectToNetwork(network, t)">
            {{ connecting ? t('network.connecting') : t('network.connect') }}
          </Button>
          <span v-if="connectError" class="network-error text-mono-small">{{ connectError }}</span>
        </div>
      </div>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useWifi } from '@/composables/useWifi';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SignalDots from '@/components/settings/categories/wifi/SignalDots.vue';

const { t } = useI18n();

const {
  status,
  savedSsids,
  loading,
  scanning,
  connecting,
  connectError,
  selectedSsid,
  password,
  knownNetworks,
  otherNetworks,
  selectNetwork,
  scanNetworks,
  connectToNetwork,
  forgetNetwork,
  initialize,
} = useWifi();

const statusHeading = computed(() => {
  if (status.value.connected) {
    return status.value.connection_type === 'ethernet'
      ? t('network.ethernet')
      : status.value.ssid;
  }
  return status.value.saved_ssid || t('network.disconnected');
});

const statusBadgeClass = computed(() => {
  if (status.value.connected) return 'network-status__badge--connected';
  if (status.value.saved_ssid) return 'network-status__badge--saved';
  return 'network-status__badge--disconnected';
});

const statusBadgeLabel = computed(() => {
  if (status.value.connected) return t('network.connected');
  if (status.value.saved_ssid) return t('network.saved');
  return t('network.disconnected');
});

onMounted(() => {
  initialize();
});
</script>

<style scoped>
/* Status card */
.network-status {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
}

.network-status__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.network-status__detail {
  color: var(--color-text-secondary);
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.network-status__ip {
  color: var(--color-text-secondary);
}

.network-status__badge {
  flex-shrink: 0;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
}

.network-status__badge--connected {
  background: color-mix(in srgb, var(--color-success) 16%, transparent);
  color: var(--color-success);
}

.network-status__badge--saved {
  background: color-mix(in srgb, var(--color-brand) 16%, transparent);
  color: var(--color-brand);
}

.network-status__badge--disconnected {
  background: color-mix(in srgb, var(--color-error) 16%, transparent);
  color: var(--color-error);
}

/* Network items */
.network-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
  cursor: pointer;
  transition: background-color var(--transition-fast), var(--transition-press);
}

.network-item--active {
  cursor: default;
}

.network-item__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
}

.network-item__ssid-row {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  min-width: 0;
}

.network-item__ssid {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.network-item__lock {
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.network-item__meta {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  flex-shrink: 0;
}

.network-item__forget {
  color: var(--color-error);
  background: none;
  border: none;
  cursor: pointer;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
  transition: background-color var(--transition-fast), var(--transition-press);
}

.network-item__forget:hover {
  background: color-mix(in srgb, var(--color-error) 8%, transparent);
}

.network-item__connected {
  color: var(--color-success);
}

.network-item__saved {
  color: var(--color-brand);
}

/* Expanded connect form */
.network-item__expand {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding-top: var(--space-02);
}

.network-error {
  color: var(--color-error);
}

/* Empty state */
.network-empty {
  color: var(--color-text-secondary);
  text-align: center;
  padding: var(--space-04);
}

/* Skeleton */
.network-skeleton {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}

.network-skeleton .skeleton-text-line {
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background-medium-16);
}
</style>
