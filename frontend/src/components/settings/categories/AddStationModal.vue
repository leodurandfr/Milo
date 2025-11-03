<template>
  <section class="settings-section">
    <form @submit.prevent="handleSubmit" class="station-form">
        <!-- Image Upload Section -->
        <div class="form-group">
          <label class="text-mono">Image de la station (optionnel)</label>
          <div class="image-upload-container">
            <!-- Preview -->
            <div class="image-preview" :class="{ 'has-image': imagePreview || selectedFile }">
              <img v-if="imagePreview" :src="imagePreview" alt="Aperçu" class="preview-img" />
              <div v-else class="preview-placeholder">
                <img :src="placeholderImg" alt="Station sans image" class="placeholder-icon" />
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
        <div class="form-group">
          <label class="text-mono">Nom de la station *</label>
          <input v-model="formData.name" type="text" required class="form-input text-body-small"
            placeholder="Ex: RTL" />
        </div>

        <div class="form-group">
          <label class="text-mono">URL du flux audio *</label>
          <input v-model="formData.url" type="url" required class="form-input text-body-small"
            placeholder="Ex: http://streaming.radio.fr/stream" />
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="text-mono">Pays</label>
            <select v-model="formData.country" class="form-input text-body-small">
              <option value="France">France</option>
              <option value="United Kingdom">Royaume-Uni</option>
              <option value="United States">États-Unis</option>
              <option value="Germany">Allemagne</option>
              <option value="Spain">Espagne</option>
              <option value="Italy">Italie</option>
            </select>
          </div>

          <div class="form-group">
            <label class="text-mono">Genre</label>
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
        <div v-if="errorMessage" class="error-message text-mono">
          ❌ {{ errorMessage }}
        </div>

        <!-- Actions -->
        <div class="form-actions">
          <Button variant="secondary" @click="$emit('back')" :disabled="isSubmitting">
            Annuler
          </Button>
          <Button variant="primary" type="submit" :disabled="isSubmitting || !formData.name || !formData.url">
            {{ isSubmitting ? 'Ajout en cours...' : 'Ajouter' }}
          </Button>
        </div>
      </form>
  </section>
</template>

<script setup>
import { ref, reactive } from 'vue';
import { useRadioStore } from '@/stores/radioStore';
import Button from '@/components/ui/Button.vue';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const emit = defineEmits(['back', 'success']);
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
      emit('back');
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
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
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

.form-input {
  padding: var(--space-02) var(--space-03);
  border: 2px solid var(--color-background-glass);
  border-radius: var(--radius-03);
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
