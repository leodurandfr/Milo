<template>
    <div class="waiting-connection">
      <div class="waiting-content">
        <div class="spotify-logo">
          <img src="https://storage.googleapis.com/pr-newsroom-wp/1/2018/11/Spotify_Logo_RGB_Green.png"
            alt="Spotify Logo">
        </div>
        <h3>En attente de connexion</h3>
        <p>Ouvrez l'application Spotify sur votre appareil et sélectionnez "oakOS" comme périphérique de lecture</p>
      </div>
    </div>
  </template>
  
  <script setup>
  // Pas de logique spécifique
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
  
  .spotify-logo {
    width: 150px;
    margin-bottom: 1.5rem;
  }
  
  .spotify-logo img {
    width: 100%;
    height: auto;
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