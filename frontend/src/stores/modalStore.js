// frontend/src/stores/modalStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';

export const useModalStore = defineStore('modal', () => {
  // État des modales avec système de stack d'écrans
  const activeModal = ref(null); // 'snapcast', 'equalizer', null
  const screenStack = ref([]); // ['main'], ['main', 'settings'], ['main', 'client-details']
  const modalData = ref({}); // Données contextuelles (ex: client sélectionné)
  
  // === GETTERS ===
  
  const isSnapcastOpen = computed(() => activeModal.value === 'snapcast');
  const isEqualizerOpen = computed(() => activeModal.value === 'equalizer');
  const hasOpenModal = computed(() => activeModal.value !== null);
  
  // Écran actuel de la modale active
  const currentScreen = computed(() => {
    if (screenStack.value.length === 0) return 'main';
    return screenStack.value[screenStack.value.length - 1];
  });
  
  // Savoir s'il y a un écran précédent (pour afficher le bouton Back)
  const canGoBack = computed(() => screenStack.value.length > 1);
  
  // Titre dynamique selon l'écran actuel
  const currentTitle = computed(() => {
    if (!hasOpenModal.value) return '';
    
    const modal = activeModal.value;
    const screen = currentScreen.value;
    
    const titles = {
      snapcast: {
        main: 'Multiroom Control',
        settings: 'Configuration Snapcast', 
        'client-details': 'Détails du Client'
      },
      equalizer: {
        main: 'Equalizer',
        settings: 'Configuration Audio' // Si besoin plus tard
      }
    };
    
    return titles[modal]?.[screen] || '';
  });
  
  // === ACTIONS PRINCIPALES ===
  
  function openSnapcast() {
    closeAll();
    activeModal.value = 'snapcast';
    screenStack.value = ['main'];
    modalData.value = {};
  }
  
  function openEqualizer() {
    closeAll();
    activeModal.value = 'equalizer';
    screenStack.value = ['main'];
    modalData.value = {};
  }
  
  function closeAll() {
    activeModal.value = null;
    screenStack.value = [];
    modalData.value = {};
  }
  
  // === NAVIGATION ENTRE ÉCRANS ===
  
  function pushScreen(screenName, data = {}) {
    if (!hasOpenModal.value) return;
    
    screenStack.value.push(screenName);
    modalData.value = { ...modalData.value, ...data };
  }
  
  function goBack() {
    if (screenStack.value.length <= 1) return;
    
    screenStack.value.pop();
    
    // Nettoyer les données spécifiques à l'écran quitté
    if (currentScreen.value === 'main') {
      modalData.value = {};
    }
  }
  
  function goToScreen(screenName, data = {}) {
    if (!hasOpenModal.value) return;
    
    // Reset au main puis push vers l'écran demandé
    screenStack.value = ['main'];
    if (screenName !== 'main') {
      screenStack.value.push(screenName);
    }
    modalData.value = { ...modalData.value, ...data };
  }
  
  // === ACTIONS SPÉCIFIQUES SNAPCAST ===
  
  function openSnapcastSettings() {
    pushScreen('settings');
  }
  
  function openClientDetails(client) {
    pushScreen('client-details', { selectedClient: client });
  }
  
  // === GETTERS DONNÉES ===
  
  const selectedClient = computed(() => modalData.value.selectedClient || null);
  
  return {
    // État
    activeModal,
    screenStack,
    modalData,
    
    // Getters principaux
    isSnapcastOpen,
    isEqualizerOpen,
    hasOpenModal,
    currentScreen,
    canGoBack,
    currentTitle,
    
    // Getters données
    selectedClient,
    
    // Actions principales
    openSnapcast,
    openEqualizer,
    closeAll,
    
    // Navigation
    pushScreen,
    goBack,
    goToScreen,
    
    // Actions spécifiques
    openSnapcastSettings,
    openClientDetails
  };
});