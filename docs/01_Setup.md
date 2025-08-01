
# 01 · SETUP

## Étape 1: Préparer le système de base

1.  **Installer Raspberry Pi OS**

    ```bash
    # Téléchargez et flashez Raspberry Pi OS 64bit
    # Utilisez Raspberry Pi Imager: https://www.raspberrypi.com/software/
    # Pensez à activer le SSH
    ```
    
3.  **Premier démarrage et configuration**
    
    ```bash
    # Connectez-vous via ssh (
    sudo raspi-config
    
    # Configurez:
    # - Localization (langue, fuseau horaire, disposition du clavier)
    # - Interface Options: Enable I2C (pour l'écran tactile)
    # - Finish et reboot
    ```
    

## Étape 2: Installer les dépendances système

1.  **Mettre à jour le système**

    ```bash
    sudo apt update
    sudo apt upgrade -y
    ```
    
2.  **Installer les dépendances de base**

    ```bash
    sudo apt update
    sudo apt upgrade -y
    sudo apt install -y git python3-pip python3-venv python3-dev libasound2-dev libssl-dev \
    cmake build-essential pkg-config nodejs npm

	#Mettre à jour node et npm
    sudo npm install -g n
    sudo n stable
    sudo npm install -g npm@latest
    hash -r

	#Vérifier l'installation
	node -v
	npm -v
    ```


## Installation de l'AMP2 Hifiberry :

**Configuration audio pour Hifiberry AMP2**

```bash
sudo nano /boot/firmware/config.txt
```

Supprimer :
```bash
dtparam=audio=on
```
Ajouter ",noaudio" ou "audio=off" en fonction de "kms" ou "fkms" :
```bash
dtoverlay=vc4-fkms-v3d,audio=off
dtoverlay=vc4-kms-v3d,noaudio
```
Ajouter l'ampli audio Hifiberry pour qu'il soit détécté :
```bash
#AMP2
dtoverlay=hifiberry-dacplus-std
#AMP4 Pro
dtoverlay=hifiberry-amp4pro
```
Désactiver la limite de puissance par USB (inutile sur Rpi 4 et 5):
```bash
usb_max_current_enable=1
```
 
## Étape 3: Cloner et configurer Milo

1.  **Cloner le dépôt**

    ```bash
    cd ~
    git clone https://github.com/Leshauts/Milo.git
    cd Milo
    ```
    
2.  **Configurer l'environnement Python**

    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    deactivate
    ```
    
3.  **Compiler le frontend**

    ```bash
    cd frontend
    npm install
    npm run build
    cd ..
    ```

### Supprimer PulseAudio/PipeWire


Désactivez et supprimez PulseAudio/PipeWire pour avoir utiliser uniquement ALSA:
```bash
sudo apt remove pulseaudio pipewire
sudo apt autoremove
```

