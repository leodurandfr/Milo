<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal-container">
      <ModalHeader title="Ajouter une station" :show-back="true" variant="neutral" @back="$emit('close')">
      </ModalHeader>

      <div class="modal-content">
        <form @submit.prevent="handleSubmit" class="station-form">
          <!-- Image Upload Section -->
          <div class="form-section">
            <label class="form-label text-body">Image de la station (optionnel)</label>
            <div class="image-upload-container">
              <!-- Preview -->
              <div class="image-preview" :class="{ 'has-image': imagePreview || selectedFile }">
                <img v-if="imagePreview" :src="imagePreview" alt="Aperçu" class="preview-img" />
                <div v-else class="preview-placeholder">
                  <span class="placeholder-icon">📻</span>
                  <p class="text-mono">Cliquez pour choisir</p>
                </div>

                <!-- Remove button if image selected -->
                <button v-if="selectedFile" type="button" class="remove-image-btn" @click="removeImage">
                  ✕
                </button>
              </div>

              <!-- Hidden file input -->
              <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp,image/gif"
                @change="handleFileSelect" class="file-input" />

              <!-- Click zone -->
              <button type="button" class="upload-btn" @click="$refs.fileInput.click()">
                {{ selectedFile ? 'Changer l\'image' : 'Choisir une image' }}
              </button>

              <p class="file-info text-mono">JPG, PNG, WEBP, GIF - Max 5MB</p>
            </div>
          </div>

          <!-- Station Details -->
          <div class="form-section">
            <label class="form-label text-body">Nom de la station *</label>
            <input v-model="formData.name" type="text" required class="form-input text-body-small"
              placeholder="Ex: RTL" />
          </div>

          <div class="form-section">
            <label class="form-label text-body">URL du flux audio *</label>
            <input v-model="formData.url" type="url" required class="form-input text-body-small"
              placeholder="Ex: http://streaming.radio.fr/stream" />
          </div>

          <div class="form-row">
            <div class="form-section">
              <label class="form-label text-body">Pays</label>
              <select v-model="formData.country" class="form-input text-body-small">
                <option value="France">France</option>
                <option value="United Kingdom">Royaume-Uni</option>
                <option value="United States">États-Unis</option>
                <option value="Germany">Allemagne</option>
                <option value="Spain">Espagne</option>
                <option value="Italy">Italie</option>
              </select>
            </div>

            <div class="form-section">
              <label class="form-label text-body">Genre</label>
              <select v-model="formData.genre" class="form-input text-body-small">
                <option value="Variety">Variété</option>
                <option value="Pop">Pop</option>
                <option value="Rock">Rock</option>
                <option value="Jazz">Jazz</option>
                <option value="Classical">Classique</option>
                <option value="Electronic">Electronic</option>
                <option value="News">News</option>
              </select>
            </div>
          </div>

          <!-- Error Message -->
          <div v-if="errorMessage" class="error-message text-body-small">
            ❌ {{ errorMessage }}
          </div>

          <!-- Actions -->
          <div class="form-actions">
            <button type="button" class="btn-secondary" @click="$emit('close')" :disabled="isSubmitting">
              Annuler
            </button>
            <button type="submit" class="btn-primary" :disabled="isSubmitting || !formData.name || !formData.url">
              {{ isSubmitting ? 'Ajout en cours...' : 'Ajouter' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue';
import { useRadioStore } from '@/stores/radioStore';
import ModalHeader from '@/components/ui/ModalHeader.vue';

const emit = defineEmits(['close', 'success']);
const radioStore = useRadioStore();

const fileInput = ref(null);
const selectedFile = ref(null);
const imagePreview = ref(null);
const isSubmitting = ref(false);
const errorMessage = ref('');

const formData = reactive({
  name: '',
  url: '',
  country: 'France',
  genre: 'Variety',
  bitrate: 128,
  codec: 'MP3'
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
  if (fileInput.value) {
    fileInput.value.value = '';
  }
}

async function handleSubmit() {
  if (isSubmitting.value) return;

  errorMessage.value = '';
  isSubmitting.value = true;

  try {
    const stationData = {
      name: formData.name.trim(),
      url: formData.url.trim(),
      country: formData.country,
      genre: formData.genre,
      bitrate: formData.bitrate,
      codec: formData.codec,
      image: selectedFile.value // File object or null
    };

    const result = await radioStore.addCustomStation(stationData);

    if (result.success) {
      console.log('✅ Station ajoutée avec succès:', result.station);
      emit('success', result.station);
      emit('close');
    } else {
      errorMessage.value = result.error || 'Échec de l\'ajout de la station';
    }
  } catch (error) {
    console.error('❌ Erreur ajout station:', error);
    errorMessage.value = error.message || 'Une erreur est survenue';
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: var(--space-04);
}

.modal-container {
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-07);
  width: 100%;
  max-width: 500px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  position: relative;
}

.modal-container::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  border-radius: var(--radius-07);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

.modal-content {
  overflow-y: auto;
  padding: var(--space-04);
  min-height: 0;
}

.station-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

.form-label {
  color: var(--color-text);
  font-weight: 500;
}

.form-input {
  padding: var(--space-03) var(--space-04);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  background: var(--color-background-neutral);
  color: var(--color-text);
  transition: border-color var(--transition-fast);
}

.form-input:focus {
  outline: none;
  border-color: var(--color-brand);
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
  border: 2px dashed var(--color-border);
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
}

.file-input {
  display: none;
}

.upload-btn {
  padding: var(--space-03) var(--space-05);
  background: var(--color-background-neutral);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  color: var(--color-text);
  font-size: var(--font-size-body);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.upload-btn:hover {
  background: var(--color-background);
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
  background: rgba(255, 0, 0, 0.1);
  border: 1px solid rgba(255, 0, 0, 0.3);
  border-radius: var(--radius-04);
  color: #ff4444;
}

/* Actions */
.form-actions {
  display: flex;
  gap: var(--space-03);
  justify-content: flex-end;
  padding-top: var(--space-02);
}

.btn-primary,
.btn-secondary {
  padding: var(--space-03) var(--space-05);
  border-radius: var(--radius-04);
  font-size: var(--font-size-body);
  cursor: pointer;
  transition: all var(--transition-fast);
  border: none;
}

.btn-primary {
  background: var(--color-brand);
  color: white;
}

.btn-primary:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  background: var(--color-background-neutral);
  color: var(--color-text);
  border: 1px solid var(--color-border);
}

.btn-secondary:hover:not(:disabled) {
  background: var(--color-background);
}

.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
