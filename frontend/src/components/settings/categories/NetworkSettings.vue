<!-- frontend/src/components/settings/categories/NetworkSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Connection status card (MultiroomItem zone pattern) -->
    <div class="connection-section" :class="{ 'is-expanded': showWifiCard, 'no-transition': skipTransition }">
      <!-- Ethernet row (always visible, like zone header) -->
      <div class="connection-card">
        <div class="connection-card__name">
          <SvgIcon name="network" :size="24" />
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
              <WifiSignal :signal="wifiCardSignal" :size="24" />
              <span class="heading-3 connection-card__ssid">{{ wifiDisplaySsid }}</span>
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
                <WifiSignal :signal="preferredNetwork.signal" :size="24" />
                <span class="text-body network-item__ssid">{{ preferredNetwork.ssid }}</span>
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
            <Button variant="background-strong" size="small" left-icon="arrowClockwise"
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
                  <WifiSignal :signal="network.signal" :size="24" />
                  <span class="text-body network-item__ssid">{{ network.ssid }}</span>
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

        <!-- WiFi country selector -->
        <div class="country-row">
          <span class="country-row__label text-mono">{{ t('network.wifiCountry') }}</span>
          <Dropdown
            :model-value="pendingCountry || country"
            :options="countryOptions"
            :placeholder="t('network.selectCountry')"
            :disabled="isRebootingCountry"
            @change="onCountryChange"
          />
        </div>

        <!-- Apply & Reboot (sticky, two-step confirm) -->
        <Button v-if="isCountryDirty || isRebootingCountry"
          :variant="confirmRebootCountry ? 'important' : 'brand'"
          class="apply-button-sticky"
          :loading="isApplyingCountry || isRebootingCountry"
          :disabled="isApplyingCountry || isRebootingCountry"
          @click="handleCountryApply">
          {{ countryApplyButtonLabel }}
        </Button>
      </div>
    </ToggleSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted, inject } from 'vue';
import { useI18n } from '@/services/i18n';
import { useNetwork, refreshWifiSignal } from '@/composables/useNetwork';
import { useTimer } from '@/composables/useTimer';
import { wifiCountryOptions } from '@/constants/wifiCountries';
import { WIFI_SIGNAL_POLL_MS } from '@/constants/network';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';
import { apiCall } from '@/services/apiCall';
import { logger } from '@/services/logger';

const { t, getCurrentLanguage } = useI18n();
const timer = useTimer();
const requestHeightDelta = inject('modalRequestHeightDelta', null);
const modalSetContentHeight = inject('modalSetContentHeight', null);
const modalContentInnerRef = inject('modalContentInnerRef', null);

const {
  status,
  country,
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
  setCountry,
  initialize,
} = useNetwork();

// === WiFi country selector (Apply & Reboot pattern from HardwareSettings) ===
const countryOptions = computed(() => wifiCountryOptions(getCurrentLanguage()));
const pendingCountry = ref('');
const confirmRebootCountry = ref(false);
const isApplyingCountry = ref(false);
const isRebootingCountry = ref(false);

const isCountryDirty = computed(() =>
  pendingCountry.value && pendingCountry.value !== country.value
);

const countryApplyButtonLabel = computed(() => {
  if (isRebootingCountry.value) return t('hardwareSettings.rebooting');
  if (confirmRebootCountry.value) return t('hardwareSettings.confirmReboot');
  return t('hardwareSettings.applyAndReboot');
});

// Sync pendingCountry when country loads from API
watch(country, (val) => {
  if (!isCountryDirty.value) pendingCountry.value = val;
}, { immediate: true });

function onCountryChange(code) {
  pendingCountry.value = code;
  confirmRebootCountry.value = false;
}

function handleCountryApply() {
  if (!confirmRebootCountry.value) {
    confirmRebootCountry.value = true;
    return;
  }
  applyCountryAndReboot();
}

async function applyCountryAndReboot() {
  isApplyingCountry.value = true;
  confirmRebootCountry.value = false;

  try {
    await setCountry(pendingCountry.value);
  } catch (err) {
    logger.error('network', 'Failed to apply WiFi country', err);
    isApplyingCountry.value = false;
    return;
  }
  isApplyingCountry.value = false;
  isRebootingCountry.value = true;

  const restartResult = await apiCall.post('/api/system/restart', null, {
    category: 'network',
    message: 'Failed to trigger reboot'
  });
  if (!restartResult.ok) {
    isRebootingCountry.value = false;
    return;
  }

  // Poll for backend to come back after reboot — expected stream of failures
  // while it restarts, so log at debug level.
  let pollCount = 0;
  const maxPolls = 60;
  countryPollIntervalId = timer.setInterval(async () => {
    pollCount++;
    if (pollCount > maxPolls) {
      timer.clear(countryPollIntervalId);
      countryPollIntervalId = null;
      isRebootingCountry.value = false;
      return;
    }
    const pingResult = await apiCall.get('/api/ping', {
      category: 'network',
      message: 'Reboot polling ping failed',
      timeout: 2000,
      logLevel: 'debug'
    });
    if (pingResult.ok) {
      timer.clear(countryPollIntervalId);
      countryPollIntervalId = null;
      window.location.reload();
    }
  }, 3000);
}

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

const wifiBadgeClass = computed(() =>
  status.value.wifi.connected ? 'connection-badge--connected' : 'connection-badge--disconnected'
);

const wifiBadgeLabel = computed(() =>
  status.value.wifi.connected ? t('network.connected') : t('network.notConnected')
);

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

// Announce wifi card height delta to Modal (MultiroomItem toggleExpand pattern).
// Synchronous height set so the animation starts immediately.
function handleWifiToggle(enabled) {
  const wasVisible = showWifiCard.value;
  const prevHeight = wasVisible ? (parseFloat(wifiRowHeight.value) || 0) : 0;
  const oldToggleContentH = wifiContentRef.value?.offsetHeight || 0;

  toggleWifi(enabled);

  if (!wasVisible && showWifiCard.value) {
    // OPENING: synchronous height + delta (like MultiroomItem expand)
    skipNextWatcher = true;
    const fullHeight = measureWifiRow();
    if (fullHeight > 0) {
      const paddingOffset = getPaddingOffset();
      if (requestHeightDelta) requestHeightDelta(fullHeight - paddingOffset);
      wifiRowHeight.value = `${fullHeight}px`;
    }
    // Correct for toggle content changes after DOM update (e.g. preferred network appearing)
    correctToggleContentDelta(oldToggleContentH);
  } else if (wasVisible && !showWifiCard.value && prevHeight > 0) {
    // CLOSING: synchronous height + delta (like MultiroomItem collapse)
    skipNextWatcher = true;
    const paddingOffset = getPaddingOffset();
    if (requestHeightDelta) requestHeightDelta(-(prevHeight - paddingOffset));
    wifiRowHeight.value = '0px';
  }
}

async function correctToggleContentDelta(oldH) {
  await nextTick();
  const newH = wifiContentRef.value?.offsetHeight || 0;
  // DOM is already in the post-change state here, so set the absolute measured
  // height (a delta would double-count the now-live content).
  if (modalSetContentHeight && Math.abs(newH - oldH) > 2) {
    modalSetContentHeight();
  }
}

watch(showWifiCard, async (visible) => {
  if (skipNextWatcher) {
    skipNextWatcher = false;
    return;
  }

  // Measure contentInner BEFORE DOM update (watcher fires pre-render)
  const beforeH = modalContentInnerRef?.value?.offsetHeight ?? 0;

  if (visible) {
    await nextTick();
    wifiRowHeight.value = `${measureWifiRow()}px`;
  } else {
    wifiRowHeight.value = '0px';
  }

  // Measure contentInner AFTER DOM update. DOM is in the post-change state, so set
  // the absolute measured height (a delta would double-count the now-live content).
  await nextTick();
  const afterH = modalContentInnerRef?.value?.offsetHeight ?? 0;
  if (modalSetContentHeight && Math.abs(afterH - beforeH) > 2) {
    modalSetContentHeight();
  }
});

let rafId = null;
let countryPollIntervalId = null;
let signalPollId = null;

// The signal arc is the only value on this panel that moves on its own, and the
// backend broadcasts nothing for it: it lives exactly as long as the card that
// shows it. Kept out of the height-measuring watcher above, whose early return
// on `skipNextWatcher` would silently skip a start or a stop.
watch(showWifiCard, (visible) => {
  if (visible && signalPollId === null) {
    refreshWifiSignal();
    signalPollId = timer.setInterval(refreshWifiSignal, WIFI_SIGNAL_POLL_MS);
  } else if (!visible && signalPollId !== null) {
    timer.clear(signalPollId);
    signalPollId = null;
  }
}, { immediate: true });

// Enable transitions only after all API data is loaded
watch(loading, (isLoading) => {
  if (!isLoading && skipTransition.value) {
    rafId = requestAnimationFrame(() => {
      rafId = requestAnimationFrame(() => {
        skipTransition.value = false;
      });
    });
  }
}, { immediate: true });

onMounted(async () => {
  // If status was pre-loaded, wifi card may already be visible —
  // set initial wrapper height before viewTransition measures entering height
  if (showWifiCard.value) {
    await nextTick();
    const h = measureWifiRow();
    if (h > 0) {
      wifiRowHeight.value = `${h}px`;
      skipNextWatcher = true;
    }
  }

  initialize();
});

onUnmounted(() => {
  if (rafId) cancelAnimationFrame(rafId);
  // countryPollIntervalId is auto-cleared by useTimer on unmount.
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

.connection-card__ssid {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  margin-top: var(--space-03);
  padding-top: var(--space-03);
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
  padding: var(--space-03);
  border-radius: var(--radius-04);
  background: var(--color-background);
  cursor: pointer;
  transition: background-color var(--transition-fast), var(--transition-press);
  min-width: 0;
}

.network-item--preferred {
  cursor: default;
    padding: var(--space-03) 6px var(--space-03) var(--space-03);

}

.network-item--preferred button {
  margin-block: calc(-1 * var(--space-03));
}

.network-item__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
}

.network-item__ssid-row {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
  flex: 1;
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
  height: 48px;
  padding: 0 var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background);
}

.network-skeleton .skeleton-text-line {
  --shimmer-base: var(--color-background);
  --shimmer-highlight: var(--color-background-medium-16);
}

/* Country selector row (hardware-row pattern) */
.country-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-03);
  padding-top: var(--space-05-fixed);
  border-top: 1px solid var(--color-border);
}

.country-row__label {
  color: var(--color-text-secondary);
  width: 33%;
  flex-shrink: 0;
}

.country-row :deep(.dropdown) {
  flex: 1;
}

/* Sticky apply button (matches HardwareSettings pattern) */
.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .connection-section {
    border-radius: var(--radius-05);
  }

  .country-row {
    flex-direction: column;
    align-items: stretch;
  }

  .country-row__label {
    width: auto;
  }
}
</style>
