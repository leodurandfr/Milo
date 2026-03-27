#!/bin/bash
# Milo - Base System Setup
#
# Installs base dependencies, configures hostname, creates the milo user,
# clones the application, and removes PulseAudio/PipeWire.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"
MILO_REPO="${MILO_REPO:-https://github.com/leodurandfr/Milo.git}"
MILO_BRANCH="${MILO_BRANCH:-main}"
REQUIRED_HOSTNAME="${REQUIRED_HOSTNAME:-milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_dependencies() {
    log_info "Updating system..."

    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true

    echo 'Dpkg::Options {
       "--force-confdef";
       "--force-confnew";
    }' | sudo tee /etc/apt/apt.conf.d/local >/dev/null

    sudo apt update
    sudo apt upgrade -y

    log_info "Installing base dependencies..."
		# Configuration optimized for Raspberry Pi OS Lite
        sudo apt install -y \
            git python3-pip python3-venv python3-dev libasound2-dev libssl-dev \
            cmake build-essential pkg-config swig liblgpio-dev nodejs npm wget unzip \
            fontconfig mpv libinput-tools bc eject libdiscid0 libdiscid-dev sg3-utils \
            fonts-noto fonts-noto-cjk fonts-lohit-deva fonts-noto-color-emoji

    log_info "Updating Node.js and npm..."
    sudo npm install -g n
    sudo n stable
    sudo npm install -g npm@latest
    hash -r

    sudo rm -f /etc/apt/apt.conf.d/local

    log_success "Dependencies installed"
}

configure_hostname() {
    local new_hostname="$1"
    echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
    sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
    sudo hostnamectl set-hostname "$new_hostname"
}

setup_hostname() {
    local current_hostname
    current_hostname=$(hostname)

    if [ "$current_hostname" != "$REQUIRED_HOSTNAME" ]; then
        log_info "Configuring hostname '$REQUIRED_HOSTNAME'..."
        configure_hostname "$REQUIRED_HOSTNAME"
        log_success "Hostname configured"
    else
        log_success "Hostname '$REQUIRED_HOSTNAME' already configured"
    fi
}

create_milo_user() {
    if id "$MILO_USER" &>/dev/null; then
        log_info "User '$MILO_USER' already exists"
    else
        log_info "Creating user '$MILO_USER'..."
        sudo useradd -m -s /bin/bash "$MILO_USER"
        sudo usermod -aG audio,video,bluetooth,input,cdrom "$MILO_USER"
        log_success "User '$MILO_USER' created"
    fi

    sudo mkdir -p "$MILO_DATA_DIR"
    sudo mkdir -p "$MILO_DATA_DIR/cd_covers"
    sudo chown -R "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR"
}

install_milo_application() {
    log_info "Cloning and configuring Milo..."

    cd /tmp

    if [[ -d "$MILO_APP_DIR" ]]; then
        log_warning "Directory $MILO_APP_DIR already exists, removing..."
        sudo rm -rf "$MILO_APP_DIR"
    fi

    sudo -u "$MILO_USER" git clone --branch "$MILO_BRANCH" --single-branch "$MILO_REPO" "$MILO_APP_DIR"
    cd "$MILO_APP_DIR"

    log_info "Configuring Python environment..."
    sudo -u "$MILO_USER" python3 -m venv venv
    sudo -u "$MILO_USER" bash -c 'source venv/bin/activate && pip install --upgrade pip'
    sudo -u "$MILO_USER" bash -c 'source venv/bin/activate && pip install -r requirements.txt'

    log_info "Building frontend..."
    cd frontend
    sudo -u "$MILO_USER" npm install
    sudo -u "$MILO_USER" npm run build
    cd ..

    log_success "Milo application installed"
}

fix_nginx_permissions() {
    log_info "Configuring permissions for nginx..."

    sudo chmod 755 /home/milo
    sudo chmod 755 /home/milo/milo
    sudo chmod 755 /home/milo/milo/frontend
    sudo chmod -R 755 /home/milo/milo/frontend/dist

    sudo chown -R "$MILO_USER:$MILO_USER" /home/milo/milo/frontend/dist

    log_success "Nginx permissions configured"
}

suppress_pulseaudio() {
    log_info "Removing PulseAudio/PipeWire..."
    sudo apt remove -y pulseaudio pipewire || true
    sudo apt autoremove -y
    log_success "PulseAudio/PipeWire removed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_dependencies
    setup_hostname
    create_milo_user
    install_milo_application
    fix_nginx_permissions
    suppress_pulseaudio
    log_success "Base system setup complete"
fi
