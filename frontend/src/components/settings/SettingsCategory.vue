<!-- frontend/src/components/settings/SettingsCategory.vue -->
<template>
  <button class="settings-category" @click="$emit('click')">
    <img :src="iconPath" :alt="title" width="32" height="32" class="category-icon" />
    <span class="category-title heading-2">{{ title }}</span>
    <Icon name="caretRight" :size="24" color="var(--color-text-light)" />
  </button>
</template>

<script setup>
import { computed } from 'vue';
import Icon from '@/components/ui/Icon.vue';

// Import des icônes settings
import languagesIcon from '@/assets/settings-icons/languages.svg';
import applicationsIcon from '@/assets/settings-icons/applications.svg';
import volumeIcon from '@/assets/settings-icons/volume.svg';
import displayIcon from '@/assets/settings-icons/display.svg';
import spotifyIcon from '@/assets/settings-icons/spotify.svg';
import multiroomIcon from '@/assets/settings-icons/multiroom.svg';
import updatesIcon from '@/assets/settings-icons/updates.svg';
import informationIcon from '@/assets/settings-icons/information.svg';

const icons = {
  languages: languagesIcon,
  applications: applicationsIcon,
  volume: volumeIcon,
  display: displayIcon,
  spotify: spotifyIcon,
  multiroom: multiroomIcon,
  updates: updatesIcon,
  information: informationIcon
};

const props = defineProps({
  icon: {
    type: String,
    required: true
  },
  title: {
    type: String,
    required: true
  }
});

defineEmits(['click']);

const iconPath = computed(() => {
  return icons[props.icon] || '';
});
</script>

<style scoped>
.settings-category {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.settings-category:hover {
  background: var(--color-background-strong);
  border-color: var(--color-background-glass);
}

.category-icon {
  flex-shrink: 0;
}

.category-title {
  flex: 1;
  text-align: left;
  color: var(--color-text);
}
</style>