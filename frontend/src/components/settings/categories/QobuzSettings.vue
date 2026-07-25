<!-- frontend/src/components/settings/categories/QobuzSettings.vue -->
<!--
  Qobuz account screen (D4). Unlike Spotify Connect (zeroconf, no login),
  qobuz-proxy needs a one-time Qobuz account login before "Milō" advertises in
  the Qobuz app. This screen drives the backend relay (backend/api/qobuz_account.py):
    - GET  /api/qobuz/account            → login status (from the cached token,
                                           so it holds when the sidecar is down)
    - GET  /api/qobuz/account/login-url  → OAuth URL to open in the browser
    - POST /api/qobuz/account/logout     → clear token + stop the speaker
  The OAuth flow itself runs on qobuz-proxy (:8689); Connect opens it in a new
  tab and we refetch status when the user returns (focus + a bounded poll).
-->
<template>
  <!-- Checking status -->
  <MessageContent v-if="loading" loading :title="t('qobuzSettings.loading')" />

  <!-- Not connected: a single message-content card carries its own Connect CTA. -->
  <MessageContent
    v-else-if="!account.authenticated"
    :title="t('qobuzSettings.notConnectedTitle')"
    :details="t('qobuzSettings.notConnectedDetails')"
    :cta-label="t('qobuzSettings.connect')"
    :cta-loading="connecting"
    :cta-click="connect"
  />

  <!-- Connected -->
  <SettingsContainer v-else>
    <SettingsSection>
      <!-- Title + email are one group on the left; Disconnect aligns with it on the right. -->
      <template #header>
        <div class="qobuz-header">
          <div class="qobuz-account-text">
            <h2 class="heading-2">{{ t('qobuzSettings.accountTitle') }}</h2>
            <p v-if="account.email" class="text-mono qobuz-email">{{ account.email }}</p>
          </div>
          <Button
            variant="background-strong"
            size="medium"
            :loading="disconnecting"
            @click="disconnect"
          >
            {{ t('qobuzSettings.disconnect') }}
          </Button>
        </div>
      </template>
    </SettingsSection>

    <!-- Allow the Qobuz mobile app to control volume. Off (default) keeps
         qobuz-proxy at unity so CamillaDSP is the only volume authority. -->
    <ToggleSection
      :title="t('qobuzSettings.appVolumeTitle')"
      :description="t('qobuzSettings.appVolumeDescription')"
      :enabled="allowAppVolume"
      @change="handleAppVolumeToggle"
    />
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import { useTimer } from '@/composables/useTimer';
import { useSettingsStore } from '@/stores/settingsStore';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const timer = useTimer();
const settingsStore = useSettingsStore();
const { updateSetting } = useSettingsAPI();

// Whether the Qobuz app may change the volume (else qobuz-proxy stays at unity
// and CamillaDSP owns the volume). Mirrors settingsStore, fed by WS + bulk load.
const allowAppVolume = computed(() => settingsStore.qobuzSettings.allow_app_volume);

async function handleAppVolumeToggle(enabled) {
  settingsStore.updateQobuzSettings({ allow_app_volume: enabled });
  await updateSetting('qobuz-settings', { allow_app_volume: enabled });
}

const account = ref({ authenticated: false, email: null });
const loading = ref(true);
const connecting = ref(false);
const disconnecting = ref(false);

// Bounded status poll after opening the OAuth tab: the callback lands on
// qobuz-proxy, not here, so we can't observe completion directly — poll until
// authenticated or the window elapses.
const POLL_MS = 3000;
const POLL_MAX_MS = 120000;
let pollHandle = null;
let pollDeadline = 0;

async function fetchStatus() {
  const result = await apiCall.get('/api/qobuz/account', {
    category: 'settings',
    message: 'Error loading Qobuz account status',
  });
  if (result.ok && result.data?.data) {
    account.value = {
      authenticated: !!result.data.data.authenticated,
      email: result.data.data.email ?? null,
    };
  }
  loading.value = false;
  return account.value.authenticated;
}

function stopPoll() {
  if (pollHandle) {
    timer.clear(pollHandle);
    pollHandle = null;
  }
  connecting.value = false;
}

function startPoll() {
  pollDeadline = Date.now() + POLL_MAX_MS;
  if (pollHandle) timer.clear(pollHandle);
  pollHandle = timer.setInterval(async () => {
    if (Date.now() > pollDeadline) {
      stopPoll();
      return;
    }
    if (await fetchStatus()) stopPoll();
  }, POLL_MS);
}

async function connect() {
  if (connecting.value) return;
  connecting.value = true;
  const result = await apiCall.get('/api/qobuz/account/login-url', {
    category: 'settings',
    message: 'Error building Qobuz login URL',
  });
  const loginUrl = result.ok ? result.data?.data?.login_url : null;
  if (!loginUrl) {
    connecting.value = false;
    return;
  }
  // Open the qobuz-proxy OAuth page; the user signs in there and returns.
  window.open(loginUrl, '_blank', 'noopener');
  startPoll();
}

async function disconnect() {
  if (disconnecting.value) return;
  disconnecting.value = true;
  await apiCall.post('/api/qobuz/account/logout', null, {
    category: 'settings',
    message: 'Error disconnecting Qobuz account',
  });
  await fetchStatus();
  disconnecting.value = false;
}

// Refetch when the tab regains focus (e.g. returning from the OAuth tab).
function onFocus() {
  if (!disconnecting.value) fetchStatus();
}

onMounted(() => {
  fetchStatus();
  window.addEventListener('focus', onFocus);
});

onUnmounted(() => {
  window.removeEventListener('focus', onFocus);
  // timer.clearAll() runs automatically on unmount.
});
</script>

<style scoped>
.qobuz-header {
  display: flex;
  align-items: center;
  gap: var(--space-04);
}

.qobuz-account-text {
  margin-right: auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.qobuz-email {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
