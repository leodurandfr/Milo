<template>
  <section class="settings-section">
    <form @submit.prevent="handleSubmit" class="station-form">
        <!-- Image Upload Section -->
        <div class="form-group">
          <label class="text-mono">Image de la station (optionnel)</label>
          <div class="image-upload-container">
            <!-- Preview -->
            <div class="image-preview" :class="{ 'has-image': currentImageUrl || imagePreview || selectedFile }">
              <img v-if="imagePreview" :src="imagePreview" alt="Aperçu" class="preview-img" />
              <img v-else-if="currentImageUrl" :src="currentImageUrl" alt="Image actuelle" class="preview-img" />
              <div v-else class="preview-placeholder">
                <img :src="placeholderImg" alt="Station sans image" class="placeholder-icon" />
                <p class="text-mono">Cliquez pour choisir</p>
              </div>

              <!-- Remove button if image selected -->
              <button v-if="selectedFile || currentImageUrl" type="button" class="remove-image-btn" @click="removeImage">
                ✕
              </button>
            </div>

            <!-- Hidden file input -->
            <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp,image/gif"
              @change="handleFileSelect" class="file-input" />

            <!-- Click zone -->
            <button type="button" class="upload-btn" @click="$refs.fileInput.click()">
              {{ selectedFile || currentImageUrl ? 'Changer l\'image' : 'Choisir une image' }}
            </button>

            <p class="file-info text-mono">JPG, PNG, WEBP, GIF - Max 5MB</p>
          </div>
        </div>

        <!-- Station Details -->
        <div class="form-group">
          <label class="text-mono">Nom de la station *</label>
          <InputText v-model="formData.name" type="text" input-class="text-body-small"
            placeholder="Ex: RTL" />
        </div>

        <div class="form-group">
          <label class="text-mono">URL du flux audio *</label>
          <InputText v-model="formData.url" type="url" input-class="text-body-small"
            placeholder="Ex: http://streaming.radio.fr/stream" />
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="text-mono">Pays</label>
            <Dropdown v-model="formData.country" :options="countryOptions" />
          </div>

          <div class="form-group">
            <label class="text-mono">Genre</label>
            <InputText v-model="formData.genre" type="text" input-class="text-body-small"
              placeholder="Ex: Pop, Rock, Jazz" />
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="text-mono">Codec</label>
            <InputText v-model="formData.codec" type="text" input-class="text-body-small"
              placeholder="Ex: MP3, AAC, OGG" />
          </div>

          <div class="form-group">
            <label class="text-mono">Bitrate (kbps)</label>
            <InputText v-model="formData.bitrate" type="number" input-class="text-body-small"
              placeholder="Ex: 128, 192, 320" />
          </div>
        </div>

        <!-- Error Message -->
        <div v-if="errorMessage" class="error-message text-mono">
          ❌ {{ errorMessage }}
        </div>

        <!-- Actions -->
        <div class="form-actions">
          <Button
            v-if="canRestore"
            variant="secondary"
            :class="{ 'restore-btn': !isConfirmingRestore, 'restore-btn-confirm': isConfirmingRestore }"
            @click="handleRestoreClick"
            :disabled="isSubmitting"
          >
            {{ isConfirmingRestore ? 'Confirmer' : 'Restaurer la station' }}
          </Button>
          <Button
            v-if="canDelete"
            variant="secondary"
            :class="{ 'delete-btn': !isConfirmingDelete, 'delete-btn-confirm': isConfirmingDelete }"
            @click="handleDeleteClick"
            :disabled="isSubmitting"
          >
            {{ isConfirmingDelete ? 'Confirmer' : 'Supprimer la station' }}
          </Button>
          <div class="spacer"></div>
          <Button variant="secondary" @click="$emit('back')" :disabled="isSubmitting">
            Annuler
          </Button>
          <Button variant="primary" type="submit" :disabled="isSubmitting || !formData.name || !formData.url">
            {{ isSubmitting ? 'Enregistrement...' : 'Enregistrer' }}
          </Button>
        </div>
      </form>
  </section>
</template>

<script setup>
import { ref, reactive, onMounted, watch, computed } from 'vue';
import Button from '@/components/ui/Button.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import InputText from '@/components/ui/InputText.vue';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';
import axios from 'axios';

const props = defineProps({
  preselectedStation: {
    type: Object,
    default: null
  },
  canRestore: {
    type: Boolean,
    default: false
  },
  canDelete: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['back', 'success', 'restore', 'delete']);

const fileInput = ref(null);
const selectedFile = ref(null);
const imagePreview = ref(null);
const currentImageUrl = ref('');
const shouldRemoveImage = ref(false);
const isSubmitting = ref(false);
const errorMessage = ref('');
const isConfirmingRestore = ref(false);
const isConfirmingDelete = ref(false);
const availableCountries = ref([]);
let lastClickTime = 0;

const formData = reactive({
  name: '',
  url: '',
  country: 'France',
  genre: 'Variety',
  codec: 'MP3',
  bitrate: '128'
});

// Load available countries from API
async function loadAvailableCountries() {
  try {
    const response = await axios.get('/api/radio/countries');
    availableCountries.value = response.data;
    console.log(`📍 Loaded ${availableCountries.value.length} countries`);
  } catch (error) {
    console.error('❌ Error loading countries:', error);
    availableCountries.value = [];
  }
}

// Convert countries to dropdown format
const countryOptions = computed(() => {
  if (availableCountries.value.length === 0) {
    return [{ label: 'Chargement...', value: 'France' }];
  }
  return availableCountries.value.map(country => ({
    label: country.name,
    value: country.name
  }));
});

// Initialize form with preselected station data
function initializeForm() {
  // Reset image-related fields
  selectedFile.value = null;
  imagePreview.value = null;
  shouldRemoveImage.value = false;
  currentImageUrl.value = '';

  if (props.preselectedStation) {
    formData.name = props.preselectedStation.name || '';
    formData.url = props.preselectedStation.url || props.preselectedStation.url_resolved || '';
    formData.country = props.preselectedStation.country || 'France';
    formData.genre = props.preselectedStation.genre || 'Variety';
    formData.codec = props.preselectedStation.codec || 'MP3';
    formData.bitrate = String(props.preselectedStation.bitrate || 128);

    // Set current image URL if exists
    if (props.preselectedStation.favicon) {
      currentImageUrl.value = props.preselectedStation.favicon;
    }
  }
}

// Watch for changes to preselectedStation prop
watch(() => props.preselectedStation, () => {
  initializeForm();
  isConfirmingRestore.value = false;
  isConfirmingDelete.value = false;
}, { immediate: true });

// Reset confirmation state if user modifies form data
watch([() => formData.name, () => formData.url, () => formData.country, () => formData.genre, selectedFile], () => {
  isConfirmingRestore.value = false;
  isConfirmingDelete.value = false;
});

onMounted(() => {
  initializeForm();
  loadAvailableCountries();
});

function handleFileSelect(event) {
  const file = event.target.files[0];

  if (!file) {
    return;
  }

  // Validate file type
  const validTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
  if (!validTypes.includes(file.type)) {
    errorMessage.value = 'Format d\'image non supporté. Utilisez JPG, PNG, WEBP ou GIF.';
    return;
  }

  // Validate file size (5MB max)
  const maxSize = 5 * 1024 * 1024; // 5MB
  if (file.size > maxSize) {
    errorMessage.value = 'Image trop volumineuse. Maximum 5MB.';
    return;
  }

  selectedFile.value = file;
  shouldRemoveImage.value = false;
  errorMessage.value = '';

  // Create preview
  const reader = new FileReader();
  reader.onload = (e) => {
    imagePreview.value = e.target.result;
  };
  reader.readAsDataURL(file);
}

function removeImage() {
  selectedFile.value = null;
  imagePreview.value = null;
  currentImageUrl.value = '';
  shouldRemoveImage.value = true;
  if (fileInput.value) {
    fileInput.value.value = '';
  }
}

function handleRestoreClick() {
  const now = Date.now();

  // Debounce: ignore clicks within 600ms of the last click
  if (now - lastClickTime < 600) {
    return;
  }

  // Update last click time
  lastClickTime = now;

  if (isConfirmingRestore.value) {
    // Second click - confirm and emit restore event
    emit('restore');
    isConfirmingRestore.value = false;
  } else {
    // First click - show confirmation state
    isConfirmingRestore.value = true;
  }
}

function handleDeleteClick() {
  const now = Date.now();

  // Debounce: ignore clicks within 600ms of the last click
  if (now - lastClickTime < 600) {
    return;
  }

  // Update last click time
  lastClickTime = now;

  if (isConfirmingDelete.value) {
    // Second click - confirm and emit delete event
    emit('delete');
    isConfirmingDelete.value = false;
  } else {
    // First click - show confirmation state
    isConfirmingDelete.value = true;
  }
}

async function handleSubmit() {
  if (isSubmitting.value || !props.preselectedStation) return;

  errorMessage.value = '';
  isSubmitting.value = true;

  try {
    const formDataToSend = new FormData();
    formDataToSend.append('station_id', props.preselectedStation.id);
    formDataToSend.append('name', formData.name.trim());
    formDataToSend.append('url', formData.url.trim());
    formDataToSend.append('country', formData.country);
    formDataToSend.append('genre', formData.genre);
    formDataToSend.append('codec', formData.codec);
    formDataToSend.append('bitrate', parseInt(formData.bitrate, 10));
    formDataToSend.append('remove_image', shouldRemoveImage.value.toString());

    if (selectedFile.value) {
      formDataToSend.append('image', selectedFile.value);
    }

    const response = await fetch('/api/radio/favorites/modify-metadata', {
      method: 'POST',
      body: formDataToSend
    });

    const data = await response.json();

    if (data.success) {
      console.log('✅ Station modifiée avec succès:', data.station);
      emit('success', data.station);
      emit('back');
    } else {
      errorMessage.value = data.error || 'Échec de la modification de la station';
    }
  } catch (error) {
    console.error('❌ Erreur modification station:', error);
    errorMessage.value = error.message || 'Une erreur est survenue';
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
}

.station-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-group label {
  color: var(--color-text-secondary);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

/* Image Upload */
.image-upload-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  align-items: center;
}

.image-preview {
  width: 200px;
  height: 200px;
  border-radius: var(--radius-05);
  overflow: hidden;
  background: var(--color-background-neutral);
  border: 2px dashed var(--color-background-glass);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  transition: border-color var(--transition-fast);
}

.image-preview.has-image {
  border-style: solid;
  border-color: var(--color-brand);
}

.preview-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.preview-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
  color: var(--color-text-secondary);
}

.placeholder-icon {
  font-size: 48px;
  width: 80px;
  height: 80px;
  object-fit: cover;
}

.file-input {
  display: none;
}

.upload-btn {
  padding: var(--space-03) var(--space-05);
  background: var(--color-background-neutral);
  border: 2px solid var(--color-background-glass);
  border-radius: var(--radius-03);
  color: var(--color-text);
  font-size: var(--font-size-body);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.upload-btn:hover {
  background: var(--color-background);
  border-color: var(--color-brand);
  transform: translateY(-2px);
}

.file-info {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
}

.remove-image-btn {
  position: absolute;
  top: var(--space-02);
  right: var(--space-02);
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  background: rgba(0, 0, 0, 0.7);
  color: white;
  border: none;
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform var(--transition-fast);
  z-index: 10;
}

.remove-image-btn:hover {
  transform: scale(1.1);
}

/* Error Message */
.error-message {
  padding: var(--space-03);
  background: rgba(244, 67, 54, 0.1);
  border: 2px solid rgba(244, 67, 54, 0.3);
  border-radius: var(--radius-04);
  color: rgb(244, 67, 54);
}

/* Actions */
.form-actions {
  display: flex;
  gap: var(--space-03);
  justify-content: flex-end;
  padding-top: var(--space-02);
}

.form-actions .spacer {
  flex: 1;
}

.form-actions .restore-btn {
  border: 2px solid rgba(244, 67, 54, 0.3);
  color: rgb(244, 67, 54);
}

.form-actions .restore-btn-confirm {
  background: rgb(244, 67, 54);
  color: white;
  border: 2px solid rgb(244, 67, 54);
}

.form-actions .delete-btn {
  border: 2px solid rgba(244, 67, 54, 0.3);
  color: rgb(244, 67, 54);
}

.form-actions .delete-btn-confirm {
  background: rgb(244, 67, 54);
  color: white;
  border: 2px solid rgb(244, 67, 54);
}

/* Mobile responsive */
@media (max-width: 600px) {
  .form-row {
    grid-template-columns: 1fr;
  }

  .image-preview {
    width: 150px;
    height: 150px;
  }

}
</style>
