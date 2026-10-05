<template>
  <div class="spotify-profiles swap-fades-leaves">
    <div class="profiles-grid" :style="{ '--profile-avatar': `${AVATAR_SIZE}px` }">
      <div v-for="profile in store.profiles" :key="profile.username" class="profile-tile">
        <button v-press type="button" class="profile-pick" :disabled="!!switching || !!forgetting"
          @click="pick(profile)">
          <div class="avatar-frame" :class="{ active: profile.active }">
            <ProfileAvatar :profile="profile" :size="AVATAR_SIZE" />
            <div v-if="switching === profile.username" class="avatar-loading">
              <LoadingSpinner :size="32" />
            </div>
          </div>
          <span class="profile-name heading-2">{{ profile.name }}</span>
          <span class="profile-state text-mono-small" :class="{ warn: profile.stale || pending === profile.username }">
            {{ stateLine(profile) }}
          </span>
        </button>
        <div class="forget-anchor">
          <IconButton icon="close" variant="glass" size="small" :disabled="!!switching || !!forgetting"
            :aria-label="t('spotify.forgetProfile')" @click="toggleArm(profile)" />
        </div>
        <Transition name="forget-confirm">
          <div v-if="armed === profile.username || forgetting === profile.username" class="forget-confirm">
            <Button variant="important" size="small"
              :loading="forgetting === profile.username" @click="forget(profile)">
              {{ t('spotify.confirmForget') }}
            </Button>
          </div>
        </Transition>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useTimer } from '@/composables/useTimer';
import IconButton from '@/components/ui/IconButton.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import Button from '@/components/ui/Button.vue';
import ProfileAvatar from '../ProfileAvatar.vue';

const emit = defineEmits(['picked']);

const { t } = useI18n();
const store = useSpotifyStore();
const timer = useTimer();

const AVATAR_SIZE = 120;

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
  armed.value = null;
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

// === Forget ===
// The X shows a confirm button under the profile, and takes it back on a
// second press: forgetting drops the stored credentials.
const armed = ref(null);
const forgetting = ref(null);

function toggleArm(profile) {
  pending.value = null;
  armed.value = armed.value === profile.username ? null : profile.username;
}

async function forget(profile) {
  if (forgetting.value) return;
  armed.value = null;
  forgetting.value = profile.username;
  await store.forgetProfile(profile.username);
  forgetting.value = null;
}

store.loadProfiles();
</script>

<style scoped>
.spotify-profiles {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

.profiles-grid {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-05);
}

/* A grid cell's width, so a short row centers instead of stretching — never
   narrower than the avatar's frame, which a phone's three columns are: the
   row then holds fewer tiles. */
.profile-tile {
  position: relative;
  min-width: 0;
  width: max(
    calc((100% - (var(--card-grid-columns) - 1) * var(--space-05)) / var(--card-grid-columns)),
    calc(var(--profile-avatar) + 2 * var(--space-01))
  );
}

/* The leaves the view swap fades (.swap-fades-leaves): the plate fades
   itself, so its blur keeps the page behind it. */
.profile-pick,
.forget-anchor > *,
.forget-confirm {
  opacity: var(--swap-fade);
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
  background: var(--color-image-veil);
}

/* Set apart from the avatar, so the name and the state line read as one. */
.profile-name {
  margin-top: var(--space-02);
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
  color: var(--color-warning);
}

/* Under the profile's text, centered in the tile. Comes down into place and
   leaves back up. */
.forget-confirm {
  display: flex;
  justify-content: center;
}

.forget-confirm-enter-active {
  transition: transform var(--transition-medium), opacity var(--transition-medium);
}

.forget-confirm-leave-active {
  transition: transform var(--transition-fast-leave), opacity var(--transition-fast-leave);
}

.forget-confirm-enter-from,
.forget-confirm-leave-to {
  opacity: 0;
  transform: translateY(calc(-1 * var(--space-03)));
}

/* A zero-size point on the avatar frame's rim, upper right at 45° (0.7071):
   the plate centers on it and overlaps the picture, above it. Centered by
   flex, not a transform, which the press scale would overwrite. */
.forget-anchor {
  --rim: calc(var(--profile-avatar) / 2 + var(--space-01));
  position: absolute;
  z-index: 1;
  top: calc(var(--space-03) + var(--rim) * (1 - 0.7071));
  left: calc(50% + var(--rim) * 0.7071);
  width: 0;
  height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
