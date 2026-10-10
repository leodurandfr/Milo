<template>
  <div class="profiles-list">
    <SpotifyProfileRow v-for="profile in store.profiles" :key="profile.username" :profile="profile"
      :state="rowState(profile)" :phase="store.phase" :disabled="!!switching || !!forgetting"
      @pick="pick(profile)" @arm="arm(profile)" @cancel="armed = null" @forget="forget(profile)" />
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useTimer } from '@/composables/useTimer';
import SpotifyProfileRow from '../SpotifyProfileRow.vue';

const emit = defineEmits(['picked']);

const store = useSpotifyStore();
const timer = useTimer();

// A switch restarts the daemon: the new account's library answers about a
// second later, measured. Past this the browser opens on whatever the home
// answers (its error has a retry).
const SWITCH_WAIT_MS = 15000;
// While the daemon restarts, the library answers 409: asked again this often.
const HOME_RETRY_MS = 300;

const switching = ref(null);

function rowState(profile) {
  if (switching.value === profile.username) return 'switching';
  if (forgetting.value === profile.username) return 'forgetting';
  if (armed.value === profile.username) return 'armed';
  return 'default';
}

async function pick(profile) {
  armed.value = null;
  if (profile.active) {
    emit('picked');
    return;
  }
  switching.value = profile.username;
  if (!(await store.switchProfile(profile.username))) {
    switching.value = null;
    return;
  }
  // The pick lands with the new account's home loaded, so the browser opens on
  // it whole: the daemon answers the library a second before the state stops
  // saying it signs in (measured), and waiting for that, then loading, showed
  // a sign-in screen in between. The state naming the account can arrive after
  // this answer, and clears the home it finds: a home counts once it has.
  const deadline = Date.now() + SWITCH_WAIT_MS;
  while (Date.now() < deadline) {
    await store.loadHome({ force: true });
    if (switching.value !== profile.username) return;
    if (store.account === profile.username && store.home) break;
    // Refused and forgotten: the profile signed in last took its place.
    if (!store.profiles.some((p) => p.username === profile.username)) break;
    await new Promise((resolve) => timer.setTimeout(resolve, HOME_RETRY_MS));
  }
  if (switching.value !== profile.username) return;
  switching.value = null;
  emit('picked');
}

// Leaving the screen ends the wait: no home is asked for behind it.
onUnmounted(() => { switching.value = null; });

// === Forget ===
// The trash asks for a confirmation in the row, Cancel takes it back:
// forgetting drops the stored credentials.
const armed = ref(null);
const forgetting = ref(null);

function arm(profile) {
  armed.value = profile.username;
}

async function forget(profile) {
  if (forgetting.value) return;
  armed.value = null;
  forgetting.value = profile.username;
  await store.forgetProfile(profile.username);
  forgetting.value = null;
}
</script>

<style scoped>
.profiles-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: var(--space-02);
}

@media (max-aspect-ratio: 4/3) {
  .profiles-list {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
