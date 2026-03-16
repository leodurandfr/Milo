<!-- frontend/src/components/settings/categories/NetworkSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Connection status card (MultiroomItem zone pattern) -->
    <div class="connection-section" :class="{ 'is-expanded': showWifiCard, 'no-transition': skipTransition }">
      <!-- Ethernet row (always visible, like zone header) -->
      <div class="connection-card">
        <div class="connection-card__name">
          <SvgIcon name="network" :size="20" />
          <span class="heading-3">{{ t('network.ethernet') }}</span>
        </div>
        <span class="connection-badge text-mono-small"
          :class="status.ethernet.connected ? 'connection-badge--connected' : 'connection-badge--disconnected'">
          {{ status.ethernet.connected ? t('network.connected') : t('network.notConnected') }}
        </span>
      </div>

      <!-- WiFi row (expandable, like expanded-clients) -->
      <div ref="wifiWrapperRef" class="expanded-wrapper" :class="{ 'no-transition': skipTransition }" :style="{ height: wifiRowHeight }">
        <div ref="wifiRowRef" class="expanded-content" :class="{ 'is-visible': showWifiCard }">
          <div class="connection-card">
            <div class="connection-card__name">
              <WifiSignal :signal="wifiCardSignal" />
              <span class="heading-3">{{ wifiDisplaySsid }}</span>
            </div>
            <span class="connection-badge text-mono-small" :class="wifiBadgeClass">
              {{ wifiBadgeLabel }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- WiFi section with toggle -->
    <ToggleSection
      :title="t('network.wifi')"
      :enabled="status.wifi_enabled"
      @change="handleWifiToggle"
    >
      <div ref="wifiContentRef" class="wifi-content">
        <span class="text-mono wifi-content__description">{{ t('network.wifiDescription') }}</span>

        <!-- Preferred network -->
        <div v-if="preferredNetwork" class="wifi-group">
          <span class="heading-3">{{ t('network.preferredNetwork') }}</span>
          <div class="network-item network-item--preferred">
            <div class="network-item__row">
              <div class="network-item__ssid-row">
                <WifiSignal :signal="preferredNetwork.signal" />
                <span class="heading-3 network-item__ssid">{{ preferredNetwork.ssid }}</span>
              </div>
              <Button variant="important" size="small" @click="forgetNetwork(preferredNetwork.ssid)">
                {{ t('network.forget') }}
              </Button>
            </div>
          </div>
        </div>

        <!-- Other networks -->
        <div class="wifi-group">
          <div class="network-subheader">
            <span class="heading-3 network-subheader__title">{{ t('network.otherNetworks') }}</span>
            <Button variant="background-strong" size="small" left-icon="arrowsClockwise"
              :loading="scanning" :disabled="scanning"
              @click="scanNetworks">
              {{ t('network.refresh') }}
            </Button>
          </div>

          <div class="network-list">
            <!-- Skeletons -->
            <template v-if="(loading || scanning) && otherNetworks.length === 0">
              <div v-for="i in 3" :key="'sk-' + i" class="network-skeleton">
                <div class="skeleton-text-line shimmer" :style="{ width: (80 + i * 20) + 'px' }"></div>
                <div class="skeleton-text-line shimmer" style="width: 40px"></div>
              </div>
            </template>

            <!-- Empty state -->
            <div v-if="!loading && !scanning && otherNetworks.length === 0" class="network-empty text-mono">
              {{ t('network.noNetworks') }}
            </div>

            <!-- Network items -->
            <div v-for="network in otherNetworks" :key="'other-' + network.ssid" class="network-item"
              @click="selectNetwork(network)">
              <div class="network-item__row">
                <div class="network-item__ssid-row">
                  <WifiSignal :signal="network.signal" />
                  <span class="heading-3 network-item__ssid">{{ network.ssid }}</span>
                </div>
                <SvgIcon name="caretDown" :size="24" color="var(--color-text-light)"
                  class="network-item__caret" :class="{ 'network-item__caret--open': selectedSsid === network.ssid }" />
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
          </div>
        </div>
      </div>
    </ToggleSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted, inject } from 'vue';
import { useI18n } from '@/services/i18n';
import { useWifi } from '@/composables/useWifi';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';

const { t } = useI18n();
const requestHeightDelta = inject('modalRequestHeightDelta', null);
const modalContentInnerRef = inject('modalContentInnerRef', null);

const {
  status,
  loading,
  scanning,
  connecting,
  connectError,
  selectedSsid,
  password,
  preferredNetwork,
  otherNetworks,
  selectNetwork,
  scanNetworks,
  connectToNetwork,
  forgetNetwork,
  toggleWifi,
  initialize,
} = useWifi();

const wifiDisplaySsid = computed(() =>
  status.value.wifi.ssid || status.value.wifi.saved_ssid
);

const wifiCardSignal = computed(() => {
  if (status.value.wifi.connected) return status.value.wifi.signal;
  return preferredNetwork.value?.signal ?? null;
});

const showWifiCard = computed(() =>
  status.value.wifi_enabled && !!wifiDisplaySsid.value
);

const wifiBadgeClass = computed(() => {
  if (status.value.ethernet.connected) return 'connection-badge--ready';
  if (status.value.wifi.connected) return 'connection-badge--connected';
  return 'connection-badge--disconnected';
});

const wifiBadgeLabel = computed(() => {
  if (status.value.ethernet.connected) return t('network.ready');
  if (status.value.wifi.connected) return t('network.connected');
  return t('network.disconnected');
});

// === WiFi row expand/collapse (MultiroomItem pattern) ===
const wifiWrapperRef = ref(null);
const wifiRowRef = ref(null);
const wifiContentRef = ref(null);
const wifiRowHeight = ref('0px');
const skipTransition = ref(true);
let skipNextWatcher = false;

function measureWifiRow() {
  if (!wifiRowRef.value) return 0;
  const el = wifiRowRef.value;
  const marginTop = parseFloat(getComputedStyle(el).marginTop) || 0;
  return el.offsetHeight + marginTop;
}

function getPaddingOffset() {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--space-05')) || 0;
}

// Announce wifi card + toggle content height deltas to Modal
// (additive to ToggleSection's own requestHeightDelta call)
function handleWifiToggle(enabled) {
  const wasVisible = showWifiCard.value;
  const prevHeight = wasVisible ? (parseFloat(wifiRowHeight.value) || 0) : 0;

  // Measure toggle slot content BEFORE optimistic update changes it
  // (offsetHeight works inside collapsed CSS grid: parent is 0px but children keep natural size)
  const oldToggleContentH = wifiContentRef.value?.offsetHeight || 0;

  toggleWifi(enabled);

  if (enabled && showWifiCard.value) {
    skipNextWatcher = true;
    announceWifiCardShow(oldToggleContentH);
  } else if (!enabled && wasVisible && prevHeight > 0) {
    skipNextWatcher = true;
    const paddingOffset = getPaddingOffset();
    wifiRowHeight.value = '0px';
    if (requestHeightDelta) requestHeightDelta(-(prevHeight - paddingOffset));
  }
}

async function announceWifiCardShow(oldToggleContentH) {
  await nextTick();

  // Measure toggle slot content AFTER DOM update (preferred network + skeletons appeared)
  const newToggleContentH = wifiContentRef.value?.offsetHeight || 0;
  const contentDelta = newToggleContentH - oldToggleContentH;

  // Wifi connection card delta (wrapper grows by fullHeight, parent loses paddingOffset)
  const fullHeight = measureWifiRow();
  const paddingOffset = getPaddingOffset();

  // Total correction: (wrapper height - padding offset) + content change inside ToggleSection
  const totalDelta = (fullHeight - paddingOffset) + contentDelta;
  if (requestHeightDelta && totalDelta > 2) {
    requestHeightDelta(totalDelta);
  }

  if (fullHeight > 0) {
    wifiRowHeight.value = `${fullHeight}px`;
  }
}

watch(showWifiCard, async (visible) => {
  console.log(`[NetworkSettings] watch showWifiCard → ${visible} | skipNext=${skipNextWatcher} | skipTransition=${skipTransition.value} ${ts()}`);
  if (skipNextWatcher) {
    skipNextWatcher = false;
    return;
  }

  // Measure contentInner BEFORE DOM update (watcher fires pre-render)
  const beforeH = modalContentInnerRef?.value?.offsetHeight ?? 0;

  if (visible) {
    await nextTick();
    const h = measureWifiRow();
    console.log(`[NetworkSettings] watcher setting wifiRowHeight=${h}px ${ts()}`);
    wifiRowHeight.value = `${h}px`;
  } else {
    wifiRowHeight.value = '0px';
  }

  // Measure contentInner AFTER DOM update and announce delta to Modal.
  // This updates the viewTransition's locked target so the modal springs
  // to the correct height in a single step (no 2-step jump).
  await nextTick();
  const afterH = modalContentInnerRef?.value?.offsetHeight ?? 0;
  const delta = afterH - beforeH;
  console.log(`[NetworkSettings] contentInner delta: ${beforeH} → ${afterH} (Δ${delta}) ${ts()}`);
  if (requestHeightDelta && Math.abs(delta) > 2) {
    requestHeightDelta(delta);
  }
});

// Enable transitions only after all API data is loaded
watch(loading, (isLoading) => {
  if (!isLoading && skipTransition.value) {
    console.log(`[NetworkSettings] loading done → enabling transitions ${ts()}`);
    rafId = requestAnimationFrame(() => {
      rafId = requestAnimationFrame(() => {
        console.log(`[NetworkSettings] skipTransition → false ${ts()}`);
        skipTransition.value = false;
      });
    });
  }
});

let rafId = null;
const t0 = performance.now();
const ts = () => `+${(performance.now() - t0).toFixed(1)}ms`;

onMounted(() => {
  console.log(`[NetworkSettings] onMounted ${ts()}`);
  // Fire and forget — don't block viewTransition's height measurement.
  // The showWifiCard watcher handles height deltas as data arrives.
  // The loading watcher enables transitions after all data is loaded.
  initialize();
});

onUnmounted(() => {
  if (rafId) cancelAnimationFrame(rafId);
});
</script>

<style scoped>
/* Connection status card (MultiroomItem zone pattern) */
.connection-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05);
  display: flex;
  flex-direction: column;
  transition: padding-bottom var(--transition-fast);
}

.connection-section.no-transition {
  transition: none;
}

.connection-section.is-expanded {
  padding-bottom: 0;
}

/* Connection rows */
.connection-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
}

.connection-card__name {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
}

.connection-badge {
  flex-shrink: 0;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
}

.connection-badge--connected {
  background: color-mix(in srgb, var(--color-success) 16%, transparent);
  color: var(--color-success);
}

.connection-badge--ready {
  background: color-mix(in srgb, var(--color-brand) 16%, transparent);
  color: var(--color-brand);
}

.connection-badge--disconnected {
  background: color-mix(in srgb, var(--color-error) 16%, transparent);
  color: var(--color-error);
}

/* Height wrapper for single-step animation */
.expanded-wrapper {
  height: 0;
  overflow: hidden;
  transition: height var(--transition-normal);
}

.expanded-wrapper.no-transition {
  transition: none;
}

/* Suppress content opacity/visibility transition during initial mount */
.expanded-wrapper.no-transition .expanded-content {
  transition: none;
}

/* Content: border-top + opacity/visibility (like .expanded-clients in MultiroomItem) */
.expanded-content {
  margin-top: var(--space-04);
  padding-top: var(--space-04);
  padding-bottom: var(--space-05);
  border-top: 1px solid var(--color-border);
  opacity: 0;
  visibility: hidden;
  transition: opacity 250ms ease, visibility 0ms linear 250ms;
}

.expanded-content.is-visible {
  opacity: 1;
  visibility: visible;
  transition: opacity 300ms ease, visibility 0ms linear 0ms;
}

/* WiFi content inside ToggleSection */
.wifi-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.wifi-content__description {
  color: var(--color-text-secondary);
  line-height: 1.4;
}

/* Grouped sections (preferred + other networks) */
.wifi-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

/* Network items */
.network-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background);
  cursor: pointer;
  transition: background-color var(--transition-fast), var(--transition-press);
}

.network-item--preferred {
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
  gap: var(--space-03);
  min-width: 0;
}

.network-item__ssid {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.network-item__caret {
  flex-shrink: 0;
  transform: rotate(-90deg);
  transition: transform var(--transition-fast);
}

.network-item__caret--open {
  transform: rotate(-180deg);
}

/* Other networks sub-header */
.network-subheader {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.network-subheader__title {
  color: var(--color-text-secondary);
}

/* Network list */
.network-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
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
  background: var(--color-background);
}

.network-skeleton .skeleton-text-line {
  --shimmer-base: var(--color-background);
  --shimmer-highlight: var(--color-background-medium-16);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .connection-section {
    border-radius: var(--radius-05);
  }
}
</style>
