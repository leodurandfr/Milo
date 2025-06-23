<template>
    <div class="now-playing-info">
      <div class="album-art">
        <img v-if="albumArtUrl" :src="albumArtUrl" alt="Album Art" />
        <div v-else class="placeholder-art">
          <span class="music-icon">🎵</span>
        </div>
      </div>
  
      <div class="track-info">
        <h3 class="track-title">{{ title || 'Titre inconnu' }}</h3>
        <p class="track-artist">{{ artist || 'Artiste inconnu' }}</p>
        <p class="track-album" v-if="album">{{ album }}</p>
      </div>
    </div>
  </template>
  
  <script setup>
  const props = defineProps({
    title: {
      type: String,
      default: ''
    },
    artist: {
      type: String,
      default: ''
    },
    album: {
      type: String,
      default: ''
    },
    albumArtUrl: {
      type: String,
      default: ''
    }
  });
  </script>
  
  <style scoped>
  .now-playing-info {
    width: 100%;
    display: flex;
    flex-direction: row;
    align-items: center;
  }
  
  .album-art {
    margin-bottom: 1.5rem;
    box-shadow: 0 8px 16px rgba(0, 0, 0, 0.3);
    border-radius: 5px;
    overflow: hidden;
  }
  
  .album-art img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display:block;
  }
  
  .placeholder-art {
    width: 100%;
    height: 100%;
    background-color: #333;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  
  .music-icon {
    font-size: 80px;
    opacity: 0.5;
  }
  
  .track-info {
    text-align: center;
    margin-bottom: 1.5rem;
    width: 100%;
  }
  
  .track-title {
    font-size: 1.5rem;
    font-weight: bold;
    margin-bottom: 0.5rem;
  }
  
  .track-artist {
    font-size: 1.2rem;
    opacity: 0.8;
    margin-bottom: 0.3rem;
  }
  
  .track-album {
    font-size: 1rem;
    opacity: 0.6;
  }
  </style>