# 04 · Installation "bluez-alsa"

## Plan d'installation et d'intégration optimisée pour Milo

### 1. Installation optimisée de bluez-alsa

```bash
# Installation alternatives
sudo apt install -y \
  libasound2-dev \
  libbluetooth-dev \
  libdbus-1-dev \
  libglib2.0-dev \
  libsbc-dev \
  bluez \
  bluez-tools \
  pkg-config \
  build-essential \
  autotools-dev \
  automake \
  libtool

#Important de reboot
sudo reboot
```

```bash
# Cloner et installer bluez-alsa
cd ~/Milo
git clone https://github.com/arkq/bluez-alsa.git
cd bluez-alsa
git checkout v4.3.1

# Générer les scripts de configuration
autoreconf --install

# Créer le répertoire de build
mkdir build && cd build

# Configuration optimisée pour Milo (sans AAC)
../configure --prefix=/usr --enable-systemd \
  --with-alsaplugindir=/usr/lib/aarch64-linux-gnu/alsa-lib \
  --with-bluealsauser=$USER --with-bluealsaaplayuser=$USER \
  --enable-cli

# Compilation
make -j$(nproc)

# Installation
sudo make install
sudo ldconfig

# Supprimer les fichiers d'installation
rm -rf ~/Milo/bluez-alsa
```



### 2. Arrêter et désactiver les .service par defaut liés au bluetooth

```bash
sudo systemctl stop bluealsa-aplay.service
sudo systemctl stop bluealsa.service
sudo systemctl disable bluealsa-aplay.service
sudo systemctl disable bluealsa.service
```



### 2. Si on utilise un dongle usb Bluetooth (normalement pas utile si Raspberry pi 5)

Désactiver le bluetooth intégré au raspberry :

```bash
sudo nano /boot/firmware/config.txt
```
Ajouter sous [all]
```bash
dtoverlay=disable-bt
```

Vérifier si bluetooth est bloqué
```bash
sudo hciconfig hci0 up
# Vérfier quand le plugin "bluetooth" est actif, doit afficher : UP RUNNING SCAN (et non "DOWN")
hciconfig

# Débloquer le bluetooth USB
sudo rfkill unblock bluetooth
# Vérifier que tout doit est sur "no"
rfkill list
```




