<template>
  <div class="profile-row" :class="[`profile-row--${state}`, { 'profile-row--active': profile.active }]">
    <button v-press type="button" class="profile-pick" :disabled="disabled || confirming" @click="emit('pick')">
      <div class="avatar-frame">
        <ProfileAvatar :profile="profile" :size="AVATAR_SIZE" :blurred="state === 'switching'" />
        <div v-if="state === 'switching'" class="avatar-loading">
          <LoadingSpinner :size="24" />
        </div>
      </div>
      <Transition name="reveal" mode="out-in">
        <div v-if="textSkeleton" class="profile-text profile-text--skeleton" aria-hidden="true">
          <span class="skeleton-text-line shimmer name-skeleton"></span>
          <span class="skeleton-text-line shimmer state-skeleton"></span>
        </div>
        <div v-else class="profile-text">
          <div class="title-line">
            <span class="profile-name heading-4">{{ profile.name }}</span>
            <Badge v-if="profile.active" tone="success">{{ t('spotify.connected') }}</Badge>
            <Badge v-else-if="state === 'switching'" tone="brand" pulse>{{ t('spotify.connecting') }}</Badge>
          </div>
          <span v-if="message" class="profile-message text-mono-small">{{ message }}</span>
        </div>
      </Transition>
    </button>
    <div v-if="!textSkeleton" class="profile-actions">
      <template v-if="confirming">
        <Button size="small" :disabled="state === 'forgetting'" @click="emit('cancel')">
          {{ t('spotify.cancel') }}
        </Button>
        <Button variant="important" size="small" :loading="state === 'forgetting'" @click="emit('forget')">
          {{ t('spotify.forget') }}
        </Button>
      </template>
      <IconButton v-else icon="trash" variant="tinted" size="small" :disabled="disabled"
        :aria-label="t('spotify.forgetProfile')" @click="emit('arm')" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useProfileDescribing } from '@/composables/useProfileDescribing';
import Badge from '@/components/ui/Badge.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import ProfileAvatar from './ProfileAvatar.vue';

const props = defineProps({
  // { username, name, spotify_name, avatar_url }
  profile: {
    type: Object,
    required: true,
  },
  // What the profiles screen is doing with this one: `pending` asks for a
  // second tap (the switch would stop what plays), `armed` for the forget's
  // confirmation, `forgetting` while it is done.
  state: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'switching', 'pending', 'armed', 'forgetting'].includes(value),
  },
  // Another profile is switching or being forgotten.
  disabled: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['pick', 'arm', 'cancel', 'forget']);

const { t } = useI18n();

const AVATAR_SIZE = 64;

const { describing } = useProfileDescribing(() => props.profile);

const confirming = computed(() => props.state === 'armed' || props.state === 'forgetting');

// Only a row at rest waits for its name: one that asks something (a second
// tap, a confirmation) or signs in draws what it asks over the skeleton.
const textSkeleton = computed(() => describing.value && props.state === 'default');

const message = computed(() => {
  if (props.state === 'pending') return t('spotify.switchStopsPlayback');
  if (confirming.value) return t('spotify.forgetQuestion');
  return '';
});
</script>

<style scoped>
/* The confirmation sits in the actions on a wide screen, and on its own line
   under the profile on a phone: one wrap, the same DOM. */
.profile-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  column-gap: var(--space-04);
  row-gap: var(--space-03);
  min-width: 0;
  min-height: 72px;
  padding: var(--space-02) var(--space-05) var(--space-02) var(--space-02);
  border-radius: var(--radius-06);
  background: var(--color-tile);
}

.profile-pick {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-04);
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.avatar-frame {
  position: relative;
  flex-shrink: 0;
  padding: var(--space-01);
  border-radius: var(--radius-full);
}

/* The picture, blurred, fades toward the page under the spinner while its
   account signs in: lighter in light, darker under a white spinner in dark. */
.avatar-loading {
  position: absolute;
  inset: var(--space-01);
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-surface-glass);
  color: var(--color-text);
}

.profile-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.profile-text--skeleton {
  gap: var(--space-02);
}

.name-skeleton {
  width: 88px;
}

.state-skeleton {
  width: 56px;
  height: 10px;
}

.title-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-01);
  min-width: 0;
}

.profile-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-secondary);
}

.profile-row--active .profile-name {
  color: var(--color-text);
}

.profile-message {
  color: var(--color-text-secondary);
}

.profile-row--pending .profile-message {
  color: var(--color-warning);
}

.profile-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

@media (max-aspect-ratio: 4/3) {
  .profile-row {
    padding-right: var(--space-04);
    column-gap: var(--space-03);
  }

  .profile-pick {
    gap: var(--space-03);
  }

  .profile-row--armed .profile-actions,
  .profile-row--forgetting .profile-actions {
    flex-basis: 100%;
  }

  .profile-row--armed .profile-actions > *,
  .profile-row--forgetting .profile-actions > * {
    flex: 1;
  }
}
</style>
