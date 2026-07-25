# Milō User Manual

---

# Installation

## Installing Milō

Milō is installed on a Raspberry Pi by flashing a pre-configured image onto an SD card. The same image is used for a Milō server or a multiroom client — the choice is made during the initial setup.

### Requirements

* A **Raspberry Pi** (model 5 with 8 GB recommended)
* A **microSD card** (16 GB minimum)
* A compatible **HiFiBerry sound card** — amplifier or DAC. The **Amp4 Pro** is required if you connect a screen.
* Optional: a **touchscreen** — Waveshare 7" USB (1024×600) or Waveshare 8" DSI (1280×800)

### Step 1: Flash the Milō image

1. Download **Raspberry Pi Imager** from [raspberrypi.com](https://www.raspberrypi.com/software/).
2. Select the **Milō** image (`.img` file) as the operating system.
3. Flash the SD card.

### Step 2: First boot

1. Insert the SD card into the Raspberry Pi and connect the power.
2. On first boot, Milō automatically creates an open Wi-Fi access point named **Milō**.
3. Connect to this network from your phone or computer — a setup page opens automatically (captive portal).
4. A [setup wizard](#setup-wizard) guides you through configuring your Milō: mode (server or client), Wi-Fi, sound card, screen, etc.

> If the Raspberry Pi is already connected to the network (Ethernet), you can reach the wizard directly at [**http://milo.local**](http://milo.local).


---

## Installing a Multiroom client

A multiroom client is an additional Raspberry Pi that acts as a remote speaker. It receives audio from the main Milō and plays it back in sync.

### Requirements

* An additional **Raspberry Pi** (model 4 with 2 GB, or model 5)
* A **microSD card** (8 GB minimum)
* A compatible **HiFiBerry sound card** — amplifier or DAC
* A **network connection** on the same network as the main Milō (Ethernet recommended)

### Step 1: Flash the Milō image

The image is the same as for the main server. Flash it onto an SD card with **Raspberry Pi Imager** (see [Installing Milō](#installing-milō)).

### Step 2: Configure as a Client

1. Insert the SD card and start the Raspberry Pi.
2. Connect to the **Milō** access point (or go to `milo.local` if a network connection is already available).
3. In the setup wizard, at the **Mode** step, select **"Multiroom client"**.
4. Configure Wi-Fi if needed, then confirm.

The wizard automatically disables the services that aren't needed (Spotify, AirPlay, Radio, etc.) and configures the device as a client.

### Step 3: Add the speaker to the main server

After the client reboots:

1. Open [**http://milo.local**](http://milo.local) in your browser.
2. Go to **Settings > Multiroom**.
3. The new speaker appears under **"Pending speakers"**.
4. Tap **Configure** to give it a name, select its sound card, and add it to a zone.

### Network connection

* **Ethernet**: recommended for the best synchronization and lowest latency.
* **Wi-Fi**: works, but adjust the buffer settings in Settings > Multiroom if needed.


---

## Setup wizard

On first boot, a full-screen wizard guides you through configuring your Milō. The wizard blocks access to the main interface until setup is complete.

### Steps

1. **Welcome**: home screen with the Milō logo and a "Get started" button.
2. **Language**: choose the interface language.
3. **Mode**: choose the operating mode:
   * **Main Milō app** (server): this Milō will be the main server with all audio sources (Spotify, AirPlay, Radio, Podcasts, etc.).
   * **Multiroom client**: this Milō will receive audio from a main server for multiroom listening.
4. **Wi-Fi**: connect to your home Wi-Fi network. This step is optional and can be skipped if you are already connected over Ethernet.
   * **From the Milō hotspot**: a banner indicates that you are connected to the Milō network and invites you to select your home network. After connecting, a confirmation screen shows the `milo.local` address to find Milō on your network.
   * **From your local network**: a warning reminds you that, after connecting to Wi-Fi, you will need to reach Milō at `milo.local`.
5. **Sound card** *(server only)*: select your sound card (HiFiBerry DAC, DAC+, Amp, etc.).
6. **Screen** *(server only)*: select your touchscreen if you have one (Waveshare 7", 8", etc.).
7. **Summary**: review the configuration (mode, Wi-Fi, language, sound card, screen) and confirm with the "Confirm & Restart" button.

> In **Multiroom client** mode, the Sound card and Screen steps are hidden automatically.

The system restarts with the configuration applied. Milō is ready to use.


---

# The Dock

The Dock is Milō's main navigation bar, located at the bottom of the screen. It gives access to all audio sources and system features.

## Dock contents

The Dock shows the icons of the enabled items, organized into two groups:

**Audio sources**: Spotify, Bluetooth, Radio, Podcasts, AirPlay, DLNA, Qobuz, Music Library, Mac, CD.

**Features** (after the separator): Equalizer, Multiroom, Lyrics, Settings.

Tap an icon to open the corresponding source or feature. The active icon is highlighted.

## Behavior

* The Dock hides automatically after a few seconds of inactivity while a source is playing.
* Swipe up from the bottom of the screen to bring it back.
* The icon order and the displayed items are customizable in [Settings > Dock](#dock-applications).


---

# Audio sources

## Spotify Connect

Milō appears as a Spotify Connect device on your network. Music is controlled from the Spotify app.

### Connecting

1. Open the **Spotify** app on your phone or computer.
2. Start a track.
3. Tap the **Devices** icon (bottom-left on mobile, bottom-right on desktop).
4. Select **Milō** from the list.

### Display

While music is playing, Milō shows the album artwork, track title, artist name, and a play/pause button.

> *Related settings:* [*Settings > Spotify*](#spotify)


---

## Bluetooth

Milō can receive audio from any Bluetooth device.

### Pairing a device

1. On your phone or computer, open the **Bluetooth settings**.
2. Scan for available devices.
3. Select **Milō** from the list.

### Display

The interface shows the connection state: **"Ready"** or **"Connected to [device name]"**, along with a **Disconnect** button when a device is connected.


---

## Radio

Milō lets you listen to thousands of Internet radio stations from around the world.

### Favorites

The main screen shows your **favorite stations**. Tap a station to start playback. To add a station to your favorites, tap the **heart** icon.

### Search for stations

Tap the **search** icon to open discovery:

* Search by name
* Filter by country or genre
* Top most popular stations

### Playback

During playback, Milō shows the station logo, its name, the genre, and the audio bitrate, as well as the current track when it is recognized.

### Customizing stations

The way favorite stations are displayed is fully customizable. You can change the name, image, stream URL, and metadata of any station. You can also add your own stations with a custom stream URL. These settings are found in [Settings > Radio](#radio-1).

### Track recognition

Milō can automatically identify the music currently playing. When a track is recognized, the title and artist are shown on screen.

### Screen saver

After a period of inactivity, a full-screen screen saver appears with the current station and track information. Touch the screen to return to the interface.

> *Related settings:* [*Settings > Radio*](#radio-1)


---

## Podcasts

Milō includes a full podcast player to search, subscribe to, and listen to podcasts.

### Navigation

The player offers several views:

* **Home**: recommendations and trending podcasts
* **Search**: find a podcast by name or keyword
* **Subscriptions**: the podcasts you follow
* **Queue**: episodes waiting to be played
* **Genres**: browse by category

### Subscribing to a podcast

1. Search for a podcast or browse the recommendations.
2. Open the podcast page.
3. Tap **Subscribe**.

The podcast appears in your **Subscriptions** for quick access.

### Playing an episode

Tap an episode to start playback. The interface shows:

* The podcast artwork
* The episode title
* A progress bar (tap it to seek)
* **Rewind 15s** and **forward 30s** buttons
* A **play/pause** button
* A speed selector (0.5x, 0.75x, 1.0x, 1.25x, 1.5x, 2.0x)

### Automatic resume

If you leave an episode partway through, Milō saves your position. Playback resumes where you left off.

### Screen saver

As with the radio, a screen saver appears after a period of inactivity with the current episode information.

> *Related settings:* [*Settings > Podcasts*](#podcasts-1)


---

## AirPlay 2

Milō is AirPlay 2 compatible and appears as a speaker on your network.

### Connecting from an iPhone or iPad

1. Open the **Control Center** (swipe down from the top-right corner).
2. Long-press the **Music** tile.
3. Tap the **AirPlay** icon.
4. Select **Milō**.

### Connecting from a Mac

1. Click the **Sound** icon in the menu bar.
2. Select **Milō** as the audio output.

### Display

Milō shows the artwork, title, artist, and the name of the connected device (e.g. "Léo's iPhone"). Playback is controlled solely from the Apple device — Milō does not show control buttons.


---

## Mac (network streaming)

Milō can receive audio from a Mac in real time over the local network, using the ROC protocol.

### Connecting

1. Install the **ROC** app on your Mac.
2. Configure the sender to send audio to Milō's address.
3. Milō detects the stream automatically and starts playback.

### Display

The interface shows the connection state: **"Ready to stream"** or **"Connected to [Mac name]"**.

> *Related settings:* [*Settings > Mac*](#mac)


---

## DLNA

Milō appears as a DLNA renderer (a "Play To" target) on your network. Any UPnP/DLNA controller can push music to it — for example BubbleUPnP or Hi-Fi Cast on Android, a Synology or QNAP NAS, Plex, JRiver, foobar2000, or Audirvana.

### Connecting

1. Open your DLNA controller app, or your NAS / media server's control interface.
2. Choose **Milo** in the list of renderers (output devices).
3. Play a track — Milō starts playback and shows the metadata.

### Display

Milō shows the artwork, title, artist, and album. Playback is controlled from the controller — Milō shows no control buttons, like AirPlay.

> **Note:** DLNA "Play To" pushes a whole music track to Milō. It is not a remote audio output for another app's sound, so it cannot play the audio of a video, a TV stream, or a film, and there is no lip-sync.


---

## Qobuz Connect

Milō appears as a Qobuz Connect device on your network, so you can cast lossless audio to it straight from the Qobuz app.

### One-time sign-in

Before Milō can appear in Qobuz, you sign in once with your Qobuz account in [Settings > Qobuz](#qobuz). Milō stays connected afterwards — you only do this again if you sign out.

### Connecting

1. Open the **Qobuz** app on your phone, tablet, or computer.
2. Start a track.
3. Tap the **devices / cast** icon.
4. Select **Milō** from the list.

### Display

Milō shows the album artwork, track title, artist, and album. Playback is controlled from the Qobuz app — like AirPlay, Milō shows no control buttons, and the progress bar stays inactive.

> *Related settings:* [*Settings > Qobuz*](#qobuz)


---

## Music Library

Music Library plays your own music collection — from a **USB drive** plugged into Milō or from a **network share** (SMB/NFS) on your NAS or computer. Milō indexes the files and lets you browse them with full artwork and metadata.

### Adding your music

* **USB drive**: plug it in. Milō detects it automatically, mounts it, and starts building the library.
* **Network share**: add it in [Settings > Music Library](#music-library-1) with the share's address and, if needed, a username and password.

### Building the library

The first time Milō indexes a collection it shows a **"building library…"** state with a live count of indexed tracks. Large libraries take a few minutes; you can keep using Milō while it works, and the catalog appears on its own when it's ready.

### Browsing and playback

Browse by **Artists**, **Albums**, **Genres**, or **Playlists**, or **search** the whole library. Tap an album, playlist, or track to start playback — Milō builds a queue from whatever context you picked and plays it back-to-back, gapless and bit-perfect (no quality loss).

During playback Milō shows the album artwork, track title, artist, album, and a progress bar, with full transport controls (play/pause, next/previous, seek).

> *Related settings:* [*Settings > Music Library*](#music-library-1)


---

## CD

Milō plays audio CDs through a USB CD/DVD drive.

### Connecting the drive

Plug a USB CD/DVD drive into Milō. It is detected automatically — no setup needed.

### Inserting a disc

Insert an audio CD into the drive. Milō recognizes it within a couple of seconds and starts building the tracklist.

### Display

Once the disc is read, Milō looks up the album title, artist, cover art, and track names online and shows them along with full transport controls (play/pause, next/previous, seek) and a progress bar. If the disc isn't found online, Milō falls back to generic track names so you can still play it.

### Ejecting

Tap the **eject** icon to release the disc. You can also just eject it directly from the drive.


---

# Equalizer (DSP)

Milō includes an audio processor that lets you adjust the sound to your taste. The equalizer is reachable from the Dock.

## Parametric equalizer

The equalizer offers 10 frequency bands (31 Hz, 63 Hz, 125 Hz, 250 Hz, 500 Hz, 1 kHz, 2 kHz, 4 kHz, 8 kHz, 16 kHz). For each band, move the slider up or down to adjust the gain (-15 to +15 dB).

### Presets

Predefined presets let you quickly apply a sound profile suited to your listening:

* **Music genres**: Acoustic, Classical, Dance, Deep, Electronic, Hip-Hop, Jazz, Latin, Lounge, Piano, Pop, R&B, Rock
* **Optimization**: Bass Boost, Bass Reducer, Treble Boost, Treble Reducer, Vocal Boost, Loudness, Small Speakers, Spoken Word
* **Neutral**: Flat (default, no modification)

You can also save your own custom preset after adjusting the bands manually.

## Loudness

Loudness compensation improves the perception of bass and treble at low volume. It makes music sound more natural when you listen quietly.

* **Bass boost**: strength of the bass reinforcement (0 to 15 dB)
* **Treble boost**: strength of the treble reinforcement (0 to 15 dB)

## Compressor

The compressor reduces the volume gap between loud and quiet passages. Useful for background listening without being surprised by sudden volume changes.

* **Ratio**: compression strength (1:1 = none, 20:1 = strong)
* **Threshold**: the level at which compression kicks in
* **Attack**: how fast the compressor reacts
* **Release**: how fast it returns to normal
* **Makeup gain**: raise the overall volume after compression


---

# Lyrics

Lyrics displays the words of the song currently playing. Tap the **Lyrics** icon in the Dock to open it full screen.

## Synced or plain

* **Synced lyrics**: the current line is highlighted and the text scrolls on its own, in time with the music.
* **Plain lyrics**: when no timed version exists, the full text is shown as a single block you scroll yourself.
* If nothing is found for the track, Milō says so rather than showing an approximation.

## Playback bar

A playback bar at the bottom of the screen keeps the cover, the title and the transport controls at hand. Tap the arrow, or swipe down, to hide it and give the lyrics the full screen; tap it again to bring it back.

## Availability

Lyrics are looked up from the track's title and artist, so they work with any source that provides them — Spotify, Qobuz, AirPlay, DLNA, CD, Music Library, and Radio when a track has been recognized. Bluetooth and Mac (which send no track information) and Podcasts (spoken word) are not supported.

Milō needs an internet connection for the first lookup of a track; results are then kept on the device, so reopening the same song is instant and works offline.


---

# Multiroom

Multiroom lets you play music on several speakers in different rooms, perfectly synchronized.

## Enabling multiroom

Tap the **Multiroom** icon in the Dock, then turn on the main switch. Activation may take a few seconds.

## Speakers and zones

### Individual speakers

Each speaker on the network appears with its name (customizable), its state (online / offline), an individual volume slider, and a mute button.

### Creating a zone

A zone groups several speakers to control them together.

1. Tap **Create a zone**.
2. Select the speakers to group.
3. Give the zone a name (e.g. "Living room", "Upstairs").

The zone has a global volume and a global mute.

## New speakers

When a new multiroom client is installed and started, it appears automatically under **"Pending speakers"**. Tap **Configure** to give it a name and add it to a zone.

## Speaker types

Each speaker on the network can be configured with a type that determines its audio behavior:

* **Satellite**: small speaker, default crossover frequency at 120 Hz
* **Bookshelf**: medium speaker, default crossover frequency at 80 Hz (THX standard)
* **Tower**: full-range speaker, default crossover frequency at 50 Hz
* **Subwoofer**: bass-only speaker, receives the frequencies below the crossover

The speaker type is set during initial configuration or at any time in the speaker's settings.

## Automatic crossover

When a subwoofer is present and online in a zone, the crossover activates automatically:

* The **main speakers** receive a high-pass filter (the bass is cut)
* The **subwoofer** receives a low-pass filter (only the bass is sent)
* The **crossover frequency** is determined automatically based on the speaker type, but can be adjusted manually (20 to 200 Hz)

A badge on the zone shows the active frequency (e.g. "80 Hz"). If the subwoofer goes offline, the crossover deactivates automatically and the main speakers receive the full signal.

> *Advanced settings:* [*Settings > Multiroom*](#multiroom-1)


---

# Settings

Settings are reachable from the **Settings** icon in the Dock. The main screen shows all categories as a grid.


---

## Language

Choose the interface language. The change is applied immediately.


---

## WiFi

Manage your Milō's Wi-Fi connection.

* **Connection status**: shows the connected network, signal strength, and IP address.
* **Known networks**: your saved networks. You can reconnect to or forget a network.
* **Other networks**: the Wi-Fi networks detected nearby. Tap a network to enter the password and connect.
* **Refresh**: rescan for available networks.


---

## Dock (Applications)

Customize the Dock's contents and order:

* **Enable / Disable** each audio source (Spotify, Bluetooth, Radio, Podcasts, AirPlay, DLNA, Qobuz, Music Library, Mac, CD) and each feature (Equalizer, Multiroom, Lyrics).
* **Reorder** the icons to your preference.


---

## Volume

* **Rotary encoder step**: sensitivity of the physical knob (1 to 6 dB per detent).
* **Touch step**: sensitivity of the on-screen +/- buttons (1 to 6 dB).
* **Volume limits**: minimum and maximum allowed volume.
* **Startup volume**: restore the last-used volume or start at a fixed volume.

> Remotes are configured in [Settings > Remote controls](#remote-controls).


---

## Remote controls

Milō can be driven by two remotes, each with its own screen in this section.

### Bluetooth remote

The **ANTICATER VK-01** remote (and similar Bluetooth HID remotes). Milō shows the connection status and the battery level; **Search** pairs it, **Unpair** forgets it. A dedicated **volume step per click** sets how much each press moves the volume.

The Bluetooth receiver must be enabled for pairing to be possible.

### IR remote (Apple Remote)

The **Apple Remote (1st generation, A1156)**, received by an infrared sensor wired to the Raspberry Pi. Milō only supports this model.

**Pairing** — press **Start detection**, then press any button on the remote within the countdown. Milō learns your remote's identity, so another Apple Remote in the same room will not control it. **Unpair** forgets it.

If nothing is detected, check the receiver's wiring; if the sensor is turned off, the screen offers a shortcut to [Settings > Hardware](#hardware) to enable it.

**Buttons**

| Button | Action |
|---|---|
| **Volume + / −** | Change the volume. Hold to keep it moving. |
| **Play / Pause** | Play or pause the current source. |
| **Next / Previous** | Skip to the next or previous track. |
| **Menu** (press) | Switch to the next audio source, in Dock order. |
| **Menu** (press twice) | Stop playback and leave the current source. |
| **Menu** (hold) | Turn the screen off immediately. |

A dedicated **volume step per click** is available, like the Bluetooth remote.


---

## Screen

* **Brightness**: screen intensity (1 to 10).
* **Interface scale**: Small, Normal, or Large.
* **Screen saver**: enable/disable and set the delay (10 seconds to 5 minutes).
* **Automatic sleep**: the screen turns off after a period of inactivity (10 seconds to 30 minutes).


---

## Multiroom

Advanced settings to optimize multiroom synchronization:

* **Network presets**: Low latency (Ethernet), Balanced, or Stability (Wi-Fi).
* **Global buffer**: network buffer (100 to 2000 ms).
* **Audio packet size**: chunk size (10 to 100 ms).


---

## Spotify

* **Automatic disconnect**: enable or disable automatic disconnection after inactivity.
* **Disconnect delay**: 10 seconds to 30 minutes.


---

## Mac

* **Target latency**: latency of the network audio stream (5 to 500 ms).
* **Latency profile**: Responsive, Balanced, or Network-optimized.
* **Frame size**: 4 ms, 8 ms, or 16 ms.


---

## Radio

* **Track recognition**: enable or disable automatic identification of the music currently playing.

### Station management

Station management is organized into three categories:

* **Unmodified favorites**: your favorite stations exactly as they come from the RadioBrowser catalog. Tap a station to customize it.
* **Modified stations**: your favorite stations whose display you have customized (name, image, etc.). You can restore the original metadata at any time.
* **Added stations**: stations created manually with your own stream URL. These stations can be deleted.

### Customize a station

For each station, you can edit:

* **Name**: the name shown in the interface
* **Stream URL**: the address of the audio stream (HTTP/HTTPS)
* **Image**: upload a custom image (JPEG, PNG, WEBP, GIF — max 5 MB) to replace the default logo
* **Country, Genre, Codec, Bitrate**: additional metadata shown under the station name

### Add a custom station

Tap **Add a station** to create an entry with your own audio stream URL. Only the name and URL are required. You can also add an image and metadata.


---

## Podcasts

* **API credentials**: user ID and API key to access the podcast catalog.
* **API usage**: number of requests used this month and the reset date.


---

## Qobuz

* **Account**: sign in with your Qobuz account so Milō can appear as a Qobuz Connect device. The screen shows whether you are signed in, and lets you sign out. Signing in is a one-time step.


---

## Music Library

* **Network shares**: add, view, or remove SMB/NFS shares. For each share you provide its address (host and path) and, if the share is protected, a username and password. USB drives need no configuration here — they are detected automatically when plugged in.
* **Library status**: shows whether Milō is currently indexing and how many tracks have been found.


---

## Hardware

Declare the hardware physically connected to your Milō:

* **Audio card**: the HiFiBerry model in use.
* **Screen**: the connected display model, or none.
* **Rotary encoder**: enable it and set its three GPIO pins (CLK, DT, SW).
* **Infrared receiver**: enable it and set the GPIO pin its OUT wire uses (VCC and GND are fixed at 3.3 V and GND). It must be on for an [Apple Remote](#ir-remote-apple-remote) to be paired.

Changing any of these requires a restart — Milō offers **Apply and reboot**.

> Wiring diagrams for each of these are in the project's hardware documentation.


---

## Updates

* **Milō system**: current version and available updates.
* **Programs**: individual updates for each component (Spotify, AirPlay, Radio, Podcasts, etc.).


---

## Information

* Milō version
* IP address
* CPU temperature
* CPU and RAM usage


---

## Shutdown / Restart

At the bottom of the settings screen, you can **restart** or **shut down** the system (with confirmation).
