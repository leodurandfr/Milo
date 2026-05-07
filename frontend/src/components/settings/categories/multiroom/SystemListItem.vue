<!-- Shared system list item for multiroom settings -->
<template>
  <ListItemButton variant="background" icon-variant="standard" :action="action" :disabled="disabled">
    <template #icon>
      <div class="speaker-icon" :class="{ 'is-offline': !online, 'is-discovery': isDiscovery }">
        <SvgIcon :name="speakerIcon" :size="28" />
      </div>
    </template>
    <template #title>
      <div class="speaker-title">
        <span :class="{ 'text-secondary': !online }">{{ name }}</span>
        <span class="text-mono-small speaker-title__meta" :class="metaClass">
          <SvgIcon
            v-if="discoverySource === 'ethernet'"
            name="network"
            :size="16"
            class="discovery-badge" />
          <WifiSignal
            v-else-if="discoverySource === 'wifi'"
            :signal="signal ?? 100"
            :size="16"
            class="discovery-badge" />
          <span>{{ metaText }}</span>
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
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';

const props = defineProps({
  name: {
    type: String,
    required: true
  },
  macId: {
    type: String,
    default: ''
  },
  online: {
    type: Boolean,
    default: true
  },
  action: {
    type: String,
    default: 'caret'
  },
  disabled: {
    type: Boolean,
    default: false
  },
  // When set, render a discovery badge and use a generic speaker icon.
  discoverySource: {
    type: String,
    default: '',
    validator: (value) => ['', 'ethernet', 'wifi'].includes(value)
  },
  // Signal strength (0-100) for wifi-discovery items; ignored otherwise.
  signal: {
    type: Number,
    default: null
  },
  // Custom status text shown below the name (replaces speaker type label).
  status: {
    type: String,
    default: ''
  },
  statusVariant: {
    type: String,
    default: '',
    validator: (value) => ['', 'configuring'].includes(value)
  }
});

const { t } = useI18n();
const equalizerStore = useEqualizerStore();

const isDiscovery = computed(() => props.discoverySource !== '');

const speakerIcon = computed(() => {
  if (isDiscovery.value) return 'speakerShelf';
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
  return t(`multiroom.systemTypes.${speakerType}`);
});

const metaText = computed(() => {
  if (!props.online) return t('multiroom.offline');
  if (props.status) return props.status;
  return speakerTypeLabel.value;
});

const metaClass = computed(() => ({
  'speaker-title__meta--configuring': props.statusVariant === 'configuring'
}));
</script>

<style scoped>
.speaker-icon {
  display: flex;
  align-items: center;
  justify-content: center;
}

.speaker-icon.is-offline,
.speaker-icon.is-discovery {
  opacity: 0.6;
}

.speaker-title {
  display: flex;
  flex-direction: column;
}

.speaker-title__meta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-01);
  color: var(--color-text-secondary);
}

.speaker-title__meta--configuring {
  color: var(--color-brand);
}

.discovery-badge {
  flex-shrink: 0;
}

.text-secondary {
  color: var(--color-text-secondary);
}
</style>
