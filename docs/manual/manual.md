# Manuel d'utilisation Milo

---

# Installation

## Installation de Milo

Milo s'installe sur un Raspberry Pi à l'aide d'un script automatique. L'installation est entièrement non-interactive et prend en charge toute la configuration du système.

### Prérequis

- Un **Raspberry Pi** (modèle 4 ou 5 recommandé)
- Une **carte microSD** (16 Go minimum)
- **Raspberry Pi OS Lite 64-bit** installé sur la carte SD
- Une **connexion Internet** (Ethernet ou WiFi)
- Une **carte son** compatible (ex : HiFiBerry)
- Optionnel : un **écran tactile** (ex : Waveshare 7" ou 8")

### Étape 1 : Préparer la carte SD

1. Téléchargez **Raspberry Pi Imager** depuis [raspberrypi.com](https://www.raspberrypi.com/software/).
2. Sélectionnez **Raspberry Pi OS Lite (64-bit)** comme système.
3. Configurez le **WiFi** et le **SSH** dans les options avancées de l'Imager (recommandé pour une installation sans écran).
4. Flashez la carte SD.

### Étape 2 : Démarrer le Raspberry Pi

1. Insérez la carte SD dans le Raspberry Pi.
2. Branchez l'alimentation et attendez le démarrage.
3. Connectez-vous en SSH : `ssh pi@raspberrypi.local` (ou l'utilisateur configuré dans l'Imager).

### Étape 3 : Lancer l'installation

Exécutez la commande suivante :

```bash
git clone https://github.com/leodurandfr/Milo.git /tmp/milo-install && bash /tmp/milo-install/install.sh
```

L'installation prend environ 20 à 40 minutes selon la vitesse de votre connexion Internet. Le script installe toutes les dépendances, configure le système audio et l'interface web, puis redémarre automatiquement.

### Étape 4 : Configuration initiale

Après le redémarrage, ouvrez un navigateur et rendez-vous sur **http://milo.local**. Un assistant de configuration vous guide (voir [Assistant de configuration](#assistant-de-configuration)).

---

## Installation d'un client Multiroom

Un client multiroom est un Raspberry Pi supplémentaire qui agit comme une enceinte distante. Il reçoit le son depuis le Milo principal et le diffuse de manière synchronisée.

### Prérequis

- Un **Raspberry Pi** supplémentaire (modèle 3, 4 ou 5)
- Une **carte microSD** (8 Go minimum)
- **Raspberry Pi OS Lite 64-bit** installé sur la carte SD
- Une **carte son** compatible (ex : HiFiBerry)
- Une **connexion réseau** sur le même réseau que le Milo principal (Ethernet recommandé)

### Étape 1 : Préparer la carte SD

Comme pour l'installation principale : utilisez **Raspberry Pi Imager**, sélectionnez **Raspberry Pi OS Lite (64-bit)**, configurez le WiFi et le SSH, puis flashez la carte SD.

### Étape 2 : Lancer l'installation

Connectez-vous en SSH au nouveau Raspberry Pi et exécutez :

```bash
git clone https://github.com/leodurandfr/Milo.git /tmp/milo-install && bash /tmp/milo-install/milo-client/install-client.sh
```

Le script détecte automatiquement le Milo principal sur le réseau. Si la détection échoue, spécifiez son adresse IP :

```bash
bash /tmp/milo-install/milo-client/install-client.sh --server 192.168.1.10
```

L'installation prend environ 10 à 15 minutes, puis le système redémarre automatiquement.

### Étape 3 : Configurer l'enceinte

Après le redémarrage du client :

1. Ouvrez **http://milo.local** sur votre navigateur.
2. Allez dans **Paramètres > Multiroom**.
3. La nouvelle enceinte apparaît dans **"Enceintes en attente"**.
4. Appuyez sur **Configurer** pour lui donner un nom, sélectionner la carte son et l'ajouter à une zone.

### Connexion réseau

- **Ethernet** : recommandé pour la meilleure synchronisation et la latence la plus faible.
- **WiFi** : fonctionne, mais ajustez les réglages de buffer dans Paramètres > Multiroom si nécessaire.

---

## Assistant de configuration

Au premier démarrage, un assistant vous guide pour configurer votre matériel.

![Assistant de configuration](images/setup-wizard.png)

### Étapes

1. **Langue** : choisissez la langue de l'interface.
2. **Carte son** : sélectionnez votre carte son (HiFiBerry DAC, DAC+, Amp, etc.).
3. **Écran** : sélectionnez votre écran tactile si vous en avez un (Waveshare 7", 8", etc.).
4. **Résumé** : vérifiez la configuration et validez.

Le système redémarre une dernière fois avec la configuration appliquée. Milo est prêt à être utilisé.

---

# Le Dock

Le Dock est la barre de navigation principale de Milo, située en bas de l'écran. Il donne accès à toutes les sources audio et fonctionnalités du système.

![Dock](images/dock.png)

## Contenu du Dock

Le Dock affiche les icônes des éléments activés, organisés en deux groupes :

**Sources audio** : Spotify, Bluetooth, Radio, Podcasts, AirPlay, Mac.

**Fonctionnalités** (après le séparateur) : Égaliseur, Multiroom, Paramètres.

Appuyez sur une icône pour accéder à la source ou fonctionnalité correspondante. L'icône active est mise en surbrillance.

## Comportement

- Le Dock se masque automatiquement après quelques secondes d'inactivité lorsqu'une source est en cours de lecture.
- Glissez vers le haut depuis le bas de l'écran pour le faire réapparaître.
- L'ordre des icônes et les éléments affichés sont personnalisables dans [Paramètres > Dock](#dock-applications).

---

# Sources audio

## Spotify Connect

Milo apparaît comme un appareil Spotify Connect sur votre réseau. La musique est contrôlée depuis l'application Spotify.

![Spotify](images/spotify.png)

### Se connecter

1. Ouvrez l'application **Spotify** sur votre téléphone ou ordinateur.
2. Lancez un morceau.
3. Appuyez sur l'icône **Appareils** (en bas à gauche sur mobile, en bas à droite sur desktop).
4. Sélectionnez **Milō** dans la liste.

### Affichage

Lorsqu'une musique est en cours, Milo affiche la pochette de l'album, le titre du morceau, le nom de l'artiste et un bouton lecture/pause.

> *Réglages associés : [Paramètres > Spotify](#spotify-1)*

---

## Bluetooth

Milo peut recevoir de l'audio depuis n'importe quel appareil Bluetooth.

![Bluetooth](images/bluetooth.png)

### Appairer un appareil

1. Sur votre téléphone ou ordinateur, ouvrez les **réglages Bluetooth**.
2. Recherchez les appareils disponibles.
3. Sélectionnez **Milō** dans la liste.

### Affichage

L'interface affiche l'état de connexion : **"Prêt"** ou **"Connecté à [nom de l'appareil]"**, ainsi qu'un bouton **Déconnecter** lorsqu'un appareil est connecté.

---

## Radio

Milo permet d'écouter des milliers de stations de radio Internet du monde entier.

![Radio](images/radio.png)

### Favoris

L'écran principal affiche vos **stations favorites**. Appuyez sur une station pour lancer la lecture. Pour ajouter une station en favori, appuyez sur l'icône **coeur**.

![Radio favoris](images/radio-favorites.png)

### Rechercher des stations

Appuyez sur l'icône de **recherche** pour accéder à la découverte :

- Recherche par nom
- Filtrer par pays ou par genre
- Top stations les plus populaires

![Radio recherche](images/radio-search.png)

### Lecture

Pendant la lecture, Milo affiche le logo de la station, son nom, le genre et le débit audio, ainsi que la piste en cours lorsqu'elle est reconnue.

![Radio lecture](images/radio-playing.png)

### Reconnaissance de piste

Milo peut identifier automatiquement la musique en cours de diffusion. Lorsqu'un morceau est reconnu, le titre et l'artiste s'affichent à l'écran.

### Écran de veille

Après une période d'inactivité, un écran de veille s'affiche en plein écran avec les informations de la station et de la piste en cours. Touchez l'écran pour revenir à l'interface.

![Radio écran de veille](images/radio-screensaver.png)

> *Réglages associés : [Paramètres > Radio](#radio-1)*

---

## Podcasts

Milo intègre un lecteur de podcasts complet pour rechercher, s'abonner et écouter des podcasts.

![Podcasts](images/podcasts.png)

### Navigation

Le lecteur propose plusieurs vues :

- **Accueil** : recommandations et podcasts tendance
- **Recherche** : trouvez un podcast par nom ou mot-clé
- **Abonnements** : vos podcasts suivis
- **File d'attente** : les épisodes en attente de lecture
- **Genres** : parcourez par catégorie

![Podcasts navigation](images/podcasts-navigation.png)

### S'abonner à un podcast

1. Recherchez un podcast ou parcourez les recommandations.
2. Ouvrez la fiche du podcast.
3. Appuyez sur **S'abonner**.

Le podcast apparaît dans vos **Abonnements** pour un accès rapide.

### Écouter un épisode

Appuyez sur un épisode pour lancer la lecture. L'interface affiche :

- La pochette du podcast
- Le titre de l'épisode
- Une barre de progression (appuyez dessus pour naviguer)
- Des boutons **reculer 15s** et **avancer 30s**
- Un bouton **lecture/pause**
- Un sélecteur de vitesse (0.5x, 0.75x, 1.0x, 1.25x, 1.5x, 2.0x)

![Podcasts lecture](images/podcasts-playing.png)

### Reprise automatique

Si vous quittez un épisode en cours, Milo sauvegarde votre position. La lecture reprend là où vous vous étiez arrêté.

### Écran de veille

Comme pour la radio, un écran de veille s'affiche après une période d'inactivité avec les informations de l'épisode en cours.

> *Réglages associés : [Paramètres > Podcasts](#podcasts-1)*

---

## AirPlay 2

Milo est compatible AirPlay 2 et apparaît comme une enceinte sur votre réseau.

![AirPlay](images/airplay.png)

### Se connecter depuis un iPhone ou iPad

1. Ouvrez le **Centre de contrôle** (glissez depuis le coin supérieur droit).
2. Appuyez longuement sur le bloc **Musique**.
3. Appuyez sur l'icône **AirPlay**.
4. Sélectionnez **Milō**.

### Se connecter depuis un Mac

1. Cliquez sur l'icône **Son** dans la barre de menu.
2. Sélectionnez **Milō** comme sortie audio.

### Affichage

Milo affiche la pochette, le titre, l'artiste et le nom de l'appareil connecté (ex : "iPhone de Léo"). La lecture est contrôlée uniquement depuis l'appareil Apple — Milo n'affiche pas de boutons de contrôle.

---

## Mac (streaming réseau)

Milo peut recevoir l'audio d'un Mac en temps réel via le réseau local, grâce au protocole ROC.

![Mac](images/mac.png)

### Se connecter

1. Installez l'application **ROC** sur votre Mac.
2. Configurez l'émetteur pour envoyer l'audio vers l'adresse de Milo.
3. Milo détecte automatiquement le flux et commence la lecture.

### Affichage

L'interface affiche l'état de connexion : **"Prêt à streamer"** ou **"Connecté à [nom du Mac]"**.

> *Réglages associés : [Paramètres > Mac](#mac-1)*

---

# Égaliseur (DSP)

Milo intègre un processeur audio qui permet d'ajuster le son selon vos préférences. L'égaliseur est accessible depuis le Dock.

![Égaliseur](images/equalizer.png)

## Activer / Désactiver

Un interrupteur principal en haut de l'écran permet d'activer ou de désactiver tous les effets audio.

## Égaliseur paramétrique

L'égaliseur propose plusieurs bandes de fréquences (des basses aux aigus). Pour chaque bande, déplacez le curseur vers le haut ou le bas pour augmenter ou réduire le niveau sonore.

![Égaliseur bandes](images/equalizer-bands.png)

### Presets

Des presets prédéfinis permettent d'appliquer rapidement un profil sonore :

- **Flat** : son neutre, aucune modification (par défaut)
- **Bright** : aigus accentués
- **Warm** : basses renforcées
- **Bass Boost** : emphase sur les graves
- Et d'autres...

Vous pouvez également sauvegarder votre propre preset après avoir ajusté les bandes manuellement.

## Loudness

La compensation loudness améliore la perception des basses et des aigus à faible volume. Elle rend la musique plus naturelle lorsque vous écoutez à bas volume.

![Loudness](images/equalizer-loudness.png)

- **Boost basses** : intensité du renforcement des graves (0 à 15 dB)
- **Boost aigus** : intensité du renforcement des aigus (0 à 15 dB)

## Compresseur

Le compresseur réduit les écarts de volume entre les passages forts et faibles. Utile pour écouter en arrière-plan sans être surpris par des changements de volume brusques.

![Compresseur](images/equalizer-compressor.png)

- **Ratio** : intensité de la compression (1:1 = aucune, 20:1 = forte)
- **Seuil** : niveau à partir duquel la compression s'active
- **Attaque** : vitesse de réaction du compresseur
- **Relâchement** : vitesse de retour à la normale
- **Gain de compensation** : remonter le volume global après compression

---

# Multiroom

Le multiroom permet de diffuser la musique sur plusieurs enceintes dans différentes pièces, parfaitement synchronisées.

![Multiroom](images/multiroom.png)

## Activer le multiroom

Appuyez sur l'icône **Multiroom** dans le Dock, puis activez l'interrupteur principal. L'activation peut prendre quelques secondes.

## Enceintes et zones

### Enceintes individuelles

Chaque enceinte du réseau apparaît avec son nom (personnalisable), son état (en ligne / hors ligne), un curseur de volume individuel et un bouton mute.

### Créer une zone

Une zone regroupe plusieurs enceintes pour les contrôler ensemble.

1. Appuyez sur **Créer une zone**.
2. Sélectionnez les enceintes à regrouper.
3. Donnez un nom à la zone (ex : "Salon", "Étage").

La zone dispose d'un volume global et d'un mute global.

![Multiroom zones](images/multiroom-zones.png)

## Nouvelles enceintes

Lorsqu'un nouveau client multiroom est installé et démarré, il apparaît automatiquement dans **"Enceintes en attente"**. Appuyez sur **Configurer** pour lui donner un nom et l'ajouter à une zone.

![Multiroom nouvelle enceinte](images/multiroom-pending.png)

## Subwoofer (crossover)

Si une de vos enceintes est un subwoofer, vous pouvez configurer un crossover :

- **Fréquence de coupure** : fréquence en dessous de laquelle le son est envoyé au subwoofer
- **Phase** : ajustement pour une meilleure intégration avec les enceintes principales

Un badge sur la zone indique quand le crossover est actif (ex : "80 Hz").

> *Réglages avancés : [Paramètres > Multiroom](#multiroom-1)*

---

# Paramètres

Les paramètres sont accessibles depuis l'icône **Paramètres** dans le Dock. L'écran principal affiche toutes les catégories sous forme de grille.

![Paramètres](images/settings.png)

---

## Langue

Choisissez la langue de l'interface. Le changement est appliqué immédiatement.

![Langue](images/settings-language.png)

---

## Dock (Applications)

Personnalisez le contenu et l'ordre du Dock :

- **Activer / Désactiver** chaque source audio (Spotify, Bluetooth, AirPlay, Radio, Podcasts, Mac) et chaque fonctionnalité (Égaliseur, Multiroom).
- **Réorganiser** l'ordre des icônes selon vos préférences.

![Dock paramètres](images/settings-dock.png)

---

## Volume

- **Pas de l'encodeur rotatif** : sensibilité du bouton physique (1 à 6 dB par cran).
- **Pas tactile** : sensibilité des boutons +/- sur l'écran (1 à 6 dB).
- **Limites de volume** : volume minimum et maximum autorisé.
- **Volume au démarrage** : restaurer le dernier volume utilisé ou démarrer à un volume fixe.
- **Télécommande Bluetooth** : appairage, état de connexion, niveau de batterie, pas de volume dédié.

![Volume paramètres](images/settings-volume.png)

---

## Écran

- **Luminosité** : intensité de l'écran (1 à 10).
- **Échelle de l'interface** : Petit, Normal ou Grand.
- **Écran de veille** : activer/désactiver et régler le délai (10 secondes à 5 minutes).
- **Mise en veille automatique** : l'écran s'éteint après une période d'inactivité (10 secondes à 30 minutes).

![Écran paramètres](images/settings-screen.png)

---

## Multiroom

Réglages avancés pour optimiser la synchronisation multiroom :

- **Presets réseau** : Latence faible (Ethernet), Équilibré ou Stabilité (WiFi).
- **Buffer global** : buffer réseau (100 à 2000 ms).
- **Taille des paquets audio** : taille des chunks (10 à 100 ms).

![Multiroom paramètres](images/settings-multiroom.png)

---

## Spotify

- **Déconnexion automatique** : activer ou désactiver la déconnexion automatique après inactivité.
- **Délai de déconnexion** : 10 secondes à 30 minutes.

![Spotify paramètres](images/settings-spotify.png)

---

## Mac

- **Latence cible** : latence du flux audio réseau (5 à 500 ms).
- **Profil de latence** : Réactif, Équilibré ou Optimisé réseau.
- **Taille de trame** : 4 ms, 8 ms ou 16 ms.

![Mac paramètres](images/settings-mac.png)

---

## Radio

- **Reconnaissance de piste** : activer ou désactiver l'identification automatique de la musique en cours.
- **Gestion des stations** : consulter et modifier vos stations favorites, modifiées ou ajoutées manuellement.

![Radio paramètres](images/settings-radio.png)

---

## Podcasts

- **Identifiants API** : identifiant utilisateur et clé API pour accéder au catalogue de podcasts.
- **Utilisation API** : nombre de requêtes utilisées ce mois-ci et date de réinitialisation.

![Podcasts paramètres](images/settings-podcasts.png)

---

## Matériel

Consultez les informations sur le matériel détecté : carte son, écran, encodeur rotatif.

![Matériel](images/settings-hardware.png)

---

## Mises à jour

- **Système Milo** : version actuelle et mises à jour disponibles.
- **Programmes** : mise à jour individuelle de chaque composant (Spotify, AirPlay, Radio, Podcasts, etc.).

![Mises à jour](images/settings-updates.png)

---

## Informations

- Version de Milo
- Adresse IP
- Température du processeur
- Utilisation CPU et RAM

![Informations](images/settings-info.png)

---

## Arrêt / Redémarrage

En bas de l'écran des paramètres, vous pouvez **redémarrer** ou **éteindre** le système (avec confirmation).
