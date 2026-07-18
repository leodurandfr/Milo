<!--
  Single-field modal for naming a playlist. Reused for both create (empty name)
  and rename (seeded with the current name). Dumb by design: it emits submit(name)
  and the parent performs the async write, drives :loading, and closes it.
-->
<template>
  <Modal :is-open="isOpen" @close="$emit('close')">
    <div class="playlist-name-modal">
      <h2 class="heading-2 playlist-name-modal__title">{{ title }}</h2>

      <InputText
        v-model="name"
        :placeholder="placeholder || t('musicLibrary.playlists.namePlaceholder')"
        :maxlength="255"
        @submit="handleSubmit"
      />

      <Button
        variant="brand"
        size="medium"
        :loading="loading"
        :disabled="loading || !name.trim()"
        @click="handleSubmit"
      >
        {{ submitLabel }}
      </Button>
    </div>
  </Modal>
</template>

<script setup>
import { ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import Modal from '@/components/ui/Modal.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    default: false,
  },
  title: {
    type: String,
    required: true,
  },
  submitLabel: {
    type: String,
    required: true,
  },
  initialName: {
    type: String,
    default: '',
  },
  placeholder: {
    type: String,
    default: '',
  },
  loading: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['close', 'submit']);

const { t } = useI18n();
const name = ref('');

// Seed (or clear) the field each time the modal opens.
watch(() => props.isOpen, (open) => {
  if (open) name.value = props.initialName;
}, { immediate: true });

function handleSubmit() {
  const trimmed = name.value.trim();
  if (!trimmed || props.loading) return;
  emit('submit', trimmed);
}
</script>

<style scoped>
.playlist-name-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.playlist-name-modal__title {
  margin: 0;
  color: var(--color-text);
}
</style>
