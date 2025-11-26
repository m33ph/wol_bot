#!/usr/bin/env bash
set -e

echo "============================================="
echo "      УСТАНОВКА TELEGRAM WOL BOT"
echo "============================================="

USER_HOME="/home/$USER"
BOT_DIR="$USER_HOME/wol_bot"
DATA_DIR="$USER_HOME/wol_bot_data"
ENV_FILE="$BOT_DIR/wol.env"
SERVICE_FILE="/etc/systemd/system/wolbot.service"

echo
echo "Бот будет установлен в: $BOT_DIR"
echo "Данные будут храниться в: $DATA_DIR"
echo

# ---------------------------------------------------------
# Функция проверки корректности IP
# ---------------------------------------------------------
valid_ip() {
    local ip=$1
    local IFS=.
    local -a octets=($ip)

    [[ ${#octets[@]} -eq 4 ]] || return 1

    for o in "${octets[@]}"; do
        [[ $o =~ ^[0-9]+$ ]] || return 1
        (( o >= 0 && o <= 255 )) || return 1
    done

    return 0
}


# ---------------------------------------------------------
# СБОР ДАННЫХ ДЛЯ .env
# ---------------------------------------------------------

echo "=== Telegram настройки ==="
read -rp "Введите Telegram Bot Token: " TG_TOKEN
read -rp "Введите ваш Telegram User ID: " TG_ADMIN

echo
echo "=== Настройки сервера OMV ==="
read -rp "MAC адрес сервера OMV (aa:bb:cc:dd:ee:ff): " SERVER_MAC

while true; do
    read -rp "IP сервера OMV: " SERVER_IP
    valid_ip "$SERVER_IP" && break
    echo "Неверный IP, попробуйте снова."
done

read -rp "SSH пользователь OMV: " SSH_USER_OMV
read -rp "Путь к SSH ключу OMV (по умолчанию ~/.ssh/omv_key): " SSH_KEY_OMV
SSH_KEY_OMV=${SSH_KEY_OMV:-"$USER_HOME/.ssh/omv_key"}

echo
echo "=== Настройки роутера OpenWrt ==="
while true; do
    read -rp "IP роутера OpenWrt: " ROUTER_IP
    valid_ip "$ROUTER_IP" && break
    echo "Неверный IP, попробуйте снова."
done

read -rp "SSH пользователь роутера (обычно root): " ROUTER_SSH_USER
read -rp "Путь к SSH ключу роутера (по умолчанию ~/.ssh/router_key): " ROUTER_SSH_KEY
ROUTER_SSH_KEY=${ROUTER_SSH_KEY:-"$USER_HOME/.ssh/router_key"}

echo
echo "=== Настройки трафика ==="
read -rp "Подсеть LAN (например 192.168.1.): " LAN_SUBNET
LAN_SUBNET=${LAN_SUBNET:-"192.168.1."}

read -rp "Интервал сбора conntrack (секунды, по умолчанию 600): " TRAFFIC_INTERVAL
TRAFFIC_INTERVAL=${TRAFFIC_INTERVAL:-600}

read -rp "Период хранения статистики (дней, по умолчанию 730): " RETENTION
RETENTION=${RETENTION:-730}

echo
echo "=== Создаю директории ==="
mkdir -p "$BOT_DIR" "$DATA_DIR"

# ---------------------------------------------------------
# Копируем файлы
# ---------------------------------------------------------

echo "[1/7] Копирую Python-файлы..."
cp wol_bot_conntrack.py "$BOT_DIR/"
cp requirements.txt "$BOT_DIR/"

echo "[2/7] Создаю файл настроек $ENV_FILE ..."

cat > "$ENV_FILE" <<EOF
TG_BOT_TOKEN="$TG_TOKEN"
ADMIN_USER_IDS="$TG_ADMIN"

SERVER_MAC="$SERVER_MAC"
SERVER_IP="$SERVER_IP"
SSH_USER_OMV="$SSH_USER_OMV"
SSH_KEY_OMV="$SSH_KEY_OMV"

ROUTER_IP="$ROUTER_IP"
ROUTER_SSH_USER="$ROUTER_SSH_USER"
ROUTER_SSH_KEY="$ROUTER_SSH_KEY"

TRAFFIC_LAN_SUBNET="$LAN_SUBNET"
TRAFFIC_GREP_PATTERN="$LAN_SUBNET"
TRAFFIC_COLLECTION_ENABLED="true"
TRAFFIC_COLLECTION_INTERVAL="$TRAFFIC_INTERVAL"
TRAFFIC_DB_PATH="$DATA_DIR/traffic_stats.db"
TRAFFIC_RETENTION_DAYS="$RETENTION"

LOG_PATH="$DATA_DIR/wol_bot_conntrack.log"
KEEP_CHAT_MESSAGES="4"
EOF

# ---------------------------------------------------------
# Python виртуальное окружение
# ---------------------------------------------------------

echo "[3/7] Создаю Python venv..."
python3 -m venv "$BOT_DIR/venv"
source "$BOT_DIR/venv/bin/activate"

echo "[4/7] Устанавливаю зависимости..."
pip install --upgrade pip
pip install -r "$BOT_DIR/requirements.txt"

# ---------------------------------------------------------
# Права
# ---------------------------------------------------------

echo "[5/7] Выставляю права безопасности..."

chown -R "$USER:$USER" "$BOT_DIR" "$DATA_DIR"
chmod 700 "$BOT_DIR" "$DATA_DIR"
chmod 600 "$ENV_FILE"
chmod 600 "$BOT_DIR/wol_bot_conntrack.py"

# ---------------------------------------------------------
# Systemd unit
# ---------------------------------------------------------

echo "[6/7] Устанавливаю systemd сервис..."

cp wolbot.service "$SERVICE_FILE"
# Подставляем имя пользователя
sed -i "s|%u|$USER|g" "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable wolbot
systemctl restart wolbot

# ---------------------------------------------------------
# Готово
# ---------------------------------------------------------

echo
echo "============================================="
echo "   УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО!"
echo "============================================="
echo
echo "Файл конфигурации: $ENV_FILE"
echo "Перезапуск: sudo systemctl restart wolbot"
echo "Логи:       journalctl -u wolbot -f"
echo
