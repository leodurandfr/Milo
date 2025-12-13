<!-- frontend/src/components/dsp/LinkedClientsDialog.vue -->
<!-- Dialog for managing linked DSP clients -->
<template>
  <Transition name="dialog-fade">
    <div v-if="isOpen" class="linked-clients-dialog">
      <div class="dialog-overlay" @click="handleClose"></div>
      <div class="dialog-content">
        <h3 class="heading-3">{{ $t('dsp.linkedClients.title', 'Link Clients') }}</h3>
        <p class="dialog-description">
          {{ $t('dsp.linkedClients.description', 'Linked clients share the same DSP settings. Changes to one will apply to all.') }}
        </p>

        <!-- Available clients list -->
        <div class="clients-list">
          <label
            v-for="target in availableTargets"
            :key="target.id"
            class="client-item"
            :class="{ disabled: !target.available }"
          >
            <input
              type="checkbox"
              :checked="selectedClients.includes(target.id)"
              :disabled="!target.available"
              @change="toggleClient(target.id)"
            />
            <span class="client-name">{{ target.name }}</span>
            <span v-if="isLinked(target.id)" class="linked-badge">
              {{ $t('dsp.linkedClients.linked', 'Linked') }}
            </span>
            <span v-if="!target.available" class="unavailable-badge">
              {{ $t('dsp.linkedClients.offline', 'Offline') }}
            </span>
          </label>
        </div>

        <!-- Current linked group info -->
        <div v-if="currentGroupClients.length > 1" class="current-group">
          <span class="group-label">{{ $t('dsp.linkedClients.currentGroup', 'Currently linked:') }}</span>
          <span class="group-clients">{{ currentGroupClients.join(', ') }}</span>
        </div>

        <div class="dialog-actions">
          <Button
            v-if="hasExistingLink"
            variant="background-strong"
            :disabled="saving"
            @click="handleUnlink"
          >
            {{ $t('dsp.linkedClients.unlink', 'Unlink') }}
          </Button>
          <Button variant="background-strong" @click="handleClose">
            {{ $t('common.cancel', 'Cancel') }}
          </Button>
          <Button
            variant="brand"
            :loading="saving"
            :disabled="selectedClients.length < 2"
            @click="handleSave"
          >
            {{ $t('dsp.linkedClients.link', 'Link Selected') }}
          </Button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import Button from '@/components/ui/Button.vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['close']);

const dspStore = useDspStore();
const saving = ref(false);
const selectedClients = ref([]);

// Get available targets from store
const availableTargets = computed(() => dspStore.availableTargets);

// Get current group's clients for the selected target
const currentGroupClients = computed(() => {
  const linkedIds = dspStore.getLinkedClientIds(dspStore.selectedTarget);
  if (linkedIds.length <= 1) return [];

  // Map IDs to names
  return linkedIds.map(id => {
    const target = availableTargets.value.find(t => t.id === id);
    return target ? target.name : id;
  });
});

// Check if current target has an existing link
const hasExistingLink = computed(() => {
  return dspStore.isClientLinked(dspStore.selectedTarget);
});

// Check if a client is linked
function isLinked(clientId) {
  return dspStore.isClientLinked(clientId);
}

// Toggle client selection
function toggleClient(clientId) {
  const index = selectedClients.value.indexOf(clientId);
  if (index === -1) {
    selectedClients.value.push(clientId);
  } else {
    selectedClients.value.splice(index, 1);
  }
}

// Initialize selection when dialog opens
watch(() => props.isOpen, (open) => {
  if (open) {
    // Pre-select currently linked clients if any
    const linkedIds = dspStore.getLinkedClientIds(dspStore.selectedTarget);
    if (linkedIds.length > 1) {
      selectedClients.value = [...linkedIds];
    } else {
      // Pre-select current target
      selectedClients.value = [dspStore.selectedTarget];
    }
  }
});

function handleClose() {
  if (!saving.value) {
    emit('close');
  }
}

async function handleSave() {
  if (selectedClients.value.length < 2) return;

  saving.value = true;
  try {
    await dspStore.linkClients(selectedClients.value);
    emit('close');
  } catch (error) {
    console.error('Error linking clients:', error);
  } finally {
    saving.value = false;
  }
}

async function handleUnlink() {
  saving.value = true;
  try {
    await dspStore.unlinkClient(dspStore.selectedTarget);
    selectedClients.value = [dspStore.selectedTarget];
  } catch (error) {
    console.error('Error unlinking client:', error);
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.linked-clients-dialog {
  position: fixed;
  inset: 0;
  z-index: 9000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-overlay {
  position: absolute;
  inset: 0;
  background: var(--color-background-medium-32);
  backdrop-filter: blur(4px);
}

.dialog-content {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  padding: var(--space-05);
  background: var(--color-background-elevated);
  border-radius: var(--radius-06);
  box-shadow: var(--shadow-02);
  min-width: 300px;
  max-width: 90vw;
  max-height: 80vh;
}

.dialog-content h3 {
  margin: 0;
  color: var(--color-text);
}

.dialog-description {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.4;
}

.clients-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  max-height: 200px;
  overflow-y: auto;
  padding: var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

.client-item {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-02) var(--space-03);
  border-radius: var(--radius-03);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.client-item:hover:not(.disabled) {
  background: var(--color-background-strong);
}

.client-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.client-item input[type="checkbox"] {
  width: 18px;
  height: 18px;
  accent-color: var(--color-brand);
}

.client-name {
  flex: 1;
  font-size: 14px;
  color: var(--color-text);
}

.linked-badge {
  font-size: 11px;
  padding: 2px 6px;
  background: var(--color-brand);
  color: var(--color-text-inverse);
  border-radius: var(--radius-02);
}

.unavailable-badge {
  font-size: 11px;
  padding: 2px 6px;
  background: var(--color-background-strong);
  color: var(--color-text-light);
  border-radius: var(--radius-02);
}

.current-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  padding: var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-03);
  font-size: 13px;
}

.group-label {
  color: var(--color-text-secondary);
}

.group-clients {
  color: var(--color-text);
  font-weight: 500;
}

.dialog-actions {
  display: flex;
  gap: var(--space-02);
  justify-content: flex-end;
}

/* Transition animations */
.dialog-fade-enter-active,
.dialog-fade-leave-active {
  transition: opacity var(--transition-normal);
}

.dialog-fade-enter-active .dialog-content,
.dialog-fade-leave-active .dialog-content {
  transition: transform var(--transition-normal), opacity var(--transition-normal);
}

.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;
}

.dialog-fade-enter-from .dialog-content,
.dialog-fade-leave-to .dialog-content {
  transform: scale(0.95);
  opacity: 0;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .dialog-content {
    min-width: 280px;
    padding: var(--space-04);
  }
}
</style>
