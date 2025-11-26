#!/usr/bin/env bash
set -e

REPO_OWNER="m33ph"
REPO_NAME="wol-bot"
BRANCH="main"

BOT_DIR="$HOME/wol_bot"
SERVICE_NAME="wolbot"

echo "==============================================="
echo "           ОБНОВЛЕНИЕ TELEGRAM WOL BOT"
echo "==============================================="
echo

# Создаём временную директорию
TMP_DIR="$(mktemp -d)"

echo "Скачиваю последнюю версию из GitHub..."
curl -fsSL "https://codeload.github.com/$REPO_OWNER/$REPO_NAME/tar.gz/$BRANCH" \
    -o "$TMP_DIR/update.tar.gz"

echo "Распаковываю..."
tar -xzf "$TMP_DIR/update.tar.gz" -C "$TMP_DIR"

UPDATE_DIR="$TMP_DIR/${REPO_NAME}-${BRANCH}"

echo "Останавливаю сервис..."
if systemctl is-active --quiet "$SERVICE_NAME"; then
    sudo systemctl stop "$SERVICE_NAME"
fi

echo "Обновляю файлы бота..."
# Не перезаписываем .env!
rm -rf "$BOT_DIR"/*
cp -r "$UPDATE_DIR"/* "$BOT_DIR"/

echo "Восстанавливаю права..."
chmod +x "$BOT_DIR"/*.py 2>/dev/null || true
chmod +x "$BOT_DIR"/*.sh 2>/dev/null || true

echo "Перезапускаю сервис..."
sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE_NAME"

echo
echo "==============================================="
echo "        ОБНОВЛЕНИЕ УСПЕШНО ЗАВЕРШЕНО"
echo "==============================================="
echo "Версия обновлена из ветки: $BRANCH"
echo
