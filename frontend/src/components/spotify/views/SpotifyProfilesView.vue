<template>
  <div class="profiles-list">
    <SpotifyProfileRow v-for="profile in store.profiles" :key="profile.username" :profile="profile"
      :state="rowState(profile)" :active="profile.username === signedIn.username" :phase="signedIn.phase"
      :disabled="!!switching || !!forgetting"
      @pick="pick(profile)" @arm="arm(profile)" @cancel="armed = null" @forget="forget(profile)" />
  </div>
</template>

<script setup>
import { computed, ref, onUnmounted } from 'vue';
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
// The signed-in profile and its phase as the pick found them, drawn until the
// screen leaves: the state names the new account, and drops the old session,
// before its home has loaded.
const switchedFrom = ref(null);
const signedIn = computed(() => (switching.value
  ? switchedFrom.value
  : { username: store.account, phase: store.phase }));

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
  switchedFrom.value = { username: store.account, phase: store.phase };
  switching.value = profile.username;
  if (!(await store.switchProfile(profile.username))) {
    switching.value = null;
    switchedFrom.value = null;
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
  // Still switching while the screen leaves: cleared now, the rows would draw
  // the account just signed in as for the length of the leave.
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
/* Two columns at most, one when a row would be narrower than 400px: the
   avatar, the trash and the paddings take ~176px, the signed-in badge up to
   ~155px (Italian, German), and the name keeps what is left beside it rather
   than being drawn over. On the kiosk the player's pane alone tips it. */
.profiles-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(max(min(400px, 100%), calc((100% - var(--space-02)) / 2)), 1fr));
  align-items: start;
  gap: var(--space-02);
}

@media (max-aspect-ratio: 4/3) {
  .profiles-list {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
