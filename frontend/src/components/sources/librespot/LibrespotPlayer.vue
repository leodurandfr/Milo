<template>
    <div class="librespot-player">
        <!-- Affichage quand librespot est actif et qu'un périphérique est connecté avec métadonnées -->
        <div class="now-playing" v-if="deviceConnected && hasTrackInfo">
            <!-- Infos sur la piste en cours -->
            <NowPlayingInfo :title="metadata.title" :artist="metadata.artist" :album="metadata.album"
                :albumArtUrl="metadata.album_art_url" />

            <!-- Barre de progression -->
            <ProgressBar :currentPosition="currentPosition" :duration="metadata.duration_ms"
                :progressPercentage="progressPercentage" @seek="seekToPosition" />

            <!-- Contrôles de lecture -->
            <PlaybackControls :isPlaying="metadata.is_playing" @play-pause="togglePlayPause" @previous="previousTrack"
                @next="nextTrack" />
        </div>

        <!-- Affichage quand aucun périphérique n'est connecté OU pas de métadonnées -->
        <WaitingConnection v-else sourceType="librespot" />
        <!-- Panneau de débogage (affiché uniquement quand showDebugInfo est true) -->
        <DebugPanel v-if="showDebugInfo" :statusResult="statusResult" :metadata="metadata"
            :currentPosition="currentPosition" :progressPercentage="progressPercentage"
            :isPlaying="audioStore.isPlaying" :isActuallyConnected="isActuallyConnected"
            :deviceConnected="deviceConnected" :hasTrackInfo="hasTrackInfo" :isDisconnected="audioStore.isDisconnected"
            :manualConnectionStatus="manualConnectionStatus" :connectionLastChecked="connectionLastChecked"
            :lastMessages="lastMessages" @check-status="checkStatus" @refresh-metadata="forceRefreshMetadata"
            @toggle-connection="toggleConnectionStatus" @reset-connection="resetConnectionStatus" />

        <!-- Bouton pour afficher/masquer le débogage -->
        <button @click="toggleDebugInfo" class="debug-toggle-button">
            {{ showDebugInfo ? 'Masquer debug' : 'Afficher debug' }}
        </button>
    </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted, watch } from 'vue';
import { useAudioStore } from '@/stores/index';
import { useLibrespotControl } from '@/composables/useLibrespotControl';
import { usePlaybackProgress } from '@/composables/usePlaybackProgress';
import axios from 'axios';

// Composants
import NowPlayingInfo from './NowPlayingInfo.vue';
import PlaybackControls from './PlaybackControls.vue';
import ProgressBar from './ProgressBar.vue';
import WaitingConnection from './../WaitingConnection.vue';
import DebugPanel from './DebugPanel.vue';

const audioStore = useAudioStore();
const { togglePlayPause, previousTrack, nextTrack, seekTo: controlSeekTo } = useLibrespotControl();
const { currentPosition, progressPercentage, seekTo: trackingSeekTo } = usePlaybackProgress();

// État local
const showDebugInfo = ref(false);
const statusResult = ref(null);
const lastMessages = ref([]);
const debugCheckInterval = ref(null);
const connectionCheckInterval = ref(null);
const manualConnectionStatus = ref(null);
const connectionLastChecked = ref(Date.now());
const connectionTimeoutMs = 20000;

// État pour stocker si nous sommes réellement connectés
const isActuallyConnected = ref(false);

const metadata = computed(() => {
    return audioStore.metadata || {};
});

// Vérifier si un appareil est connecté (connexion Spotify établie)
const deviceConnected = computed(() => {
    return !audioStore.isDisconnected;
});

// Vérifier si on a des informations sur la piste en cours
const hasTrackInfo = computed(() => {
    const hasCurrent = metadata.value &&
        metadata.value.title &&
        metadata.value.artist;

    const isConnectedWithoutMetadata = !audioStore.isDisconnected &&
        statusResult.value &&
        statusResult.value.device_connected === true &&
        !hasCurrent;

    if (isConnectedWithoutMetadata &&
        statusResult.value?.raw_status?.track?.name) {

        const track = statusResult.value.raw_status.track;

        audioStore.updateMetadataFromStatus({
            title: track.name,
            artist: track.artist_names?.join(', ') || 'Artiste inconnu',
            album: track.album_name || 'Album inconnu',
            album_art_url: track.album_cover_url,
            duration_ms: track.duration,
            position_ms: track.position,
            is_playing: !statusResult.value.raw_status.paused,
            connected: true,
            deviceConnected: true,
            username: statusResult.value.raw_status.username,
            device_name: statusResult.value.raw_status.device_name
        });

        return true;
    }

    return hasCurrent && !audioStore.isDisconnected;
});

// Surveiller l'état de déconnexion dans le store
watch(() => audioStore.isDisconnected, (newValue) => {
    if (newValue === true) {
        isActuallyConnected.value = false;
    }
});

// Surveiller les changements d'état de connexion
watch(() => [
    metadata.value?.deviceConnected,
    metadata.value?.connected,
    metadata.value?.is_playing
],
    (newValues, oldValues) => {
        if (!newValues || !oldValues) return;

        const [newDeviceConnected, newConnected, newIsPlaying] = newValues || [false, false, false];

        if (audioStore.isDisconnected) {
            isActuallyConnected.value = false;
            return;
        }

        if (newDeviceConnected || newConnected || newIsPlaying) {
            if (Object.keys(metadata.value).length > 0 && !audioStore.isDisconnected) {
                isActuallyConnected.value = true;
                connectionLastChecked.value = Date.now();
            } else {
                checkStatus();
            }
        }

        if (isActuallyConnected.value &&
            !newDeviceConnected && !newConnected && !newIsPlaying) {
            checkStatus();
        }

        if (manualConnectionStatus.value !== null) {
            isActuallyConnected.value = manualConnectionStatus.value;
        }
    }
);

function toggleDebugInfo() {
    showDebugInfo.value = !showDebugInfo.value;
}

function toggleConnectionStatus() {
    if (manualConnectionStatus.value === null) {
        manualConnectionStatus.value = !deviceConnected.value;
    } else {
        manualConnectionStatus.value = !manualConnectionStatus.value;
    }
    console.log("État de connexion forcé à:", manualConnectionStatus.value);
}

function resetConnectionStatus() {
    manualConnectionStatus.value = null;
    console.log("État de connexion réinitialisé (autodétection)");
}

async function checkStatus() {
    try {
        const isConnected = await audioStore.checkConnectionStatus();
        isActuallyConnected.value = isConnected;

        if (isConnected) {
            connectionLastChecked.value = Date.now();
        }

        addDebugMessage("api_connection_check", { connected: isConnected });

        if (showDebugInfo.value) {
            const response = await axios.get('/api/librespot/status');
            statusResult.value = response.data;
            addDebugMessage("status_check", statusResult.value);
        }

        return isConnected;
    } catch (error) {
        console.error("Error checking status:", error);

        if (showDebugInfo.value) {
            statusResult.value = { error: error.message };
            addDebugMessage("status_check_error", { error: error.message });
        }

        isActuallyConnected.value = false;
        return false;
    }
}

function addDebugMessage(type, data) {
    const now = new Date();
    const timeString = now.toLocaleTimeString();

    lastMessages.value.unshift({
        timestamp: timeString,
        type: type,
        data: data
    });

    if (lastMessages.value.length > 5) {
        lastMessages.value.pop();
    }
}

async function forceRefreshMetadata() {
    try {
        const isConnected = await checkStatus();

        if (isConnected) {
            await audioStore.controlSource('librespot', 'refresh_metadata');
            addDebugMessage("refresh_metadata", { timestamp: new Date().toISOString() });
        } else {
            audioStore.clearMetadata();
            addDebugMessage("metadata_cleared", { reason: "déconnecté" });
        }
    } catch (error) {
        console.error("Error refreshing metadata:", error);
        addDebugMessage("refresh_error", { error: error.message });
    }
}

function checkConnectionTimeout() {
    const timeSinceLastCheck = Date.now() - connectionLastChecked.value;

    if (isActuallyConnected.value && timeSinceLastCheck > connectionTimeoutMs) {
        console.log(`Délai de connexion dépassé (${timeSinceLastCheck}ms), vérification...`);
        checkStatus();
    }

    if (!isActuallyConnected.value && timeSinceLastCheck > connectionTimeoutMs * 2) {
        console.log("Vérification périodique de l'état de connexion...");
        checkStatus();
        connectionLastChecked.value = Date.now();
    }
}

function seekToPosition(position) {
    trackingSeekTo(position);
    controlSeekTo(position);
}

onMounted(() => {
    setTimeout(() => {
        checkStatus().then(isConnected => {
            if (isConnected) {
                forceRefreshMetadata();
            }
        });
    }, 500);

    debugCheckInterval.value = setInterval(() => {
        if (showDebugInfo.value) {
            checkStatus();
        }
    }, 30000);

    connectionCheckInterval.value = setInterval(() => {
        checkConnectionTimeout();
    }, 15000);
});

onUnmounted(() => {
    if (debugCheckInterval.value) {
        clearInterval(debugCheckInterval.value);
    }

    if (connectionCheckInterval.value) {
        clearInterval(connectionCheckInterval.value);
    }

    manualConnectionStatus.value = null;
});
</script>

<style scoped>
.librespot-player {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
}

.now-playing {
    background-color: #1E1E1E;
    border-radius: 10px;
    padding: 1.5rem;
    color: white;
    display: flex;
    flex-direction: column;
    align-items: center;
    width: 100%;
    max-width: 500px;
}

.debug-toggle-button {
    margin-top: 1rem;
    background-color: #333;
    color: white;
    border: none;
    padding: 0.4rem 0.8rem;
    border-radius: 4px;
    font-size: 0.8rem;
    cursor: pointer;
}
</style>