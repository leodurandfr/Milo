<!-- frontend/src/components/multiroom/ClientEdit.vue -->
<!-- Form for editing a single client's name (not in a zone) -->
<template>
  <div class="client-edit">
    <!-- Client Name Input -->
    <section class="settings-section">
      <div class="section-group">
        <h2 class="heading-2">{{ $t('multiroom.speakerName', 'Speaker Name') }}</h2>
        <div class="client-info">
          <span class="client-hostname text-mono">{{ client?.host }}</span>
          <span class="client-arrow">→</span>
        </div>
        <InputText
          v-model="clientName"
          :placeholder="client?.host"
          size="medium"
          :maxlength="50"
        />
      </div>
    </section>

    <!-- Actions -->
    <div class="actions">
      <Button
        variant="background-strong"
        size="medium"
        :disabled="saving"
        @click="$emit('back')"
      >
        {{ $t('common.cancel', 'Cancel') }}
      </Button>
      <Button
        variant="brand"
        size="medium"
        :loading="saving"
        :disabled="!clientName.trim()"
        @click="handleSave"
      >
        {{ $t('common.save', 'Save') }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useMultiroomStore } from '@/stores/multiroomStore';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';

const props = defineProps({
  clientId: {
    type: String,
    required: true
  }
});

const emit = defineEmits(['back', 'saved']);

const multiroomStore = useMultiroomStore();
const saving = ref(false);
const clientName = ref('');

// Find client by ID
const client = computed(() =>
  multiroomStore.clients.find(c => c.id === props.clientId)
);

onMounted(() => {
  if (client.value) {
    clientName.value = client.value.name || client.value.host;
  }
});

async function handleSave() {
  const newName = clientName.value?.trim();
  if (!newName) return;

  saving.value = true;
  try {
    await multiroomStore.updateClientName(props.clientId, newName);
    emit('saved');
    emit('back');
  } catch (error) {
    console.error('Error saving client name:', error);
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.client-edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
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
  gap: var(--space-04);
}

.client-info {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.client-hostname {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.client-arrow {
  color: var(--color-text-light);
  font-size: 12px;
}

.actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
  padding-top: var(--space-02);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .actions {
    grid-template-columns: 1fr;
  }

  .actions > :last-child {
    order: -1;
  }
}
</style>
