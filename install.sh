#!/usr/bin/env bash
set -e

### ----------------------------------------------------------
### Определяем пользователя и домашний каталог
### ----------------------------------------------------------
if [ -n "$SUDO_USER" ] && [ "$USER" = "root" ]; then
    REAL_USER="$SUDO_USER"
else
    REAL_USER="$USER"
fi

REAL_HOME=$(eval echo "~$REAL_USER")

BOT_DIR="$REAL_HOME/wol_bot"
DATA_DIR="$REAL_HOME/wol_bot_data"
ENV_FILE="$BOT_DIR/.env"

REPO_URL="https://raw.githubusercontent.com/m33ph/wol_bot/main"

### ----------------------------------------------------------
### Утилита: проверка IP
### ----------------------------------------------------------
valid_ip() {
    local ip=$1
    if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        IFS='.' read -r a b c d <<< "$ip"
        if (( a<=255 && b<=255 && c<=255 && d<=255 )); then
            return 0
        fi
    fi
    return 1
}

### ----------------------------------------------------------
### Запрашиваем данные
### ----------------------------------------------------------
echo "==============================================="
echo "           УСТАНОВКА TELEGRAM WOL BOT"
echo "==============================================="
echo
echo "Бот будет установлен в: $BOT_DIR"
echo "Данные будут храниться в: $DATA_DIR"
echo

printf "Введите Telegram Bot Token: "
read -r TG_TOKEN

printf "Введите Telegram User ID (кому разрешён доступ): "
read -r TG_UID

### --- OMV сервер ---
while true; do
    printf "Введите IP адрес OMV сервера: "
    read -r OMV_IP
    if valid_ip "$OMV_IP"; then break; fi
    echo "Неверный IP, попробуйте снова."
done

printf "Введите MAC-адрес OMV сервера: "
read -r OMV_MAC

### --- SSH для OMV ---
printf "Включить выключение через SSH? (yes/no): "
read -r SSH_YN
if [[ "$SSH_YN" = "yes" ]]; then
    SSH_ENABLED="true"
    printf "SSH логин для OMV: "
    read -r SSH_USER
    printf "Путь к приватному ключу SSH: "
    read -r SSH_KEY
else
    SSH_ENABLED="false"
    SSH_USER=""
    SSH_KEY=""
fi

### --- Роутер ---
while true; do
    printf "Введите IP роутера (OpenWrt): "
    read -r ROUTER_IP
    if valid_ip "$ROUTER_IP"; then break; fi
    echo "Неверный IP, попробуйте снова."
done

printf "SSH логин роутера: "
read -r ROUTER_USER
printf "Путь к приватному ключу роутера: "
read -r ROUTER_KEY

### ----------------------------------------------------------
### Создаём директории
### ----------------------------------------------------------
echo
echo "=== Создаю директории ==="
mkdir -p "$BOT_DIR"
mkdir -p "$DATA_DIR"

### ----------------------------------------------------------
### Скачиваем файлы бота
### ----------------------------------------------------------
echo "[1/5] Копирую Python-файлы..."

curl -fsSL "$REPO_URL/wol_bot.py" -o "$BOT_DIR/wol_bot.py"
curl -fsSL "$REPO_URL/wol_bot_conntrack.py" -o "$BOT_DIR/wol_bot_conntrack.py"
curl -fsSL "$REPO_URL/wol_bot_utils.py" -o "$BOT_DIR/wol_bot_utils.py"

### ----------------------------------------------------------
### Создаём .env
### ----------------------------------------------------------
echo "[2/5] Создаю .env файл..."

cat > "$ENV_FILE" <<EOF
WOL_BOT_TOKEN="$TG_TOKEN"
WOL_ALLOWED_USERS="$TG_UID"

WOL_SERVER_MAC="$OMV_MAC"
WOL_SERVER_IP="$OMV_IP"
WOL_SERVER_NAME="OMV Server"

WOL_SSH_ENABLED="$SSH_ENABLED"
WOL_SSH_USER="$SSH_USER"
WOL_SSH_KEY_FILE="$SSH_KEY"

ROUTER_IP="$ROUTER_IP"
ROUTER_SSH_USER="$ROUTER_USER"
ROUTER_SSH_KEY_FILE="$ROUTER_KEY"

TRAFFIC_DB_PATH="$DATA_DIR/traffic.db"
TRAFFIC_RETENTION_DAYS=730
EOF

### ----------------------------------------------------------
### Python и зависимости
### ----------------------------------------------------------
echo "[3/5] Устанавливаю зависимости..."

sudo apt update -y
sudo apt install -y python3 python3-pip python3-venv

python3 -m venv "$BOT_DIR/venv"
"$BOT_DIR/venv/bin/pip" install -U pip
"$BOT_DIR/venv/bin/pip" install python-telegram-bot python-dotenv paramiko wakeonlan

### ----------------------------------------------------------
### systemd сервис
### ----------------------------------------------------------
echo "[4/5] Устанавливаю systemd сервис..."

SERVICE_FILE="/etc/systemd/system/wol_bot.service"

sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=Telegram WoL Bot
After=network.target

[Service]
User=$REAL_USER
WorkingDirectory=$BOT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$BOT_DIR/venv/bin/python $BOT_DIR/wol_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable wol_bot.service
sudo systemctl restart wol_bot.service

### ----------------------------------------------------------
### Готово
### ----------------------------------------------------------
echo
echo "==============================================="
echo "         УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО"
echo "==============================================="
echo "Бот установлен в: $BOT_DIR"
echo "Логи: journalctl -u wol_bot -f"
echo
