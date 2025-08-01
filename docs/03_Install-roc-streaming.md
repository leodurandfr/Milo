# 03 · Installation "roc-toolkit"

## Installation sur Raspberry

### 1. Installation des prérequis
```bash
# Installation des dépendances nécessaires
sudo apt install -y g++ pkg-config scons ragel gengetopt libuv1-dev \
  libspeexdsp-dev libunwind-dev libsox-dev libsndfile1-dev libssl-dev libasound2-dev \
  libtool intltool autoconf automake make cmake avahi-utils libpulse-dev
```

### 2. Compilation et installation


```bash
cd ~/Milo
git clone https://github.com/roc-streaming/roc-toolkit.git
cd roc-toolkit
scons -Q --build-3rdparty=openfec
sudo scons -Q --build-3rdparty=openfec install
sudo ldconfig
```

```bash
# Supprimer les fichiers d'installation
rm -rf ~/Milo/roc-toolkit
```

### 3. Vérification
```bash
roc-recv --version
```



## Mac

### 1. Installation de roc-vad
```bash
sudo /bin/bash -c \
  "$(curl -fsSL https://raw.githubusercontent.com/roc-streaming/roc-vad/HEAD/install.sh)"
```


### 2. Création le dispositif virtuel
```bash
roc-vad device add sender --name "Milo"
```

### 3. Récuperer l'ID du dispositif virtuel
```bash
roc-vad device list
```

### 4. Associer le dispositif virtuel avec l'ip du raspberry 
```bash
#Si "device list" affiche "1" pour le device virtuel et ajouter l'IP du Raspberry PI.
roc-vad device connect 1 \
   --source rtp+rs8m://milo.local:10001 \
   --repair rs8m://milo.local:10002 \
   --control rtcp://milo.local:10003
```

### 4. Autres : gestion des dispositifs Mac
```bash
# Lister les dispositifs
roc-vad device list

# Voir les détails d'un dispositif
roc-vad device show 1

# Supprimer un dispositif
roc-vad device del 1

# Désactiver temporairement
roc-vad device disable 1
roc-vad device enable 1
```
