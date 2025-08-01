# 11 · Installation de Snapclient sur un autre Raspberry pour le Multiroom

### 1. Téléchargement du package v0.31.0

```bash
# Créer un dossier temporaire
mkdir -p ~/snapcast-install
cd ~/snapcast-install

# Télécharger snapclient  
wget https://github.com/badaix/snapcast/releases/download/v0.31.0/snapclient_0.31.0-1_arm64_bookworm.deb
```

```bash
sudo tee /etc/default/snapclient > /dev/null << 'EOF'
SNAPCLIENT_OPTS="--soundcard default:CARD=sndrpihifiberry --mixer hardware:'Digital'"
EOF
```