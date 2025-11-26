#!/usr/bin/env bash
set -e

echo "==============================================="
echo "         УСТАНОВКА TELEGRAM WOL BOT"
echo "==============================================="

# ------------------------------
# Директории
# ------------------------------
USER_HOME=$(eval echo "~$USER")
INSTALL_DIR="$USER_HOME/wol_bot"
DATA_DIR="$USER_HOME/wol_bot_data"

echo ""
echo "Бот будет установлен в: $INSTALL_DIR"
echo "Данные будут храниться в: $DATA_DIR"
echo ""

mkdir -p "$INSTALL_DIR"
mkdir -p "$DATA_DIR"

# ------------------------------
# Сбор данных от пользователя
# ------------------------------
read -rp "Введите Telegram Bot Token: " TG_TOKEN
read -rp "Введите Telegram User ID (кому разрешён доступ): " TG_UID
read -rp "Введите MAC адрес сервера (например: aa:bb:cc:dd:ee:ff): " SRV_MAC
read -rp "Введите IP сервера OMV: " SRV_IP
read -rp "Введите SSH user OMV: " OMV_USER
read -rp "Введите путь к SSH ключу OMV: " OMV_KEY
read -rp "Введите IP роутера OpenWrt: " RT_IP
read -rp "Введите SSH user роутера: " RT_USER
read -rp "Введите путь к SSH ключу роутера: " RT_KEY

# ------------------------------
# Генерация .env
# ------------------------------
cat > "$INSTALL_DIR/.env" <<EOF
TG_BOT_TOKEN="$TG_TOKEN"
ADMIN_USER_IDS="$TG_UID"

SERVER_MAC="$SRV_MAC"
SERVER_IP="$SRV_IP"

SSH_USER_OMV="$OMV_USER"
SSH_KEY_OMV="$OMV_KEY"

ROUTER_IP="$RT_IP"
ROUTER_SSH_USER="$RT_USER"
ROUTER_SSH_KEY="$RT_KEY"

TRAFFIC_LAN_SUBNET="192.168.1."
TRAFFIC_GREP_PATTERN="192.168.1."
TRAFFIC_COLLECTION_ENABLED="true"
TRAFFIC_COLLECTION_INTERVAL="600"
TRAFFIC_DB_PATH="$DATA_DIR/traffic_stats.db"
TRAFFIC_RETENTION_DAYS="730"

LOG_PATH="$DATA_DIR/wol_bot.log"
KEEP_CHAT_MESSAGES="4"
EOF

echo ""
echo "Файл .env создан."

# ------------------------------
# Копирование Python-файла
# ------------------------------

echo ""
echo "=== Копирую wol_bot_conntrack.py ==="
curl -fsSL "https://raw.githubusercontent.com/m33ph/wol_bot/main/wol_bot_conntrack.py" -o "$INSTALL_DIR/wol_bot_conntrack.py"

# ------------------------------
# Создание venv и установка зависимостей
# ------------------------------

echo ""
echo "=== Создаю виртуальную среду ==="
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

echo "=== Устанавливаю зависимости ==="
pip install -U pip wheel
pip install python-telegram-bot==20.7 python-dotenv wakeonlan paramiko python-dateutil aiosqlite

deactivate

# ------------------------------
# Создаём systemd сервис
# ------------------------------

echo ""
echo "=== Создаю systemd службу ==="

sudo tee /etc/systemd/system/wolbot.service >/dev/null <<EOF
[Unit]
Description=Telegram WOL Bot
After=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
Environment="PYTHONUNBUFFERED=1"
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/wol_bot_conntrack.py
Restart=always
RestartSec=5
StandardOutput=append:$DATA_DIR/wol_bot.log
StandardError=append:$DATA_DIR/wol_bot.err.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable wolbot.service
sudo systemctl restart wolbot.service

echo ""
echo "==============================================="
echo "       Установка завершена!"
echo "    Статус: systemctl status wolbot.service"
echo "==============================================="
