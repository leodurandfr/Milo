# 02 · Installation "go-librespot"

## Guide d'installation go-librespot

### 1. Installation des prérequis
```bash
# Installation des dépendances nécessaires
sudo apt-get install -y libogg-dev libvorbis-dev libasound2-dev
```

### 2. Préparation de l'environnement
```bash
# Créer un dossier temporaire pour le téléchargement
mkdir -p ~/temp/go-librespot
cd ~/temp/go-librespot

# Télécharger go-librespot
wget https://github.com/devgianlu/go-librespot/releases/download/v0.2.0/go-librespot_linux_arm64.tar.gz

# Décompresser l'archive
tar -xvzf go-librespot_linux_arm64.tar.gz

# Déplacer l'exécutable dans /usr/local/bin pour une installation système
sudo cp go-librespot /usr/local/bin/
sudo chmod +x /usr/local/bin/go-librespot

# Créer le répertoire de configuration pour le service
sudo mkdir -p /var/lib/milo/go-librespot
# Nécessaire pour accéder au fichier de configuration
sudo chown -R milo:audio /var/lib/milo/go-librespot
```

### 3. Configuration de go-librespot

Créer le fichier configuration de go-librespot :
```bash
# Créer le fichier de configuration principal
sudo tee /var/lib/milo/go-librespot/config.yml > /dev/null << 'EOF'
# Configuration Spotify Connect
device_name: "Milo-v0.2"
device_type: "speaker"
bitrate: 320

# Configuration Audio > ALSA
audio_backend: "alsa"
audio_device: "milo_spotify"

# Désactive le contrôle du volume via les applications Spotify
external_volume: true

# API
server:
  enabled: true
  address: "0.0.0.0"
  port: 3678
  allow_origin: "*"
EOF
```

**Nettoyer les fichiers d'installation :**
```bash
# Nettoyer les fichiers temporaires
cd ~
rm -rf ~/temp
```


