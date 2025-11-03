<template>
  <section class="settings-section">
    <form @submit.prevent="handleSubmit" class="change-image-form">
        <!-- Nom de la station (recherche) -->
        <div class="form-group">
          <label class="text-mono">Nom exact de la station *</label>
          <input v-model="stationName" type="text" required class="form-input text-body-small"
            placeholder="Ex: RTL" @input="searchStation" />
          <p v-if="searchResult === 'searching'" class="search-info text-mono">Recherche...</p>
          <p v-else-if="searchResult === 'found'" class="search-info success text-mono">✅ Station trouvée</p>
          <p v-else-if="searchResult === 'not_found'" class="search-info error text-mono">❌ Station introuvable</p>
        </div>

        <!-- Image actuelle (si trouvée) -->
        <div v-if="foundStation" class="current-image-section">
          <label class="text-mono">Image actuelle</label>
          <div class="current-image-preview">
            <img v-if="foundStation.favicon" :src="foundStation.favicon" alt="Image actuelle"
              class="current-img" />
            <div v-else class="no-image">
              <img :src="placeholderImg" alt="Station sans image" style="width: 100%; height: 100%; object-fit: cover;" />
            </div>
          </div>
        </div>

        <!-- Nouvelle image -->
        <div class="form-group">
          <label class="text-mono">Nouvelle image *</label>
          <div class="image-upload-container">
            <!-- Preview -->
            <div class="image-preview" :class="{ 'has-image': imagePreview || selectedFile }">
              <img v-if="imagePreview" :src="imagePreview" alt="Aperçu" class="preview-img" />
              <div v-else class="preview-placeholder">
                <span class="placeholder-icon">📷</span>
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

            <p class="file-info text-mono">JPG, PNG, WEBP, GIF - Max 10MB</p>
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
          <Button variant="primary" type="submit" :disabled="isSubmitting || !foundStation || !selectedFile">
            {{ isSubmitting ? 'Modification en cours...' : 'Changer l\'image' }}
          </Button>
        </div>
      </form>
  </section>
</template>

<script setup>
import { ref } from 'vue';
import { useRadioStore } from '@/stores/radioStore';
import Button from '@/components/ui/Button.vue';
import axios from 'axios';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const emit = defineEmits(['back', 'success']);
const radioStore = useRadioStore();

const fileInput = ref(null);
const stationName = ref('');
const foundStation = ref(null);
const searchResult = ref(null); // 'searching', 'found', 'not_found', null
const selectedFile = ref(null);
const imagePreview = ref(null);
const isSubmitting = ref(false);
const errorMessage = ref('');
const searchTimeout = ref(null);

async function searchStation() {
  if (!stationName.value.trim()) {
    foundStation.value = null;
    searchResult.value = null;
    return;
  }

  // Debounce search
  if (searchTimeout.value) {
    clearTimeout(searchTimeout.value);
  }

  searchResult.value = 'searching';

  searchTimeout.value = setTimeout(async () => {
    try {
      // Chercher dans les stations (favoris + custom)
      const response = await axios.get('/api/radio/stations', {
        params: {
          query: stationName.value.trim(),
          limit: 100
        }
      });

      const exactMatch = response.data.stations.find(
        s => s.name.toLowerCase() === stationName.value.trim().toLowerCase()
      );

      if (exactMatch) {
        foundStation.value = exactMatch;
        searchResult.value = 'found';
      } else {
        foundStation.value = null;
        searchResult.value = 'not_found';
      }
    } catch (error) {
      console.error('Erreur recherche station:', error);
      foundStation.value = null;
      searchResult.value = 'not_found';
    }
  }, 500);
}

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

  // Validate file size (10MB max)
  const maxSize = 10 * 1024 * 1024; // 10MB
  if (file.size > maxSize) {
    errorMessage.value = 'Image trop volumineuse. Maximum 10MB.';
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
  if (isSubmitting.value || !foundStation.value || !selectedFile.value) return;

  errorMessage.value = '';
  isSubmitting.value = true;

  try {
    const formData = new FormData();
    formData.append('station_id', foundStation.value.id);
    formData.append('image', selectedFile.value);

    const response = await axios.post('/api/radio/custom/update-image', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    });

    if (response.data.success) {
      console.log('✅ Image modifiée avec succès');
      emit('success', response.data.station);
      emit('back');
    } else {
      errorMessage.value = response.data.error || 'Échec de la modification';
    }
  } catch (error) {
    console.error('❌ Erreur modification image:', error);
    errorMessage.value = error.response?.data?.detail || error.message || 'Une erreur est survenue';
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

.change-image-form {
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

.search-info {
  font-size: var(--font-size-small);
  margin-top: var(--space-01);
}

.search-info.success {
  color: #4caf50;
}

.search-info.error {
  color: rgb(244, 67, 54);
}

/* Current Image */
.current-image-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.current-image-section label {
  color: var(--color-text-secondary);
}

.current-image-preview {
  width: 120px;
  height: 120px;
  border-radius: var(--radius-04);
  overflow: hidden;
  background: var(--color-background-neutral);
  border: 2px solid var(--color-background-glass);
  display: flex;
  align-items: center;
  justify-content: center;
}

.current-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.no-image {
  font-size: 48px;
  color: var(--color-text-secondary);
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
</style>
