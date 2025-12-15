<!-- frontend/src/components/dsp/ZoneList.vue -->
<!-- List view showing all DSP zones with modify/delete actions -->
<template>
  <div class="zone-list">
    <!-- Existing Zones -->
    <section v-if="zones.length > 0" class="settings-section">
      <p class="description text-mono">
        {{ $t('dsp.zones.description', 'Link clients to create zones with shared DSP settings. Changes to one zone member will apply to all.') }}
      </p>
      <div class="section-group">
        <div
          v-for="zone in zones"
          :key="zone.id"
          class="zone-card"
        >
          <div class="zone-info">
            <h3 class="zone-name heading-3">{{ zone.displayName }}</h3>
            <p class="zone-clients text-mono">{{ zone.clientNames }}</p>
          </div>
          <div class="zone-actions">
            <Button
              variant="background-strong"
              size="small"
              @click="$emit('edit-zone', zone.id)"
            >
              {{ $t('common.modify', 'Modify') }}
            </Button>
            <Button
              variant="important"
              size="small"
              :disabled="deleting === zone.id"
              @click="handleDeleteClick(zone.id)"
            >
              {{ isConfirmingDelete === zone.id ? $t('common.confirm', 'Confirm') : $t('common.delete', 'Delete') }}
            </Button>
          </div>
        </div>
      </div>
    </section>

    <!-- Empty State -->
    <section v-else class="settings-section">
      <p class="description text-mono">
        {{ $t('dsp.zones.description', 'Link clients to create zones with shared DSP settings. Changes to one zone member will apply to all.') }}
      </p>
      <div class="empty-state">
        <span class="empty-icon">🔗</span>
        <p class="empty-text text-mono">
          {{ $t('dsp.zones.noZones', 'No zones created yet. Create a zone to link multiple clients.') }}
        </p>
      </div>
    </section>

    <!-- Create Zone Button -->
    <Button
      variant="brand"
      size="medium"
      class="create-button"
      @click="$emit('create-zone')"
    >
      {{ $t('dsp.zones.createZone', 'Create Zone') }}
    </Button>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import Button from '@/components/ui/Button.vue';

const emit = defineEmits(['edit-zone', 'create-zone', 'saved']);

const dspStore = useDspStore();

// Confirmation state
const isConfirmingDelete = ref(null);
const deleting = ref(null);
let lastClickTime = 0;

// Get available targets from store (for client name lookup)
const availableTargets = computed(() => dspStore.availableTargets);

// Transform linked groups into display format
const zones = computed(() => {
  return dspStore.linkedGroups.map((group, index) => {
    // Build client names string
    const clientNames = (group.client_ids || [])
      .map(id => {
        const target = availableTargets.value.find(t => t.id === id);
        return target ? target.name : id;
      })
      .join(' · ');

    // Use custom name or generate default
    const displayName = group.name || `Zone ${index + 1}`;

    return {
      id: group.id,
      displayName,
      clientNames,
      clientIds: group.client_ids || []
    };
  });
});

// Reset confirmation state when zones change
watch(() => dspStore.linkedGroups, () => {
  isConfirmingDelete.value = null;
});

function handleDeleteClick(zoneId) {
  const now = Date.now();

  // Debounce: ignore clicks within 600ms of the last click
  if (now - lastClickTime < 600) {
    return;
  }

  // Update last click time
  lastClickTime = now;

  if (isConfirmingDelete.value === zoneId) {
    // Second click - confirm deletion
    deleteZone(zoneId);
    isConfirmingDelete.value = null;
  } else {
    // First click - show confirmation state
    isConfirmingDelete.value = zoneId;
  }
}

async function deleteZone(zoneId) {
  const zone = zones.value.find(z => z.id === zoneId);
  if (!zone) return;

  deleting.value = zoneId;
  try {
    // Unlink all clients in the zone
    for (const clientId of zone.clientIds) {
      await dspStore.unlinkClient(clientId);
    }
    emit('saved');
  } catch (error) {
    console.error('Error deleting zone:', error);
  } finally {
    deleting.value = null;
  }
}
</script>

<style scoped>
.zone-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.description {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.5;
  margin: 0;
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.section-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

/* Zone Card */
.zone-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
  padding: var(--space-04);
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
}

.zone-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.zone-name {
  margin: 0;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.zone-clients {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.zone-actions {
  display: flex;
  gap: var(--space-02);
  flex-shrink: 0;
}

/* Empty State */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
  padding: var(--space-05);
  text-align: center;
}

.empty-icon {
  font-size: 32px;
  opacity: 0.5;
}

.empty-text {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}

/* Create Button */
.create-button {
  width: 100%;
  justify-content: center;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .zone-card {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-03);
  }

  .zone-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
}
</style>
