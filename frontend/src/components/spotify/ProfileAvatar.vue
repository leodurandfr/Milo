<template>
  <div class="profile-avatar" :class="{ blurred }" :style="{ '--avatar-size': `${size}px` }">
    <!-- Until Spotify names the picture; the image's own skeleton takes over
         in place once it does. -->
    <Transition :name="profile.avatar_url ? 'none' : 'reveal'">
      <div v-if="describing" class="avatar-skeleton shimmer" />
    </Transition>
    <Transition name="reveal">
      <span v-if="initialShown" class="avatar-initial heading-2" aria-hidden="true">{{ initial }}</span>
    </Transition>
    <LazyImage v-if="profile.avatar_url" ref="imageRef" :src="profile.avatar_url" :alt="profile.name"
      :skeleton="!waited" class="avatar-image" />
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useProfileDescribing } from '@/composables/useProfileDescribing';
import LazyImage from '@/components/ui/LazyImage.vue';

const props = defineProps({
  // { name, avatar_url }
  profile: {
    type: Object,
    required: true,
  },
  size: {
    type: Number,
    default: 96,
  },
  // Set aside while its account signs in.
  blurred: {
    type: Boolean,
    default: false,
  },
});

const initial = computed(() => (props.profile.name || '?').trim().charAt(0).toUpperCase());

const imageRef = ref(null);
const { describing, waited } = useProfileDescribing(() => props.profile);
// No picture to wait for: none described, one that failed, or the wait over.
const initialShown = computed(() => {
  if (!props.profile.avatar_url) return !describing.value;
  if (imageRef.value?.imageLoaded) return false;
  return !!imageRef.value?.imageError || waited.value;
});
</script>

<style scoped>
.profile-avatar {
  position: relative;
  width: var(--avatar-size);
  height: var(--avatar-size);
  flex-shrink: 0;
  border-radius: var(--radius-full);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-inset);
}

.avatar-image {
  width: 100%;
  height: 100%;
}

/* Under the picture, which comes after them and is positioned too. */
.avatar-skeleton,
.avatar-initial {
  position: absolute;
  inset: 0;
}

.avatar-initial {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
}

/* Inside the circle, which keeps a sharp edge. */
.blurred > * {
  filter: blur(var(--blur-01));
}
</style>
