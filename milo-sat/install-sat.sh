#!/bin/bash
# Milo Sat - Installation Script v1.2 (with uninstall + default volume)

set -e

MILO_SAT_USER="milo-sat"
MILO_SAT_HOME="/home/$MILO_SAT_USER"
MILO_SAT_APP_DIR="$MILO_SAT_HOME/milo-sat"
MILO_SAT_DATA_DIR="/var/lib/milo-sat"
MILO_SAT_REPO_BASE="https://raw.githubusercontent.com/leodurandfr/Milo/main/milo-sat"
REBOOT_REQUIRED=false

# Variables to store user choices
USER_HOSTNAME_CHOICE=""
USER_HIFIBERRY_CHOICE=""
HIFIBERRY_OVERLAY=""
CARD_NAME=""
MILO_PRINCIPAL_IP=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_banner() {
    echo -e "${BLUE}"
    echo "  __  __ _ _        ____        _   "
    echo " |  \/  (_) | ___  / ___|  __ _| |_ "
    echo " | |\/| | | |/ _ \ \___ \ / _\` | __|"
    echo " | |  | | | | (_) | ___) | (_| | |_ "
    echo " |_|  |_|_|_|\___/ |____/ \__,_|\__|"
    echo ""
    echo "Satellite Installation Script v1.2"
    echo -e "${NC}"
}

check_root() {
    if [[ $EUID -eq 0 ]]; then
        log_error "Ce script ne doit pas être exécuté en tant que root."
        exit 1
    fi
}

check_system() {
    log_info "Vérification du système..."
    
    if ! grep -q "Raspberry Pi" /proc/cpuinfo; then
        log_error "Ce script est conçu pour Raspberry Pi uniquement."
        exit 1
    fi
    
    ARCH=$(uname -m)
    if [[ "$ARCH" != "aarch64" ]]; then
        log_error "Architecture non supportée: $ARCH. Raspberry Pi OS 64bit requis."
        exit 1
    fi
    
    log_success "Système compatible détecté"
}

discover_milo_principal() {
    log_info "Recherche de Milo principal sur le réseau..."
    
    if MILO_PRINCIPAL_IP=$(getent hosts milo.local 2>/dev/null | awk '{print $1}' | head -1); then
        log_success "Milo principal trouvé à l'adresse: $MILO_PRINCIPAL_IP (milo.local)"
        return 0
    fi
    
    log_error "Impossible de trouver Milo principal automatiquement."
    echo ""
    read -p "Entrez l'adresse IP de Milo principal: " MILO_PRINCIPAL_IP
    if [[ -z "$MILO_PRINCIPAL_IP" ]]; then
        log_error "Adresse IP requise. Installation annulée."
        exit 1
    fi
}

collect_user_choices() {
    echo ""
    log_info "Configuration du satellite Milo - Répondez aux questions suivantes :"
    echo ""
    
    # 1. Hostname choice
    echo "Choisissez un numéro pour ce satellite (01-99):"
    while true; do
        read -p "Numéro du satellite [01]: " USER_HOSTNAME_CHOICE
        USER_HOSTNAME_CHOICE=${USER_HOSTNAME_CHOICE:-01}
        
        if [[ $USER_HOSTNAME_CHOICE =~ ^[0-9]{1,2}$ ]]; then
            USER_HOSTNAME_CHOICE=$(printf "%02d" $USER_HOSTNAME_CHOICE)
            log_success "Hostname: milo-sat-$USER_HOSTNAME_CHOICE"
            break
        else
            echo "Veuillez entrer un nombre entre 01 et 99."
        fi
    done
    
    # 2. HiFiBerry card choice
    echo ""
    log_info "Configuration de la carte audio HiFiBerry..."
    echo ""
    echo "Sélectionnez votre carte audio HiFiBerry:"
    echo ""
    echo "1) Amp2"
    echo "2) Amp4"
    echo "3) Amp4 Pro"
    echo "4) Amp100"
    echo "5) Beocreate 4CA"
    echo ""
    
    while true; do
        read -p "Votre choix [1]: " USER_HIFIBERRY_CHOICE
        USER_HIFIBERRY_CHOICE=${USER_HIFIBERRY_CHOICE:-1}
        
        case $USER_HIFIBERRY_CHOICE in
            1) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp2"; break;;
            2) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp4"; break;;
            3) HIFIBERRY_OVERLAY="hifiberry-amp4pro"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp4 Pro"; break;;
            4) HIFIBERRY_OVERLAY="hifiberry-amp100"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp100"; break;;
            5) HIFIBERRY_OVERLAY="hifiberry-dac"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Beocreate 4CA"; break;;
            *) echo "Choix invalide. Veuillez entrer un nombre entre 1 et 5.";;
        esac
    done
    
    echo ""
    log_success "Configuration terminée ! L'installation va maintenant se poursuivre automatiquement..."
    echo ""
    sleep 2
}

setup_hostname() {
    local new_hostname="milo-sat-$USER_HOSTNAME_CHOICE"
    local current_hostname=$(hostname)
    
    if [ "$current_hostname" != "$new_hostname" ]; then
        log_info "Configuration du hostname '$new_hostname'..."
        echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
        sudo hostnamectl set-hostname "$new_hostname"
        log_success "Hostname configuré"
        REBOOT_REQUIRED=true
    else
        log_success "Hostname '$new_hostname' déjà configuré"
    fi
}

configure_audio_hardware() {
    log_info "Configuration du hardware audio pour HiFiBerry..."
    
    local config_file="/boot/firmware/config.txt"
    
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
        if [[ ! -f "$config_file" ]]; then
            log_error "Fichier config.txt non trouvé"
            exit 1
        fi
    fi
    
    sudo cp "$config_file" "$config_file.backup.$(date +%Y%m%d_%H%M%S)"
    
    sudo sed -i '/^dtparam=audio=on/d' "$config_file"
    
    if grep -q "vc4-fkms-v3d" "$config_file"; then
        sudo sed -i 's/dtoverlay=vc4-fkms-v3d.*/dtoverlay=vc4-fkms-v3d,audio=off/' "$config_file"
    fi
    
    if grep -q "vc4-kms-v3d" "$config_file"; then
        sudo sed -i 's/dtoverlay=vc4-kms-v3d.*/dtoverlay=vc4-kms-v3d,noaudio/' "$config_file"
    fi
    
    if ! grep -q "$HIFIBERRY_OVERLAY" "$config_file"; then
        echo "" | sudo tee -a "$config_file"
        echo "# Milo Sat - HiFiBerry Audio" | sudo tee -a "$config_file"
        echo "dtoverlay=$HIFIBERRY_OVERLAY" | sudo tee -a "$config_file"
    fi
    
    log_success "Configuration audio hardware terminée"
    REBOOT_REQUIRED=true
}

install_dependencies() {
    log_info "Mise à jour du système..."
    
    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true
    
    echo 'Dpkg::Options {
       "--force-confdef";
       "--force-confnew";
    }' | sudo tee /etc/apt/apt.conf.d/local >/dev/null
    
    sudo apt update
    sudo apt upgrade -y
    
    log_info "Installation des dépendances minimales..."
    sudo apt install -y \
        python3-pip \
        python3-venv \
        python3-dev \
        libasound2-dev \
        wget \
        avahi-utils
    
    sudo rm -f /etc/apt/apt.conf.d/local
    
    log_success "Dépendances installées"
}

create_milo_sat_user() {
    if id "$MILO_SAT_USER" &>/dev/null; then
        log_info "Utilisateur '$MILO_SAT_USER' existe déjà"
    else
        log_info "Création de l'utilisateur '$MILO_SAT_USER'..."
        sudo useradd -m -s /bin/bash -G audio,sudo "$MILO_SAT_USER"
        log_success "Utilisateur '$MILO_SAT_USER' créé"
    fi
    
    sudo mkdir -p "$MILO_SAT_DATA_DIR"
    sudo chown -R "$MILO_SAT_USER:audio" "$MILO_SAT_DATA_DIR"
}

install_snapclient() {
    log_info "Installation de Snapclient..."

    # Install snapclient from Debian repositories
    # This automatically resolves dependencies according to the Debian version
    export DEBIAN_FRONTEND=noninteractive
    sudo apt install -y snapclient

    sudo systemctl stop snapclient.service || true
    sudo systemctl disable snapclient.service || true

    log_success "Snapclient installé"
}

download_milo_sat_files() {
    log_info "Téléchargement des fichiers Milo Sat..."
    
    sudo -u "$MILO_SAT_USER" mkdir -p "$MILO_SAT_APP_DIR"
    
    sudo -u "$MILO_SAT_USER" wget -O "$MILO_SAT_APP_DIR/main.py" "$MILO_SAT_REPO_BASE/app/main.py"
    sudo -u "$MILO_SAT_USER" wget -O "$MILO_SAT_APP_DIR/requirements.txt" "$MILO_SAT_REPO_BASE/app/requirements.txt"
    
    log_success "Fichiers Milo Sat téléchargés"
}

install_milo_sat_application() {
    log_info "Configuration de l'environnement Python pour Milo Sat..."
    
    cd "$MILO_SAT_APP_DIR"
    sudo -u "$MILO_SAT_USER" python3 -m venv venv
    sudo -u "$MILO_SAT_USER" bash -c "source venv/bin/activate && pip install --upgrade pip"
    sudo -u "$MILO_SAT_USER" bash -c "source venv/bin/activate && pip install -r requirements.txt"
    
    log_success "Application Milo Sat installée"
}

configure_alsa() {
    log_info "Configuration ALSA..."
    
    sudo tee /etc/asound.conf > /dev/null << 'EOF'
# ALSA configuration for Milo Sat
pcm.!default {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

ctl.!default {
    type hw
    card sndrpihifiberry
}
EOF
    
    log_success "Configuration ALSA terminée"
}

create_systemd_services() {
    log_info "Création des services systemd..."
    
    sudo tee /etc/systemd/system/milo-sat.service > /dev/null << EOF
[Unit]
Description=Milo Sat API Service
After=network.target

[Service]
Type=simple
User=$MILO_SAT_USER
Group=audio
WorkingDirectory=$MILO_SAT_APP_DIR
ExecStart=$MILO_SAT_APP_DIR/venv/bin/python3 main.py

Restart=always
RestartSec=5
TimeoutStopSec=10

Environment=MILO_PRINCIPAL_IP=$MILO_PRINCIPAL_IP

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    sudo tee /etc/systemd/system/milo-sat-snapclient.service > /dev/null << EOF
[Unit]
Description=Snapclient for Milo Sat
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$MILO_SAT_USER
Group=audio

ExecStart=/usr/bin/snapclient -h milo.local -p 1704 --logsink=system --soundcard default:CARD=$CARD_NAME --mixer hardware:'Digital'

Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    log_success "Services systemd créés"
}

enable_services() {
    log_info "Activation des services..."
    
    sudo systemctl daemon-reload
    sudo systemctl enable milo-sat.service
    sudo systemctl enable milo-sat-snapclient.service
    
    log_success "Services activés"
}

configure_sudoers() {
    log_info "Configuration des permissions sudo pour milo-sat..."
    
    sudo tee /etc/sudoers.d/milo-sat > /dev/null << 'EOF'
# Milo Sat - Permissions for automatic updates
milo-sat ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop milo-sat-snapclient.service
milo-sat ALL=(ALL) NOPASSWD: /usr/bin/systemctl start milo-sat-snapclient.service
milo-sat ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active milo-sat-snapclient.service
milo-sat ALL=(root) NOPASSWD:SETENV: /usr/bin/apt install *
EOF
    
    sudo chmod 0440 /etc/sudoers.d/milo-sat
    log_success "Permissions sudo configurées"
}


finalize_installation() {
    log_info "Finalisation de l'installation..."
    
    echo ""
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}  Installation Milo Sat terminée !${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo ""
    echo -e "${BLUE}Configuration :${NC}"
    echo "  • Hostname: milo-sat-$USER_HOSTNAME_CHOICE"
    echo "  • Utilisateur: $MILO_SAT_USER"
    echo "  • Application: $MILO_SAT_APP_DIR"
    echo "  • Carte audio: $HIFIBERRY_OVERLAY"
    echo "  • Milo principal: $MILO_PRINCIPAL_IP"
    echo "  • Volume par défaut: 16%"
    echo ""
    echo -e "${BLUE}Services :${NC}"
    echo "  • milo-sat.service (API sur port 8001)"
    echo "  • milo-sat-snapclient.service"
    echo ""
    
    if [[ "$REBOOT_REQUIRED" == "true" ]]; then
        echo -e "${YELLOW}⚠️  REDÉMARRAGE REQUIS${NC}"
        echo ""
        
        while true; do
            read -p "Redémarrer maintenant ? (O/n): " restart_choice
            case $restart_choice in
                [Nn]* )
                    echo -e "${YELLOW}Pensez à redémarrer manuellement avec: sudo reboot${NC}"
                    break
                    ;;
                [Oo]* | "" )
                    log_info "Redémarrage dans 5 secondes..."
                    sleep 5
                    sudo reboot
                    ;;
                * )
                    echo "Veuillez répondre par 'O' (oui) ou 'n' (non)."
                    ;;
            esac
        done
    else
        log_info "Démarrage des services..."
        sudo systemctl start milo-sat.service
        sudo systemctl start milo-sat-snapclient.service
        
        echo ""
        echo -e "${GREEN}✅ Milo Sat est prêt ! Il devrait apparaître automatiquement dans Milo principal.${NC}"
    fi
}

# === UNINSTALL FUNCTION ===

uninstall_milo_sat() {
    echo -e "${YELLOW}"
    echo "╔════════════════════════════════════════╗"
    echo "║  DÉSINSTALLATION DE MILO SAT           ║"
    echo "╔════════════════════════════════════════╗"
    echo -e "${NC}"
    echo ""
    echo -e "${RED}⚠️  Cette opération va supprimer :${NC}"
    echo "  • Les services milo-sat et milo-sat-snapclient"
    echo "  • L'utilisateur milo-sat et ses données"
    echo "  • La configuration audio HiFiBerry"
    echo "  • Snapclient"
    echo ""
    
    read -p "Êtes-vous sûr de vouloir continuer ? (oui/non): " confirm
    if [[ "$confirm" != "oui" ]]; then
        log_info "Désinstallation annulée"
        exit 0
    fi
    
    log_info "Début de la désinstallation..."
    echo ""
    
    # 1. Stop and disable services
    log_info "Arrêt des services..."
    sudo systemctl stop milo-sat.service 2>/dev/null || true
    sudo systemctl stop milo-sat-snapclient.service 2>/dev/null || true
    sudo systemctl disable milo-sat.service 2>/dev/null || true
    sudo systemctl disable milo-sat-snapclient.service 2>/dev/null || true
    
    # 2. Remove service files
    log_info "Suppression des services systemd..."
    sudo rm -f /etc/systemd/system/milo-sat.service
    sudo rm -f /etc/systemd/system/milo-sat-snapclient.service
    sudo systemctl daemon-reload
    
    # 3. Remove sudoers rules
    log_info "Suppression des règles sudoers..."
    sudo rm -f /etc/sudoers.d/milo-sat
    
    # 4. Uninstall Snapclient
    log_info "Désinstallation de Snapclient..."
    sudo apt remove -y snapclient 2>/dev/null || true
    sudo apt autoremove -y
    
    # 5. Remove ALSA configuration
    log_info "Suppression de la configuration ALSA..."
    sudo rm -f /etc/asound.conf
    
    # 6. Restore config.txt (remove HiFiBerry)
    log_info "Restauration de la configuration audio..."
    local config_file="/boot/firmware/config.txt"
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi
    
    if [[ -f "$config_file" ]]; then
        sudo sed -i '/# Milo Sat - HiFiBerry Audio/d' "$config_file"
        sudo sed -i '/dtoverlay=hifiberry-/d' "$config_file"
        
        # Re-enable built-in audio
        if ! grep -q "^dtparam=audio=on" "$config_file"; then
            echo "dtparam=audio=on" | sudo tee -a "$config_file" > /dev/null
        fi
        
        REBOOT_REQUIRED=true
    fi
    
    # 7. Remove application directories
    log_info "Suppression des fichiers de l'application..."
    sudo rm -rf "$MILO_SAT_APP_DIR"
    sudo rm -rf "$MILO_SAT_DATA_DIR"
    
    # 8. Remove milo-sat user
    log_info "Suppression de l'utilisateur milo-sat..."
    if id "$MILO_SAT_USER" &>/dev/null; then
        sudo userdel -r "$MILO_SAT_USER" 2>/dev/null || true
    fi
    
    # 9. Restore default hostname (optional)
    local current_hostname=$(hostname)
    if [[ "$current_hostname" == milo-sat-* ]]; then
        log_info "Restauration du hostname par défaut..."
        echo "raspberrypi" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\traspberrypi/" /etc/hosts
        sudo hostnamectl set-hostname "raspberrypi"
        REBOOT_REQUIRED=true
    fi
    
    echo ""
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}  Désinstallation terminée !${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo ""
    
    if [[ "$REBOOT_REQUIRED" == "true" ]]; then
        echo -e "${YELLOW}⚠️  REDÉMARRAGE REQUIS pour finaliser${NC}"
        echo ""
        
        while true; do
            read -p "Redémarrer maintenant ? (O/n): " restart_choice
            case $restart_choice in
                [Nn]* )
                    echo -e "${YELLOW}Pensez à redémarrer manuellement avec: sudo reboot${NC}"
                    break
                    ;;
                [Oo]* | "" )
                    log_info "Redémarrage dans 5 secondes..."
                    sleep 5
                    sudo reboot
                    ;;
                * )
                    echo "Veuillez répondre par 'O' (oui) ou 'n' (non)."
                    ;;
            esac
        done
    else
        log_success "Système nettoyé. Milo Sat a été complètement supprimé."
    fi
}

# === MAIN FUNCTION ===

main() {
    # Check if uninstall mode
    if [[ "$1" == "--uninstall" ]]; then
        show_banner
        check_root
        uninstall_milo_sat
        exit 0
    fi
    
    # Normal installation
    show_banner
    
    check_root
    check_system
    
    log_info "Début de l'installation de Milo Sat"
    echo ""
    
    discover_milo_principal
    collect_user_choices
    
    install_dependencies
    setup_hostname
    configure_audio_hardware
    
    create_milo_sat_user
    install_snapclient
    download_milo_sat_files
    install_milo_sat_application
    
    configure_alsa
    create_systemd_services
    enable_services
    configure_sudoers
    
    finalize_installation
}

main "$@"