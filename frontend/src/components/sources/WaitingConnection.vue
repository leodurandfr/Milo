<!-- Remplacer le contenu de frontend/src/components/sources/librespot/WaitingConnection.vue -->
<template>
  <div class="waiting-connection">
    <div class="waiting-content">
      <div class="source-logo" v-if="logoUrl">
        <img :src="logoUrl" :alt="sourceName + ' Logo'">
      </div>
      <div class="source-icon" v-else>
        <span>{{ sourceIcon }}</span>
      </div>
      <h3>{{ title }}</h3>
      <p>{{ message }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  // Type de source audio (librespot, bluetooth, macos, webradio)
  sourceType: {
    type: String,
    default: 'librespot'
  },
  // Optionnel: URL du logo à afficher
  customLogoUrl: {
    type: String,
    default: ''
  },
  // Optionnel: Titre personnalisé 
  customTitle: {
    type: String,
    default: ''
  },
  // Optionnel: Message personnalisé
  customMessage: {
    type: String,
    default: ''
  }
});

// Configuration par défaut pour chaque source
const sourceConfigs = {
  librespot: {
    name: 'Spotify',
    icon: '🎵',
    logo: 'https://storage.googleapis.com/pr-newsroom-wp/1/2018/11/Spotify_Logo_RGB_Green.png',
    title: 'En attente de connexion',
    message: 'Ouvrez l\'application Spotify sur votre appareil et sélectionnez "oakOS" comme périphérique de lecture'
  },
  bluetooth: {
    name: 'Bluetooth',
    icon: '📱',
    logo: '',
    title: 'En attente de connexion Bluetooth',
    message: 'Activez le Bluetooth sur votre appareil et connectez-vous à "oakOS"'
  },
  macos: {
    name: 'MacOS',
    icon: '💻',
    logo: '',
    title: 'En attente de connexion MacOS',
    message: 'Ouvrez les préférences audio sur votre Mac et sélectionnez "oakOS" comme périphérique de sortie'
  },
  webradio: {
    name: 'Web Radio',
    icon: '📻',
    logo: '',
    title: 'Sélectionnez une station',
    message: 'Choisissez une station de radio pour commencer l\'écoute'
  }
};

// Utiliser la configuration correspondante ou celle par défaut
const sourceConfig = computed(() => {
  return sourceConfigs[props.sourceType] || sourceConfigs.librespot;
});

// Nom de la source
const sourceName = computed(() => sourceConfig.value.name);

// Icône à utiliser si pas de logo
const sourceIcon = computed(() => sourceConfig.value.icon);

// URL du logo (personnalisé ou par défaut)
const logoUrl = computed(() => props.customLogoUrl || sourceConfig.value.logo);

// Titre (personnalisé ou par défaut)
const title = computed(() => props.customTitle || sourceConfig.value.title);

// Message (personnalisé ou par défaut)
const message = computed(() => props.customMessage || sourceConfig.value.message);
</script>

<style scoped>
.waiting-connection {
  background-color: #1E1E1E;
  border-radius: 10px;
  padding: 1.5rem;
  color: white;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  max-width: 500px;
  padding: 2rem;
}

.waiting-content {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.source-logo {
  width: 150px;
  margin-bottom: 1.5rem;
}

.source-logo img {
  width: 100%;
  height: auto;
}

.source-icon {
  font-size: 4rem;
  margin-bottom: 1.5rem;
  opacity: 0.8;
}

.waiting-connection h3 {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
}

.waiting-connection p {
  opacity: 0.8;
  max-width: 400px;
  margin: 0 auto 0.5rem;
  text-align: center;
}
</style>