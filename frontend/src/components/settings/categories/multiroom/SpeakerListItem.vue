<!-- Shared speaker list item for multiroom settings -->
<template>
  <ListItemButton variant="background" icon-variant="standard" :action="action">
    <template #icon>
      <div class="speaker-icon" :class="{ 'is-offline': !online }">
        <SvgIcon :name="speakerIcon" :size="28" />
      </div>
    </template>
    <template #title>
      <div class="speaker-title">
        <span :class="{ 'text-secondary': !online }">{{ name }}</span>
        <span v-if="!online" class="text-mono-small speaker-title__status">
          {{ t('multiroom.offline') }}
        </span>
        <span v-else class="text-mono-small speaker-title__type">
          {{ speakerTypeLabel }}
        </span>
      </div>
    </template>
  </ListItemButton>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useEqualizerStore } from '@/stores/equalizerStore';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  name: {
    type: String,
    required: true
  },
  macId: {
    type: String,
    required: true
  },
  online: {
    type: Boolean,
    default: true
  },
  action: {
    type: String,
    default: 'caret'
  }
});

const { t } = useI18n();
const equalizerStore = useEqualizerStore();

const speakerIcon = computed(() => {
  const speakerType = equalizerStore.getClientSpeakerType(props.macId);
  const iconMap = {
    satellite: 'speakerSatellite',
    bookshelf: 'speakerShelf',
    tower: 'speakerColumn',
    subwoofer: 'speakerSub'
  };
  return iconMap[speakerType] || 'speakerShelf';
});

const speakerTypeLabel = computed(() => {
  const speakerType = equalizerStore.getClientSpeakerType(props.macId);
  return t(`multiroom.speakerTypes.${speakerType}`);
});
</script>

<style scoped>
.speaker-icon {
  display: flex;
  align-items: center;
  justify-content: center;
}

.speaker-icon.is-offline {
  opacity: 0.4;
}

.speaker-title {
  display: flex;
  flex-direction: column;
}

.speaker-title__type,
.speaker-title__status {
  color: var(--color-text-secondary);
}

.text-secondary {
  color: var(--color-text-secondary);
}
</style>
