


# 05 · Installation de Snapcast

### 1. Téléchargement des packages v0.31.0

```bash
# Créer un dossier temporaire
mkdir -p ~/snapcast-install
cd ~/snapcast-install

# Télécharger snapserver
wget https://github.com/badaix/snapcast/releases/download/v0.31.0/snapserver_0.31.0-1_arm64_bookworm.deb

# Télécharger snapclient  
wget https://github.com/badaix/snapcast/releases/download/v0.31.0/snapclient_0.31.0-1_arm64_bookworm.deb

```

### 2. Installation

```bash
# Installer snapserver
sudo apt install ./snapserver_0.31.0-1_arm64_bookworm.deb

# Installer snapclient
sudo apt install ./snapclient_0.31.0-1_arm64_bookworm.deb

# Supprimer les fichiers téléchargés
rm -rf ~/snapcast-install

# Vérifier les versions installées
snapserver --version
snapclient --version
```


### 3. Arrêter et désactiver les .service par defaut de "snapcast"
```bash
sudo systemctl stop snapserver.service
sudo systemctl disable snapserver.service

sudo systemctl stop snapclient.service
sudo systemctl disable snapclient.service

```