# ~/.bash_profile - Auto-start Cage on tty1 only

# Source .bashrc if it exists
if [ -f ~/.bashrc ]; then
    . ~/.bashrc
fi

# Launch Cage only on tty1 (physical screen), not on SSH sessions
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec ~/.config/milo-cage-start.sh
fi
