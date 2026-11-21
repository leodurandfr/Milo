<!-- frontend/src/components/settings/categories/NetworkSettings.vue -->
<template>
  <SectionStack>
    <!-- Connection status card: Ethernet always, the WiFi row unfolding under it -->
    <div class="connection-section" :class="{ 'is-expanded': showWifiCard }">
      <!-- Ethernet row (always visible, like zone header) -->
      <div class="connection-card connection-card--ethernet">
        <div class="connection-card__name">
          <SvgIcon name="network" :size="24" />
          <span class="heading-3">{{ t('network.ethernet') }}</span>
        </div>
        <div class="connection-card__status">
          <span v-if="miloLocalIface === 'ethernet'" class="connection-card__ip text-mono-small">
            {{ status.ethernet.ip_address }}
          </span>
          <Badge :tone="status.ethernet.connected ? 'success' : 'error'">
            {{ status.ethernet.connected ? t('network.connected') : t('network.notConnected') }}
          </Badge>
        </div>
      </div>

      <!-- WiFi row: px height while it animates, then the class's -->
      <div ref="wifiWrapperRef" class="expanded-wrapper" :class="{ 'no-transition': skipTransition }"
        @transitionend.self="onWifiWrapperTransitionEnd"
        @transitioncancel.self="onWifiWrapperTransitionCancel">
        <div ref="wifiRowRef" class="expanded-content" :class="{ 'is-visible': showWifiCard }">
          <div class="connection-card">
            <div class="connection-card__name">
              <WifiSignal :signal="wifiShown.signal" :size="24" />
              <span class="heading-3 connection-card__ssid">{{ wifiShown.ssid }}</span>
            </div>
            <div class="connection-card__status">
              <span v-if="wifiShown.ip" class="connection-card__ip text-mono-small">
                {{ wifiShown.ip }}
              </span>
              <Badge :tone="wifiShown.tone" :pulse="wifiShown.pulse">
                {{ wifiShown.label }}
              </Badge>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- WiFi section with toggle -->
    <ToggleSection
      :title="t('network.wifi')"
      :enabled="status.wifi_enabled"
      @change="toggleWifi"
    >
      <div class="wifi-content">
        <span class="text-body-medium wifi-content__description">{{ t('network.wifiDescription') }}</span>

        <!-- Preferred network -->
        <div v-if="preferredNetwork" class="wifi-group">
          <span class="heading-3">{{ t('network.preferredNetwork') }}</span>
          <div class="network-item network-item--preferred">
            <div class="network-item__row">
              <div class="network-item__ssid-row">
                <WifiSignal :signal="wifiShown.signal" :size="24" />
                <span class="text-body-medium network-item__ssid">{{ preferredNetwork.ssid }}</span>
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
            <Button variant="tinted" size="small" left-icon="arrowClockwise"
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
            <NoticeBox v-if="!loading && !scanning && otherNetworks.length === 0" kind="empty">
              {{ t('network.noNetworks') }}
            </NoticeBox>

            <!-- Network items -->
            <div v-for="network in otherNetworks" :key="'other-' + network.ssid" class="network-item"
              @click="selectNetwork(network)">
              <div class="network-item__row">
                <div class="network-item__ssid-row">
                  <WifiSignal :signal="network.signal" :size="24" />
                  <span class="text-body-medium network-item__ssid">{{ network.ssid }}</span>
                </div>
                <SvgIcon name="caretDown" :size="24" color="var(--color-text-tertiary)"
                  class="network-item__caret" :class="{ 'network-item__caret--open': selectedSsid === network.ssid }" />
              </div>

              <!-- Expand: password + connect, or save over the recovery AP -->
              <div v-if="selectedSsid === network.ssid" class="network-item__expand" @click.stop>
                <InputText v-if="network.security" v-model="password" type="password"
                  :placeholder="t('network.password')" @submit="choose(network)" />
                <Button variant="brand" :loading="connecting" :disabled="connecting || (network.security && !password)"
                  @click="choose(network)">
                  {{ connecting
                    ? (overRecoveryAp ? t('network.saving') : t('network.connecting'))
                    : (overRecoveryAp ? t('network.save') : t('network.connect')) }}
                </Button>
                <NoticeBox v-if="connectError" :detail="connectDetail">{{ connectError }}</NoticeBox>
              </div>
            </div>
          </div>
        </div>

        <!-- WiFi country selector. Applies on change and rescans; no reboot.
             `milo-set-wifi-country` runs `iw reg set` there and then, and only
             writes cmdline.txt for the next boot's initial domain — the
             apply-and-reboot sequence that used to live here was doing on this
             page what the setup wizard has never needed. -->
        <div class="country-section">
          <WifiCountrySelector />
        </div>
      </div>
    </ToggleSection>

    <!-- Outside the section: a refused switch-on leaves it closed. -->
    <NoticeBox v-if="wifiRefusal !== null" :detail="wifiRefusal">{{ t('settings.actionFailed') }}</NoticeBox>
  </SectionStack>
</template>

<script setup>
import { ref, computed, watch, nextTick, onUnmounted, inject } from 'vue';
import { useI18n } from '@/services/i18n';
import { useNetwork, refreshWifiSignal } from '@/composables/useNetwork';
import { useTimer } from '@/composables/useTimer';
import { useHeldWhileClosed } from '@/composables/useHeldWhileClosed';
import { useSettingsStore } from '@/stores/settingsStore';
import { isKiosk } from '@/utils/kiosk';
import { WIFI_SIGNAL_POLL_MS } from '@/constants/network';
import { COLLAPSE_TRANSITION, COLLAPSE_FOLLOW_MS } from '@/constants/collapse';
import SectionStack from '@/components/ui/SectionStack.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import InputText from '@/components/ui/InputText.vue';
import WifiCountrySelector from '@/components/network/WifiCountrySelector.vue';
import Badge from '@/components/ui/Badge.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';

const { t } = useI18n();
const timer = useTimer();

const {
  status,
  loading,
  scanning,
  connecting,
  connectError,
  connectDetail,
  wifiRefusal,
  selectedSsid,
  password,
  preferredNetwork,
  otherNetworks,
  selectNetwork,
  scanNetworks,
  connectToNetwork,
  saveNetwork,
  forgetNetwork,
  toggleWifi,
  initialize,
} = useNetwork();

// A browser on the unit's recovery access point saves the network rather than
// joining it: joining takes the one radio from under that browser, and the
// unit refuses it. The save waits for a gesture on the unit itself, and the
// unit tries the network on its next turn back to the home network. The
// kiosk, a loopback client, joins at once.
const settingsStore = useSettingsStore();
const overRecoveryAp = computed(() => settingsStore.hotspotActive && !isKiosk());

function choose(network) {
  if (overRecoveryAp.value) saveNetwork(network, t);
  else connectToNetwork(network, t);
}

// From setup, not onMounted: the view's first frame is then the skeletons, never
// the last scan swapped for them a tick later.
initialize();

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

const wifiBadgeTone = computed(() => {
  if (status.value.wifi.connected) return 'success';
  if (status.value.wifi.connecting) return 'warning';
  return 'error';
});

const wifiBadgeLabel = computed(() => {
  if (status.value.wifi.connected) return t('network.connected');
  if (status.value.wifi.connecting) return t('network.connecting');
  return t('network.notConnected');
});

// The row whose address milo.local answers on. Avahi publishes on one
// interface only, and 90-milo-network picks it: ethernet whenever it holds an
// IPv4 (which `connected` requires), else wifi. Same rule, read the same way.
const miloLocalIface = computed(() => {
  if (status.value.ethernet.connected && status.value.ethernet.ip_address) return 'ethernet';
  if (status.value.wifi.connected && status.value.wifi.ip_address) return 'wifi';
  return null;
});

// What the WiFi card and the preferred row draw. Off, they close on what they
// showed with the radio on: its reply and the status_changed echo land mid-fade
// with the link down, and would flip the badge and blank the arc.
const wifiShown = useHeldWhileClosed(() => status.value.wifi_enabled, () => ({
  ssid: wifiDisplaySsid.value,
  signal: wifiCardSignal.value,
  ip: miloLocalIface.value === 'wifi' ? status.value.wifi.ip_address : null,
  tone: wifiBadgeTone.value,
  label: wifiBadgeLabel.value,
  pulse: status.value.wifi.connecting && !status.value.wifi.connected,
}));

// === WiFi row expand/collapse — MultiroomItem's mechanism ===
// The row animates its own height on the same curve as the Modal clip, and the
// clip is told the delta through modalSpringHeightDelta, so frame and content
// are equal at every frame. The section's box is the same open or closed (its
// bottom padding lives on the rows), so the delta is the row and nothing else.
// A collapse lands on 0, where a spring's overshoot cannot be drawn on the row
// but would be on the clip: it rides the monotone curve on both sides, the one
// the ToggleSection folding with it rides too (Collapse.vue).
const springHeightDelta = inject('modalSpringHeightDelta', null);
const wifiWrapperRef = ref(null);
const wifiRowRef = ref(null);
const skipTransition = ref(true);

// One path for every change, the toggle's and a status update's alike. Explicit
// px only while animating: settled, the height is the class's (auto or 0), so a
// row that grows later is never cut to a stale value.
watch(showWifiCard, async (open) => {
  const wrapper = wifiWrapperRef.value;
  // Before the data lands (transitions off) or out of sight, the modal's own
  // observer sizes the clip: the row takes its height from the class.
  if (!wrapper || skipTransition.value || !wrapper.checkVisibility()) return;
  // Pinned before the patch flips the class, so a reversal starts from what is
  // on screen and the new direction's curve is the one the move starts on.
  const from = wrapper.offsetHeight;
  wrapper.style.height = `${from}px`;
  await nextTick();
  if (!wifiRowRef.value) return;
  const to = open ? wifiRowRef.value.offsetHeight : 0;
  if (to === from) {
    wrapper.style.height = '';
    return;
  }
  springHeightDelta?.(
    to - from,
    open ? {} : { transition: COLLAPSE_TRANSITION, durationMs: COLLAPSE_FOLLOW_MS }
  );
  wrapper.style.height = `${to}px`;
});

function onWifiWrapperTransitionEnd(event) {
  if (event.propertyName === 'height') wifiWrapperRef.value.style.height = '';
}

// A reversal cancels too, and keeps its new target; only a transition cut by
// the view leaving the screen would otherwise leave the px height for good.
function onWifiWrapperTransitionCancel(event) {
  if (event.propertyName === 'height' && !wifiWrapperRef.value.checkVisibility()) {
    wifiWrapperRef.value.style.height = '';
  }
}

let rafId = null;
let signalPollId = null;

// The signal arc is the only value on this panel that moves on its own, and the
// backend broadcasts nothing for it: it lives exactly as long as the card that
// shows it. Kept out of the height watcher above, whose early returns (no
// transition yet, out of sight) would silently skip a start or a stop.
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

onUnmounted(() => {
  if (rafId) cancelAnimationFrame(rafId);
});
</script>

<style scoped>
/* Connection status card. The bottom padding lives on the rows (the Ethernet
   card and .expanded-content), never on the section: its box must not change
   when the WiFi row unfolds, or the delta announced to the Modal clip would
   need a correction that silently tracks --space-05 (MultiroomItem, same). */
.connection-section {
  background: var(--color-surface);
  border-radius: var(--radius-06);
  padding: var(--space-05) var(--space-05) 0;
  display: flex;
  flex-direction: column;
}

/* Connection rows */
.connection-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
}

.connection-card--ethernet {
  padding-bottom: var(--space-05);
}

.connection-card__name {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
}

.connection-card__status {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  flex-shrink: 0;
}

.connection-card__ip {
  color: var(--color-text-secondary);
}

.connection-card__ssid {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Same curves as the Modal clip (see the showWifiCard watcher): the spring
   opening, the monotone one closing — the collapse lands on 0, where the
   spring's lobe would be clamped away here but drawn on the clip. */
.expanded-wrapper {
  height: 0;
  overflow: hidden;
  transition: height var(--transition-collapse);
}

.connection-section.is-expanded .expanded-wrapper {
  height: auto;
  transition: height var(--transition-spring-light);
}

/* Suppress content opacity/visibility transition during initial mount. The
   wrapper needs no such rule: until the data lands the watcher sets no px
   height, so its class flips auto <-> 0, which never animates. */
.expanded-wrapper.no-transition .expanded-content {
  transition: none;
}

/* Content: border-top + opacity/visibility (like .expanded-clients in MultiroomItem) */
.expanded-content {
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
  background: var(--color-inset);
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

/* Skeleton */
.network-skeleton {
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 48px;
  padding: 0 var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-inset);
}

/* Divider above the country row (the row itself lives in WifiCountrySelector) */
.country-section {
  padding-top: var(--space-05-fixed);
  border-top: 1px solid var(--color-border);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .connection-section {
    border-radius: var(--radius-05);
  }

}
</style>
