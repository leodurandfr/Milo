# Manuel d'utilisation Milo

> 🇬🇧 [English version](manual_en.md)

---

# Installation

## Installation de Milo

Milo s'installe sur un Raspberry Pi en flashant une image pré-configurée sur une carte SD. La même image est utilisée pour un serveur Milo ou un client multiroom — le choix se fait lors de la configuration initiale.

### Prérequis

* Un **Raspberry Pi** (modèle 4 ou 5 recommandé)
* Une **carte microSD** (16 Go minimum)
* Une **carte son** compatible (ex : HiFiBerry)
* Optionnel : un **écran tactile** (ex : Waveshare 7" ou 8")

### Étape 1 : Flasher l'image Milo

1. Téléchargez **Raspberry Pi Imager** depuis [raspberrypi.com](https://www.raspberrypi.com/software/).
2. Sélectionnez l'image **Milo** (fichier `.img`) comme système.
3. Flashez la carte SD.

### Étape 2 : Premier démarrage

1. Insérez la carte SD dans le Raspberry Pi et branchez l'alimentation.
2. Au premier démarrage, Milo crée automatiquement un point d'accès WiFi ouvert nommé **Milō**.
3. Connectez-vous à ce réseau depuis votre téléphone ou ordinateur — une page de configuration s'ouvrira automatiquement (portail captif).
4. Un [assistant de configuration](#assistant-de-configuration) vous guide pour configurer votre Milo : mode (serveur ou client), WiFi, carte son, écran, etc.

> Si le Raspberry Pi est déjà connecté au réseau (Ethernet), vous pouvez accéder directement à l'assistant via [**http://milo.local**](http://milo.local).


---

## Installation d'un client Multiroom

Un client multiroom est un Raspberry Pi supplémentaire qui agit comme une enceinte distante. Il reçoit le son depuis le Milo principal et le diffuse de manière synchronisée.

### Prérequis

* Un **Raspberry Pi** supplémentaire (modèle 3, 4 ou 5)
* Une **carte microSD** (8 Go minimum)
* Une **carte son** compatible (ex : HiFiBerry)
* Une **connexion réseau** sur le même réseau que le Milo principal (Ethernet recommandé)

### Étape 1 : Flasher l'image Milo

L'image est la même que pour le serveur principal. Flashez-la sur une carte SD avec **Raspberry Pi Imager** (voir [Installation de Milo](#installation-de-milo)).

### Étape 2 : Configurer en mode Client

1. Insérez la carte SD et démarrez le Raspberry Pi.
2. Connectez-vous au point d'accès **Milō** (ou accédez à `milo.local` si une connexion réseau est déjà disponible).
3. Dans l'assistant de configuration, à l'étape **Mode**, sélectionnez **« Client multiroom »**.
4. Configurez le WiFi si nécessaire, puis validez.

L'assistant désactive automatiquement les services inutiles (Spotify, AirPlay, Radio, etc.) et configure l'appareil en tant que client.

### Étape 3 : Ajouter l'enceinte au serveur principal

Après le redémarrage du client :


1. Ouvrez [**http://milo.local**](http://milo.local) sur votre navigateur.
2. Allez dans **Paramètres > Multiroom**.
3. La nouvelle enceinte apparaît dans **"Enceintes en attente"**.
4. Appuyez sur **Configurer** pour lui donner un nom, sélectionner la carte son et l'ajouter à une zone.

### Connexion réseau

* **Ethernet** : recommandé pour la meilleure synchronisation et la latence la plus faible.
* **WiFi** : fonctionne, mais ajustez les réglages de buffer dans Paramètres > Multiroom si nécessaire.


---

## Assistant de configuration

Au premier démarrage, un assistant en plein écran vous guide pour configurer votre Milo. L'assistant bloque l'accès à l'interface principale tant que la configuration n'est pas terminée.

### Étapes


1. **Bienvenue** : écran d'accueil avec le logo Milo et un bouton « Commencer ».
2. **Langue** : choisissez la langue de l'interface.
3. **Mode** : choisissez le mode de fonctionnement :
   * **Application Milo principale** (serveur) : ce Milo sera le serveur principal avec toutes les sources audio (Spotify, AirPlay, Radio, Podcasts, etc.).
   * **Client multiroom** : ce Milo recevra l'audio depuis un serveur principal pour une écoute multiroom.
4. **WiFi** : connectez-vous à votre réseau WiFi domestique. Cette étape est optionnelle et peut être passée si vous êtes déjà connecté en Ethernet.
   * **Depuis le hotspot Milō** : un bandeau indique que vous êtes connecté au réseau Milō et vous invite à sélectionner votre réseau domestique. Après la connexion, un écran de confirmation s'affiche avec l'adresse `milo.local` pour retrouver Milo sur votre réseau.
   * **Depuis votre réseau local** : un avertissement rappelle qu'après la connexion au WiFi, vous devrez accéder à Milo via `milo.local`.
5. **Carte son** *(serveur uniquement)* : sélectionnez votre carte son (HiFiBerry DAC, DAC+, Amp, etc.).
6. **Écran** *(serveur uniquement)* : sélectionnez votre écran tactile si vous en avez un (Waveshare 7", 8", etc.).
7. **Récapitulatif** : vérifiez la configuration (mode, WiFi, langue, carte son, écran) et validez avec le bouton « Valider & Redémarrer ».

> En mode **Client multiroom**, les étapes Carte son et Écran sont automatiquement masquées.

Le système redémarre avec la configuration appliquée. Milo est prêt à être utilisé.


---

# Le Dock

Le Dock est la barre de navigation principale de Milo, située en bas de l'écran. Il donne accès à toutes les sources audio et fonctionnalités du système.

## Contenu du Dock

Le Dock affiche les icônes des éléments activés, organisés en deux groupes :

**Sources audio** : Spotify, Bluetooth, Radio, Podcasts, AirPlay, DLNA, Mac.

**Fonctionnalités** (après le séparateur) : Égaliseur, Multiroom, Paramètres.

Appuyez sur une icône pour accéder à la source ou fonctionnalité correspondante. L'icône active est mise en surbrillance.

## Comportement

* Le Dock se masque automatiquement après quelques secondes d'inactivité lorsqu'une source est en cours de lecture.
* Glissez vers le haut depuis le bas de l'écran pour le faire réapparaître.
* L'ordre des icônes et les éléments affichés sont personnalisables dans [Paramètres > Dock](#dock-applications).


---

# Sources audio

## Spotify Connect

Milo apparaît comme un appareil Spotify Connect sur votre réseau. La musique est contrôlée depuis l'application Spotify.

### Se connecter


1. Ouvrez l'application **Spotify** sur votre téléphone ou ordinateur.
2. Lancez un morceau.
3. Appuyez sur l'icône **Appareils** (en bas à gauche sur mobile, en bas à droite sur desktop).
4. Sélectionnez **Milō** dans la liste.

### Affichage

Lorsqu'une musique est en cours, Milo affiche la pochette de l'album, le titre du morceau, le nom de l'artiste et un bouton lecture/pause.

> \*Réglages associés : \*[*Paramètres > Spotify*](#spotify-1)


---

## Bluetooth

Milo peut recevoir de l'audio depuis n'importe quel appareil Bluetooth.

### Appairer un appareil


1. Sur votre téléphone ou ordinateur, ouvrez les **réglages Bluetooth**.
2. Recherchez les appareils disponibles.
3. Sélectionnez **Milō** dans la liste.

### Affichage

L'interface affiche l'état de connexion : **"Prêt"** ou **"Connecté à \[nom de l'appareil\]"**, ainsi qu'un bouton **Déconnecter** lorsqu'un appareil est connecté.


---

## Radio

Milo permet d'écouter des milliers de stations de radio Internet du monde entier.

### Favoris

L'écran principal affiche vos **stations favorites**. Appuyez sur une station pour lancer la lecture. Pour ajouter une station en favori, appuyez sur l'icône **coeur**.

### Rechercher des stations

Appuyez sur l'icône de **recherche** pour accéder à la découverte :

* Recherche par nom
* Filtrer par pays ou par genre
* Top stations les plus populaires

### Lecture

Pendant la lecture, Milo affiche le logo de la station, son nom, le genre et le débit audio, ainsi que la piste en cours lorsqu'elle est reconnue.

### Personnalisation des stations

L'affichage des stations favorites est entièrement personnalisable. Vous pouvez modifier le nom, l'image, l'URL du flux et les métadonnées de n'importe quelle station. Vous pouvez aussi ajouter vos propres stations avec une URL de flux personnalisée. Ces réglages se trouvent dans [Paramètres > Radio](#radio-1).

### Reconnaissance de piste

Milo peut identifier automatiquement la musique en cours de diffusion. Lorsqu'un morceau est reconnu, le titre et l'artiste s'affichent à l'écran.

### Écran de veille

Après une période d'inactivité, un écran de veille s'affiche en plein écran avec les informations de la station et de la piste en cours. Touchez l'écran pour revenir à l'interface.

> *Réglages associés : *[*Paramètres > Radio*](#radio-1)


---

## Podcasts

Milo intègre un lecteur de podcasts complet pour rechercher, s'abonner et écouter des podcasts.

### Navigation

Le lecteur propose plusieurs vues :

* **Accueil** : recommandations et podcasts tendance
* **Recherche** : trouvez un podcast par nom ou mot-clé
* **Abonnements** : vos podcasts suivis
* **File d'attente** : les épisodes en attente de lecture
* **Genres** : parcourez par catégorie

### S'abonner à un podcast


1. Recherchez un podcast ou parcourez les recommandations.
2. Ouvrez la fiche du podcast.
3. Appuyez sur **S'abonner**.

Le podcast apparaît dans vos **Abonnements** pour un accès rapide.

### Écouter un épisode

Appuyez sur un épisode pour lancer la lecture. L'interface affiche :

* La pochette du podcast
* Le titre de l'épisode
* Une barre de progression (appuyez dessus pour naviguer)
* Des boutons **reculer 15s** et **avancer 30s**
* Un bouton **lecture/pause**
* Un sélecteur de vitesse (0.5x, 0.75x, 1.0x, 1.25x, 1.5x, 2.0x)

### Reprise automatique

Si vous quittez un épisode en cours, Milo sauvegarde votre position. La lecture reprend là où vous vous étiez arrêté.

### Écran de veille

Comme pour la radio, un écran de veille s'affiche après une période d'inactivité avec les informations de l'épisode en cours.

> \*Réglages associés : \*[*Paramètres > Podcasts*](#podcasts-1)


---

## AirPlay 2

Milo est compatible AirPlay 2 et apparaît comme une enceinte sur votre réseau.

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

### Se connecter


1. Installez l'application **ROC** sur votre Mac.
2. Configurez l'émetteur pour envoyer l'audio vers l'adresse de Milo.
3. Milo détecte automatiquement le flux et commence la lecture.

### Affichage

L'interface affiche l'état de connexion : **"Prêt à streamer"** ou **"Connecté à \[nom du Mac\]"**.

> \*Réglages associés : \*[*Paramètres > Mac*](#mac-1)


---

## DLNA

Milo apparaît comme un lecteur DLNA (une cible « Play To ») sur votre réseau. N'importe quelle application de contrôle UPnP/DLNA peut lui envoyer de la musique — par exemple BubbleUPnP ou Hi-Fi Cast sur Android, un NAS Synology ou QNAP, Plex, JRiver, foobar2000 ou Audirvana.

### Se connecter

1. Ouvrez votre application de contrôle DLNA, ou l'interface de votre NAS / serveur multimédia.
2. Choisissez **Milo** dans la liste des lecteurs (périphériques de sortie).
3. Lancez un morceau — Milo démarre la lecture et affiche les métadonnées.

### Affichage

Milo affiche la pochette, le titre, l'artiste et l'album. La lecture est pilotée depuis l'application de contrôle — Milo n'affiche pas de boutons de commande, comme pour AirPlay.

> **Remarque :** le « Play To » DLNA envoie un morceau de musique entier à Milo. Ce n'est pas une sortie audio déportée pour le son d'une autre application : il ne peut donc pas jouer l'audio d'une vidéo, d'un flux TV ou d'un film, et il n'y a pas de synchronisation labiale.


---

# Égaliseur (DSP)

Milo intègre un processeur audio qui permet d'ajuster le son selon vos préférences. L'égaliseur est accessible depuis le Dock.

## Égaliseur paramétrique

L'égaliseur propose 10 bandes de fréquences (31 Hz, 63 Hz, 125 Hz, 250 Hz, 500 Hz, 1 kHz, 2 kHz, 4 kHz, 8 kHz, 16 kHz). Pour chaque bande, déplacez le curseur vers le haut ou le bas pour ajuster le gain (-15 à +15 dB).

### Presets

Des presets prédéfinis permettent d'appliquer rapidement un profil sonore adapté à votre écoute :

* **Genres musicaux** : Acoustic, Classical, Dance, Deep, Electronic, Hip-Hop, Jazz, Latin, Lounge, Piano, Pop, R&B, Rock
* **Optimisation** : Bass Boost, Bass Reducer, Treble Boost, Treble Reducer, Vocal Boost, Loudness, Small Speakers, Spoken Word
* **Neutre** : Flat (par défaut, aucune modification)

Vous pouvez également sauvegarder votre propre preset personnalisé après avoir ajusté les bandes manuellement.

## Loudness

La compensation loudness améliore la perception des basses et des aigus à faible volume. Elle rend la musique plus naturelle lorsque vous écoutez à bas volume.

* **Boost basses** : intensité du renforcement des graves (0 à 15 dB)
* **Boost aigus** : intensité du renforcement des aigus (0 à 15 dB)

## Compresseur

Le compresseur réduit les écarts de volume entre les passages forts et faibles. Utile pour écouter en arrière-plan sans être surpris par des changements de volume brusques.

* **Ratio** : intensité de la compression (1:1 = aucune, 20:1 = forte)
* **Seuil** : niveau à partir duquel la compression s'active
* **Attaque** : vitesse de réaction du compresseur
* **Relâchement** : vitesse de retour à la normale
* **Gain de compensation** : remonter le volume global après compression


---

# Multiroom

Le multiroom permet de diffuser la musique sur plusieurs enceintes dans différentes pièces, parfaitement synchronisées.

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

## Nouvelles enceintes

Lorsqu'un nouveau client multiroom est installé et démarré, il apparaît automatiquement dans **"Enceintes en attente"**. Appuyez sur **Configurer** pour lui donner un nom et l'ajouter à une zone.

## Types d'enceintes

Chaque enceinte du réseau peut être configurée avec un type qui détermine son comportement audio :

* **Satellite** : petite enceinte, fréquence de coupure par défaut à 120 Hz
* **Bibliothèque (Bookshelf)** : enceinte moyenne, fréquence de coupure par défaut à 80 Hz (standard THX)
* **Colonne (Tower)** : enceinte large bande, fréquence de coupure par défaut à 50 Hz
* **Subwoofer** : enceinte de basses uniquement, reçoit les fréquences sous la coupure

Le type d'enceinte se définit lors de la configuration initiale ou à tout moment dans les réglages de l'enceinte.

## Crossover automatique

Lorsqu'un subwoofer est présent et en ligne dans une zone, le crossover s'active automatiquement :

* Les **enceintes principales** reçoivent un filtre passe-haut (les basses sont coupées)
* Le **subwoofer** reçoit un filtre passe-bas (seules les basses sont envoyées)
* La **fréquence de coupure** est déterminée automatiquement selon le type d'enceinte, mais peut être ajustée manuellement (20 à 200 Hz)

Un badge sur la zone indique la fréquence active (ex : "80 Hz"). Si le subwoofer est hors ligne, le crossover se désactive automatiquement et les enceintes principales reçoivent le signal complet.

> \*Réglages avancés : \*[*Paramètres > Multiroom*](#multiroom-1)


---

# Paramètres

Les paramètres sont accessibles depuis l'icône **Paramètres** dans le Dock. L'écran principal affiche toutes les catégories sous forme de grille.


---

## Langue

Choisissez la langue de l'interface. Le changement est appliqué immédiatement.


---

## WiFi

Gérez la connexion WiFi de votre Milo.

* **État de la connexion** : affiche le réseau connecté, la force du signal et l'adresse IP.
* **Réseaux connus** : vos réseaux enregistrés. Vous pouvez vous reconnecter ou oublier un réseau.
* **Autres réseaux** : les réseaux WiFi détectés à proximité. Appuyez sur un réseau pour entrer le mot de passe et vous connecter.
* **Actualiser** : relancez la recherche de réseaux disponibles.


---

## Dock (Applications)

Personnalisez le contenu et l'ordre du Dock :

* **Activer / Désactiver** chaque source audio (Spotify, Bluetooth, AirPlay, Radio, Podcasts, Mac) et chaque fonctionnalité (Égaliseur, Multiroom).
* **Réorganiser** l'ordre des icônes selon vos préférences.


---

## Volume

* **Pas de l'encodeur rotatif** : sensibilité du bouton physique (1 à 6 dB par cran).
* **Pas tactile** : sensibilité des boutons +/- sur l'écran (1 à 6 dB).
* **Limites de volume** : volume minimum et maximum autorisé.
* **Volume au démarrage** : restaurer le dernier volume utilisé ou démarrer à un volume fixe.
* **Télécommande Bluetooth** : appairage, état de connexion, niveau de batterie, pas de volume dédié.


---

## Écran

* **Luminosité** : intensité de l'écran (1 à 10).
* **Échelle de l'interface** : Petit, Normal ou Grand.
* **Écran de veille** : activer/désactiver et régler le délai (10 secondes à 5 minutes).
* **Mise en veille automatique** : l'écran s'éteint après une période d'inactivité (10 secondes à 30 minutes).


---

## Multiroom

Réglages avancés pour optimiser la synchronisation multiroom :

* **Presets réseau** : Latence faible (Ethernet), Équilibré ou Stabilité (WiFi).
* **Buffer global** : buffer réseau (100 à 2000 ms).
* **Taille des paquets audio** : taille des chunks (10 à 100 ms).


---

## Spotify

* **Déconnexion automatique** : activer ou désactiver la déconnexion automatique après inactivité.
* **Délai de déconnexion** : 10 secondes à 30 minutes.


---

## Mac

* **Latence cible** : latence du flux audio réseau (5 à 500 ms).
* **Profil de latence** : Réactif, Équilibré ou Optimisé réseau.
* **Taille de trame** : 4 ms, 8 ms ou 16 ms.


---

## Radio

* **Reconnaissance de piste** : activer ou désactiver l'identification automatique de la musique en cours.

### Gestion des stations

La gestion des stations est organisée en trois catégories :

* **Favoris non modifiés** : vos stations favorites telles qu'elles proviennent du catalogue RadioBrowser. Appuyez sur une station pour la personnaliser.
* **Stations modifiées** : vos stations favorites dont vous avez personnalisé l'affichage (nom, image, etc.). Vous pouvez restaurer les métadonnées d'origine à tout moment.
* **Stations ajoutées** : des stations créées manuellement avec votre propre URL de flux. Ces stations peuvent être supprimées.

### Personnaliser une station

Pour chaque station, vous pouvez modifier :

* **Nom** : le nom affiché dans l'interface
* **URL du flux** : l'adresse du flux audio (HTTP/HTTPS)
* **Image** : uploadez une image personnalisée (JPEG, PNG, WEBP, GIF — max 5 Mo) pour remplacer le logo par défaut
* **Pays, Genre, Codec, Débit** : métadonnées complémentaires affichées sous le nom de la station

### Ajouter une station personnalisée

Appuyez sur **Ajouter une station** pour créer une entrée avec votre propre URL de flux audio. Seuls le nom et l'URL sont obligatoires. Vous pouvez également ajouter une image et des métadonnées.


---

## Podcasts

* **Identifiants API** : identifiant utilisateur et clé API pour accéder au catalogue de podcasts.
* **Utilisation API** : nombre de requêtes utilisées ce mois-ci et date de réinitialisation.


---

## Matériel

Consultez les informations sur le matériel détecté : carte son, écran, encodeur rotatif.


---

## Mises à jour

* **Système Milo** : version actuelle et mises à jour disponibles.
* **Programmes** : mise à jour individuelle de chaque composant (Spotify, AirPlay, Radio, Podcasts, etc.).


---

## Informations

* Version de Milo
* Adresse IP
* Température du processeur
* Utilisation CPU et RAM


---

## Arrêt / Redémarrage

En bas de l'écran des paramètres, vous pouvez **redémarrer** ou **éteindre** le système (avec confirmation).

/