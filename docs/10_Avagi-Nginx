
# 09 · Configuration Avahi + Nginx

Ce guide permet d'accéder à votre application Milo via `http://milo.local` au lieu de `http://192.168.1.152:3000`.-

## 1. Configuration Avahi (mDNS)

### Installation d'Avahi

```bash
sudo apt install avahi-daemon avahi-utils
```


### Configuration Avahi

Configuration complète pour la découverte réseau `/etc/avahi/avahi-daemon.conf`  :

```bash
sudo  tee /etc/avahi/avahi-daemon.conf > /dev/null <<  'EOF'
[server]
host-name=milo
domain-name=local
use-ipv4=yes
use-ipv6=no
allow-interfaces=eth0,wlan0
deny-interfaces=docker0,lo
ratelimit-interval-usec=1000000
ratelimit-burst=1000

[wide-area]
enable-wide-area=no

[publish]
publish-hinfo=no
publish-workstation=yes
publish-domain=yes
publish-addresses=yes
publish-aaaa-on-ipv4=no
publish-a-on-ipv6=no
EOF
```

### Service Avahi pour Milo (optionnel - pas utilisé dans install.sh)

Créer un service Avahi pour annoncer Milo sur le réseau :

```bash
sudo nano /etc/avahi/services/milo.service
```

Contenu :

```xml
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Milo Audio System on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>80</port>
    <txt-record>path=/</txt-record>
    <txt-record>description=Milo Audio Control Interface</txt-record>
  </service>
</service-group>

```

### Redémarrer Avahi

```bash
sudo systemctl enable avahi-daemon
sudo systemctl restart avahi-daemon

```

## 2. Configuration Nginx

### Installation

```bash
sudo apt install nginx

```

### Configuration du site Milo

Créer `/etc/nginx/sites-available/milo` :

```bash
sudo nano /etc/nginx/sites-available/milo
```

Contenu du fichier :
```nginx
server {
   listen 80;
   server_name milo.local;
   
   location / {
       proxy_pass http://127.0.0.1:3000;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
   }
   
   location /api/ {
       proxy_pass http://127.0.0.1:8000;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
   }
   
   location /ws {
       proxy_pass http://127.0.0.1:8000;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_read_timeout 86400;
       proxy_send_timeout 86400;
       proxy_buffering off;
   }
}

```

### Activation du site

```bash
# Activer le site milo
sudo ln -s /etc/nginx/sites-available/milo /etc/nginx/sites-enabled/

# Supprimer le site par défaut
sudo rm -f /etc/nginx/sites-enabled/default

# Tester la configuration
sudo nginx -t

# Démarrer et activer nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

### Redémarrer le service frontend

```bash
sudo systemctl restart milo-frontend
```


## Vérification

1.  **Test résolution DNS** :
    
    ```bash
    ping milo.local
    
    ```
    
2.  **Test découverte réseau Avahi** :
    
    ```bash
    # Découvrir les services sur le réseau
    avahi-browse -at
    
    # Rechercher spécifiquement Milo
    avahi-browse -r _http._tcp
    
    ```
    
3.  **Test accès web** :
    
    -   Depuis le Raspberry Pi : `http://milo.local`
    -   Depuis un autre appareil du réseau : `http://milo.local`

4.  **Vérification des services** :
    
    ```bash
    sudo systemctl status avahi-daemon
    sudo systemctl status nginx
    sudo systemctl status milo-frontend
    
    ```
