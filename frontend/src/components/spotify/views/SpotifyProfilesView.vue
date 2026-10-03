<template>
  <div class="spotify-profiles">
    <header class="profiles-header">
      <h2 class="heading-2">{{ t('spotify.whoIsListening') }}</h2>
    </header>

    <div class="profiles-grid">
      <div v-for="profile in store.profiles" :key="profile.username" class="profile-tile">
        <button v-press type="button" class="profile-pick" :disabled="!!switching"
          @click="pick(profile)">
          <div class="avatar-frame" :class="{ active: profile.active }">
            <ProfileAvatar :profile="profile" :size="96" />
            <div v-if="switching === profile.username" class="avatar-loading">
              <LoadingSpinner :size="32" />
            </div>
          </div>
          <span class="profile-name heading-4">{{ profile.name }}</span>
          <span class="profile-state text-mono-small" :class="{ warn: profile.stale || pending === profile.username }">
            {{ stateLine(profile) }}
          </span>
        </button>
        <IconButton icon="threeDots" variant="ghost" size="small" class="profile-menu"
          :aria-label="t('spotify.manageProfile')" @click="manage(profile)" />
      </div>
    </div>

    <Modal :is-open="!!managed" @close="closeManage">
      <div v-if="managed" class="manage-profile">
        <NavigationHeader :title="managed.name" />
        <SettingsSection>
          <div class="manage-field">
            <InputText v-model="newName" :placeholder="managed.spotify_name || managed.username"
              :maxlength="64" @submit="rename" />
            <Button variant="brand" :disabled="busy" @click="rename">{{ t('spotify.save') }}</Button>
          </div>
        </SettingsSection>
        <SettingsSection>
          <div class="manage-forget">
            <p class="text-body forget-hint">{{ t('spotify.forgetHint') }}</p>
            <Button variant="important" :disabled="busy" @click="forget">
              {{ confirmForget ? t('spotify.forgetConfirm') : t('spotify.forget') }}
            </Button>
          </div>
        </SettingsSection>
      </div>
    </Modal>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useTimer } from '@/composables/useTimer';
import IconButton from '@/components/ui/IconButton.vue';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import Modal from '@/components/ui/Modal.vue';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ProfileAvatar from '../ProfileAvatar.vue';

const emit = defineEmits(['picked']);

const { t } = useI18n();
const store = useSpotifyStore();
const timer = useTimer();

// A switch restarts the daemon: about a second, measured. Past this the tile
// gives up waiting and the profile screen stays.
const SWITCH_WAIT_MS = 15000;

const switching = ref(null);
let switchTimeout = null;
// Two-tap confirm when picking another account would end what plays.
const pending = ref(null);

function stateLine(profile) {
  if (profile.stale) return t('spotify.castAgain');
  if (pending.value === profile.username) return t('spotify.switchStopsPlayback');
  if (profile.active) return t('spotify.connected');
  return '';
}

async function pick(profile) {
  if (profile.active) {
    emit('picked');
    return;
  }
  if (store.session && pending.value !== profile.username) {
    pending.value = profile.username;
    return;
  }
  pending.value = null;
  switching.value = profile.username;
  timer.clear(switchTimeout);
  switchTimeout = timer.setTimeout(() => { switching.value = null; }, SWITCH_WAIT_MS);
  if (!(await store.switchProfile(profile.username))) switching.value = null;
}

// The pick lands when the daemon answers as that account — not when the
// account is first announced, which happens before the daemon restarts.
watch(() => store.account === switching.value && !store.signingIn, (landed) => {
  if (switching.value && landed) {
    switching.value = null;
    timer.clear(switchTimeout);
    emit('picked');
  }
});

// === Rename / forget ===
const managed = ref(null);
const newName = ref('');
const confirmForget = ref(false);
const busy = ref(false);

function manage(profile) {
  managed.value = profile;
  // Only a name the user gave: Spotify's own stays live, never pinned.
  newName.value = profile.name === (profile.spotify_name || profile.username) ? '' : profile.name;
  confirmForget.value = false;
}

function closeManage() {
  managed.value = null;
}

async function rename() {
  if (!managed.value || busy.value) return;
  busy.value = true;
  await store.renameProfile(managed.value.username, newName.value.trim() || null);
  busy.value = false;
  closeManage();
}

async function forget() {
  if (!managed.value || busy.value) return;
  if (!confirmForget.value) {
    confirmForget.value = true;
    return;
  }
  busy.value = true;
  await store.forgetProfile(managed.value.username);
  busy.value = false;
  closeManage();
}

store.loadProfiles();
</script>

<style scoped>
.spotify-profiles {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

.profiles-header h2 {
  margin: 0;
  color: var(--color-text);
}

.profiles-grid {
  display: grid;
  grid-template-columns: repeat(var(--card-grid-columns), minmax(0, 1fr));
  gap: var(--space-05);
}

.profile-tile {
  position: relative;
  min-width: 0;
}

.profile-pick {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-03) 0;
  border: none;
  background: transparent;
  cursor: pointer;
  min-width: 0;
}

.avatar-frame {
  position: relative;
  border-radius: var(--radius-full);
  padding: var(--space-01);
}

.avatar-frame.active {
  box-shadow: 0 0 0 2px var(--color-brand);
}

.avatar-loading {
  position: absolute;
  inset: var(--space-01);
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-contrast-32);
}

.profile-name {
  max-width: 100%;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.profile-state {
  min-height: 1lh;
  color: var(--color-text-secondary);
  text-align: center;
}

.profile-state.warn {
  color: var(--color-brand);
}

.profile-menu {
  position: absolute;
  top: 0;
  right: 0;
}

.manage-profile {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.manage-field {
  display: flex;
  gap: var(--space-03);
  align-items: center;
}

.manage-field > :first-child {
  flex: 1;
}

.manage-forget {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  align-items: flex-start;
}

.forget-hint {
  margin: 0;
  color: var(--color-text-secondary);
}
</style>
