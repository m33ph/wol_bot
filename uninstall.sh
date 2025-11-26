#!/usr/bin/env bash
set -e

# Если запускается через pipe (curl | bash)
if [ ! -t 0 ]; then
    export NONINTERACTIVE=1
else
    export NONINTERACTIVE=0
fi

echo "==============================================="
echo "      УДАЛЕНИЕ TELEGRAM WOL BOT"
echo "==============================================="
echo

if [ "$NONINTERACTIVE" -eq 0 ]; then
    read -rp "Вы уверены, что хотите удалить бота и все его данные? (y/N): " confirm
    confirm=${confirm,,}
    if [[ "$confirm" != "y" && "$confirm" != "yes" ]]; then
        echo "Отмена."
        exit 0
    fi
else
    echo "Запуск внутри pipe — подтверждение отключено."
    echo "Продолжаю удаление без вопросов."
fi

USER_HOME="/home/$USER"
BOT_DIR="$USER_HOME/wol_bot"
DATA_DIR="$USER_HOME/wol_bot_data"
SERVICE_FILE="/etc/systemd/system/wolbot.service"

echo
echo "Останавливаю сервис..."
if systemctl is-active --quiet wolbot; then
    sudo systemctl stop wolbot || true
fi

echo "Отключаю автозапуск..."
if systemctl is-enabled --quiet wolbot; then
    sudo systemctl disable wolbot || true
fi

echo "Удаляю systemd unit..."
if [[ -f "$SERVICE_FILE" ]]; then
    sudo rm -f "$SERVICE_FILE"
    sudo systemctl daemon-reload
fi

echo
echo "Удаляю директории:"
echo " - $BOT_DIR"
echo " - $DATA_DIR"
rm -rf "$BOT_DIR" "$DATA_DIR"

echo
echo "Удаляю временные файлы..."
rm -rf /tmp/wolbot_update 2>/dev/null || true

echo
echo "Очищаю переменные окружения..."
unset TG_BOT_TOKEN ADMIN_USER_IDS SERVER_MAC SERVER_IP
unset SSH_USER_OMV SSH_KEY_OMV ROUTER_IP ROUTER_SSH_USER ROUTER_SSH_KEY
unset TRAFFIC_LAN_SUBNET TRAFFIC_GREP_PATTERN TRAFFIC_COLLECTION_ENABLED
unset TRAFFIC_COLLECTION_INTERVAL TRAFFIC_DB_PATH TRAFFIC_RETENTION_DAYS
unset LOG_PATH KEEP_CHAT_MESSAGES

echo
echo "==============================================="
echo "        УДАЛЕНИЕ ЗАВЕРШЕНО"
echo "==============================================="
echo "Если потребуется — установите бота заново:"
echo "curl -fsSL https://raw.githubusercontent.com/USERNAME/wol-bot/main/install.sh | bash"
echo
