// frontend/src/stores/modalStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { watch } from 'vue';


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
  
  // Titre dynamique selon l'écran actuel (seulement pour les sous-écrans)
  const currentTitle = computed(() => {
    if (!hasOpenModal.value || !canGoBack.value) return '';
    
    const modal = activeModal.value;
    const screen = currentScreen.value;
    
    const titles = {
      snapcast: {
        settings: 'Configuration Multiroom', 
        'client-details': 'Détails du Client'
      }
    };
    
    return titles[modal]?.[screen] || '';
  });
  
  // === ACTIONS PRINCIPALES ===
  
  function openSnapcast() {
    console.log('📂 Opening Snapcast modal');
    closeAll();
    activeModal.value = 'snapcast';
    screenStack.value = ['main'];
    modalData.value = {};
  }
  
  function openEqualizer() {
    console.log('📂 Opening Equalizer modal');
    closeAll();
    activeModal.value = 'equalizer';
    screenStack.value = ['main'];
    modalData.value = {};
  }
  
  function closeAll() {
    console.log('❌ Closing all modals');
    activeModal.value = null;
    screenStack.value = [];
    modalData.value = {};
  }
  
  // === NAVIGATION ENTRE ÉCRANS ===
  
  function pushScreen(screenName, data = {}) {
    if (!hasOpenModal.value) {
      console.warn('⚠️ Cannot push screen: no modal open');
      return;
    }
    
    console.log(`📱 Pushing screen: ${screenName}`);
    screenStack.value.push(screenName);
    modalData.value = { ...modalData.value, ...data };
    
    console.log(`📱 Screen stack: [${screenStack.value.join(', ')}]`);
  }
  
  function goBack() {
    if (screenStack.value.length <= 1) {
      console.warn('⚠️ Cannot go back: already at root screen');
      return;
    }
    
    const currentScreenName = currentScreen.value;
    screenStack.value.pop();
    const newScreenName = currentScreen.value;
    
    console.log(`⬅️ Going back: ${currentScreenName} → ${newScreenName}`);
    
    // Nettoyer les données spécifiques à l'écran quitté
    if (currentScreenName === 'client-details') {
      // Garder les autres données mais supprimer selectedClient
      const { selectedClient, ...otherData } = modalData.value;
      modalData.value = otherData;
    }
    
    console.log(`📱 Screen stack: [${screenStack.value.join(', ')}]`);
  }
  
  function goToScreen(screenName, data = {}) {
    if (!hasOpenModal.value) {
      console.warn('⚠️ Cannot go to screen: no modal open');
      return;
    }
    
    console.log(`🎯 Going to screen: ${screenName}`);
    
    // Reset au main puis push vers l'écran demandé
    screenStack.value = ['main'];
    if (screenName !== 'main') {
      screenStack.value.push(screenName);
    }
    modalData.value = { ...modalData.value, ...data };
    
    console.log(`📱 Screen stack: [${screenStack.value.join(', ')}]`);
  }
  
  // === ACTIONS SPÉCIFIQUES SNAPCAST ===
  
  function openSnapcastSettings() {
    console.log('⚙️ Opening Snapcast settings');
    pushScreen('settings');
  }
  
  function openClientDetails(client) {
    if (!client) {
      console.error('❌ Cannot open client details: no client provided');
      return;
    }
    
    console.log('👤 Opening client details for:', client.name || client.id);
    pushScreen('client-details', { selectedClient: client });
  }
  
  // === GETTERS DONNÉES ===
  
  const selectedClient = computed(() => modalData.value.selectedClient || null);
  
  // Debug computed pour surveiller les changements
  const debugInfo = computed(() => ({
    activeModal: activeModal.value,
    currentScreen: currentScreen.value,
    screenStack: [...screenStack.value],
    canGoBack: canGoBack.value,
    selectedClient: selectedClient.value?.name || null
  }));
  
  // Watcher pour debug
  watch(debugInfo, (newInfo) => {
    console.log('🔍 Modal Store Debug:', newInfo);
  }, { deep: true });
  
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
    openClientDetails,
    
    // Debug
    debugInfo
  };
});