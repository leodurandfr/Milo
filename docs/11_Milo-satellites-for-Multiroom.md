


```bash
sudo tee /etc/default/snapclient > /dev/null << 'EOF'
SNAPCLIENT_OPTS="--soundcard default:CARD=sndrpihifiberry --mixer hardware:'Digital'"
EOF
```