/**
 * Service pour gérer la progression de lecture en temps réel
 * Ce service complète le store audio en calculant localement la position
 * entre les mises à jour WebSocket
 */
import { ref, computed, watch, onMounted, onUnmounted, watchEffect } from 'vue';
import { useAudioStore } from '@/stores/audio';

export function usePlaybackProgress() {
  const audioStore = useAudioStore();
  
  // Variables locales pour le suivi de progression
  const playbackStartTime = ref(null);     // Timestamp de début de lecture
  const clientStartPosition = ref(0);      // Position au moment du début de lecture
  const refreshInterval = ref(null);       // Intervalle de rafraîchissement de l'UI
  const lastSeekTimestamp = ref(0);        // Timestamp du dernier seek reçu
  const forceActivePlaying = ref(false);   // Forcer l'état de lecture actif
  const currentPositionMs = ref(0);        // Position actuelle en ms (mise à jour activement)
  const progressPercentageValue = ref(0);  // Pourcentage de progression (mis à jour activement)
  const lastUpdateTime = ref(Date.now());  // Dernier moment où la position a été mise à jour
  const lastLogTime = ref(0);              // Pour limiter les logs
  
  // Fonction pour mettre à jour la position actuelle et forcer la réactivité
  function updateCurrentPosition() {
    if (!playbackStartTime.value) {
      currentPositionMs.value = clientStartPosition.value;
      return;
    }
    
    const now = Date.now();
    const elapsed = now - playbackStartTime.value;
    
    // Calcul de la nouvelle position
    const newPosition = clientStartPosition.value + elapsed;
    
    // Limite pour ne pas dépasser la durée totale
    const duration = audioStore.metadata?.duration_ms || Infinity;
    currentPositionMs.value = Math.min(newPosition, duration);
    
    // Calcul du pourcentage pour la barre de progression
    if (duration && duration > 0) {
      progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
    } else {
      progressPercentageValue.value = 0;
    }
    
    // Mettre à jour le timestamp de dernière mise à jour
    lastUpdateTime.value = now;
    
    // Logs limités à chaque 10 secondes
    if (now - lastLogTime.value > 10000) {
      console.log(`Position: ${Math.floor(currentPositionMs.value/1000)}s (${progressPercentageValue.value.toFixed(1)}%)`);
      lastLogTime.value = now;
    }
  }
  
  // Computed properties exposées (utilisant les refs actives)
  const currentPosition = computed(() => currentPositionMs.value);
  const progressPercentage = computed(() => progressPercentageValue.value);
  
  // Vérifier si la lecture est réellement active
  const isActuallyPlaying = computed(() => {
    // Si force active est défini, prioritaire sur le reste
    if (forceActivePlaying.value) {
      return true;
    }
    
    // Récupérer l'état de lecture du store
    const storeIsPlaying = audioStore.isPlaying;
    
    // Si nous avons reçu un seek récemment (dans les 10 dernières secondes),
    // considérer que nous sommes en lecture
    const recentSeek = (Date.now() - lastSeekTimestamp.value) < 10000;
    
    // Si métadonnées indiquent explicitement lecture en cours
    const metadataPlaying = audioStore.metadata?.is_playing === true;
    
    return storeIsPlaying || recentSeek || metadataPlaying;
  });
  
  // Surveiller l'état de lecture pour démarrer/arrêter le suivi
  watch(isActuallyPlaying, (isPlaying) => {
    console.log(`État de lecture changé: ${isPlaying}`);
    
    if (isPlaying) {
      startProgressTracking();
    } else {
      // Délai pour éviter d'arrêter trop tôt en cas de transition
      setTimeout(() => {
        if (!isActuallyPlaying.value) {
          stopProgressTracking();
        }
      }, 1000);
    }
  });
  
  // Surveiller les changements de position dans les métadonnées
  watch(() => audioStore.metadata?.position_ms, (newPosition) => {
    if (newPosition !== undefined && newPosition !== null) {
      console.log(`Nouvelle position reçue: ${newPosition}ms`);
      
      // Mise à jour de la position de référence
      clientStartPosition.value = newPosition;
      currentPositionMs.value = newPosition;
      
      // Réinitialiser le temps de début pour un calcul précis
      if (isActuallyPlaying.value) {
        playbackStartTime.value = Date.now();
        
        // S'assurer que le suivi est actif
        if (!refreshInterval.value) {
          startProgressTracking();
        }
      }
    }
  });
  
  // Surveiller le changement de piste pour réinitialiser le suivi
  watch(() => audioStore.metadata?.title, (newTitle, oldTitle) => {
    if (newTitle && newTitle !== oldTitle) {
      console.log(`Nouvelle piste: "${newTitle}"`);
      
      // Réinitialiser la position au début ou à la position indiquée
      if (audioStore.metadata?.position_ms !== undefined) {
        clientStartPosition.value = audioStore.metadata.position_ms;
      } else {
        clientStartPosition.value = 0;
      }
      
      currentPositionMs.value = clientStartPosition.value;
      
      // Redémarrer le suivi si on est en lecture
      if (isActuallyPlaying.value) {
        stopProgressTracking();
        startProgressTracking();
      }
    }
  });
  
  // Démarrer le suivi de progression
  function startProgressTracking() {
    // Éviter les démarrages multiples
    if (refreshInterval.value) {
      return;
    }
    
    console.log('Démarrage du suivi de progression');
    
    // Initialiser le temps de début
    playbackStartTime.value = Date.now();
    
    // Utiliser la position du store ou la dernière position connue
    if (audioStore.metadata?.position_ms !== undefined) {
      clientStartPosition.value = audioStore.metadata.position_ms;
      currentPositionMs.value = clientStartPosition.value;
    }
    
    // Créer un nouvel intervalle (250ms = 4 fois par seconde, bon équilibre performance/fluidité)
    refreshInterval.value = setInterval(() => {
      if (isActuallyPlaying.value) {
        updateCurrentPosition();
      }
    }, 250);
    
    // Mettre à jour immédiatement
    updateCurrentPosition();
  }
  
  // Arrêter le suivi de progression
  function stopProgressTracking() {
    // Éviter les arrêts multiples
    if (!refreshInterval.value) {
      return;
    }
    
    console.log('Arrêt du suivi de progression');
    
    // Sauvegarder la position actuelle
    updateCurrentPosition();
    clientStartPosition.value = currentPositionMs.value;
    
    // Arrêter l'intervalle
    clearInterval(refreshInterval.value);
    refreshInterval.value = null;
    
    // Réinitialiser le temps de début
    playbackStartTime.value = null;
    
    // Réinitialiser l'état de force active
    forceActivePlaying.value = false;
  }
  
  // Forcer une position spécifique (par exemple après un seek manuel)
  function seekTo(position) {
    console.log(`Seek manuel à ${position}ms`);
    
    // Enregistrer le timestamp du seek
    lastSeekTimestamp.value = Date.now();
    
    // Mettre à jour la position localement
    clientStartPosition.value = position;
    currentPositionMs.value = position;
    
    // Réinitialiser le temps de début
    playbackStartTime.value = Date.now();
    
    // Forcer la lecture active
    forceActivePlaying.value = true;
    
    // S'assurer que le suivi est actif
    if (!refreshInterval.value) {
      startProgressTracking();
    } else {
      // Mise à jour immédiate
      updateCurrentPosition();
    }
    
    // Envoyer la commande au backend
    audioStore.controlSource('librespot', 'seek', { position_ms: position });
    
    // Désactiver l'état forcé après un certain temps
    setTimeout(() => {
      forceActivePlaying.value = false;
    }, 5000);
  }
  
  // Gérer les événements de seek reçus (par ex. depuis l'app Spotify officielle)
  function handleSeekEvent(event) {
    console.log('Événement audio-seek reçu:', event.detail);
    
    const { position, timestamp, source } = event.detail;
    
    // Vérifier que la position est valide et que l'événement concerne notre source
    if (position !== undefined && (!source || source === 'librespot')) {
      // Mettre à jour la position
      clientStartPosition.value = position;
      currentPositionMs.value = position;
      
      // Réinitialiser le temps de début
      playbackStartTime.value = timestamp || Date.now();
      
      // Enregistrer le timestamp du seek
      lastSeekTimestamp.value = Date.now();
      
      // Forcer l'état de lecture actif
      forceActivePlaying.value = true;
      
      // S'assurer que le suivi est actif
      if (!refreshInterval.value) {
        startProgressTracking();
      } else {
        // Mise à jour immédiate
        updateCurrentPosition();
      }
      
      console.log(`Seek traité: position=${position}ms`);
      
      // Désactiver l'état forcé après un certain temps
      setTimeout(() => {
        forceActivePlaying.value = false;
      }, 5000);
    }
  }
  
  // Initialiser le suivi au montage du composant
  onMounted(() => {
    console.log('Montage du composant de suivi de progression');
    
    // Nettoyer d'abord les écouteurs existants pour éviter les doublons
    window.removeEventListener('audio-seek', handleSeekEvent);
    
    // Ajouter l'écouteur d'événements
    window.addEventListener('audio-seek', handleSeekEvent);
    
    // Initialiser les valeurs à partir des métadonnées si disponibles
    if (audioStore.metadata?.position_ms !== undefined) {
      clientStartPosition.value = audioStore.metadata.position_ms;
      currentPositionMs.value = clientStartPosition.value;
    }
    
    // Démarrer le suivi si la lecture est active
    if (isActuallyPlaying.value) {
      // Petit délai pour s'assurer que tout est bien initialisé
      setTimeout(() => {
        startProgressTracking();
      }, 100);
    }
    
    // Créer un watchEffect pour détecter les incohérences (moins agressif)
    watchEffect(() => {
      const now = Date.now();
      
      // Si on est supposé être en lecture mais que la position n'a pas été mise à jour depuis longtemps
      // (5 secondes au lieu de 2 pour réduire les faux positifs)
      if (isActuallyPlaying.value && refreshInterval.value && 
          (now - lastUpdateTime.value > 5000)) {
        console.warn("Détection d'incohérence: lecture active mais position non mise à jour");
        // Forcer le redémarrage du suivi
        stopProgressTracking();
        startProgressTracking();
      }
    });
    
    // Forcer une première mise à jour
    updateCurrentPosition();
  });
  
  // Nettoyer les ressources au démontage du composant
  onUnmounted(() => {
    console.log('Démontage du composant de suivi de progression');
    
    // Arrêter l'intervalle
    if (refreshInterval.value) {
      clearInterval(refreshInterval.value);
      refreshInterval.value = null;
    }
    
    // Supprimer l'écouteur d'événements
    window.removeEventListener('audio-seek', handleSeekEvent);
  });
  
  return {
    currentPosition,
    progressPercentage,
    isActuallyPlaying,
    seekTo,
    startProgressTracking,
    stopProgressTracking
  };
}