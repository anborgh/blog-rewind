#!/usr/bin/env bash
# Ставит memorybot системным сервисом. Запускать из корня репозитория:
#
#   sudo deploy/install.sh
#
# Скрипт идемпотентный: чтобы обновить бота, сделайте git pull и запустите снова.
# Файл .env и архив в data/ при обновлении не трогаются.
set -euo pipefail

APP_USER="${APP_USER:-memorybot}"
APP_DIR="${APP_DIR:-/opt/memorybot}"
SERVICE="${SERVICE:-memorybot}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
    printf '\033[31mОшибка:\033[0m %s\n' "$*" >&2
    exit 1
}

step() {
    printf '\n\033[1m==> %s\033[0m\n' "$*"
}

[[ ${EUID} -eq 0 ]] || die "нужны права root — запустите через sudo"
[[ -f "${SOURCE_DIR}/pyproject.toml" ]] || die "рядом со скриптом нет проекта memorybot"

step "Проверяю uv"
if ! command -v uv >/dev/null 2>&1; then
    command -v curl >/dev/null 2>&1 || die "нет ни uv, ни curl — установите curl и повторите"
    curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
UV="$(command -v uv)"
echo "uv: ${UV} ($(${UV} --version))"

step "Создаю пользователя ${APP_USER}"
if id -u "${APP_USER}" >/dev/null 2>&1; then
    echo "уже есть"
else
    useradd --system --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
    echo "создан"
fi

step "Копирую проект в ${APP_DIR}"
mkdir -p "${APP_DIR}/data"
tar -C "${SOURCE_DIR}" \
    --exclude=./.git \
    --exclude=./.venv \
    --exclude=./data \
    --exclude=./.env \
    --exclude=./.python \
    --exclude=./.pytest_cache \
    --exclude=./.ruff_cache \
    --exclude='./**/__pycache__' \
    -cf - . | tar -C "${APP_DIR}" -xf -

step "Собираю окружение"
# Интерпретатор кладём внутрь APP_DIR: если системного Python 3.12 нет,
# uv скачает свой, и он останется доступным сервисному пользователю.
env UV_PYTHON_INSTALL_DIR="${APP_DIR}/.python" \
    "${UV}" sync --frozen --no-dev --project "${APP_DIR}"

step "Расставляю права"
chown -R root:root "${APP_DIR}"
chmod -R a+rX "${APP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/data"
chmod 750 "${APP_DIR}/data"

if [[ ! -f "${APP_DIR}/.env" ]]; then
    # Пустой BOT_TOKEN лучше примера: сервис сразу скажет, чего ему не хватает.
    sed 's|^BOT_TOKEN=.*|BOT_TOKEN=|' "${SOURCE_DIR}/.env.example" >"${APP_DIR}/.env"
    NEEDS_TOKEN=1
else
    NEEDS_TOKEN=0
fi
chown "root:${APP_USER}" "${APP_DIR}/.env"
chmod 640 "${APP_DIR}/.env"

step "Ставлю сервис ${SERVICE}"
UNIT="/etc/systemd/system/${SERVICE}.service"
sed -e "s|/opt/memorybot|${APP_DIR}|g" \
    -e "s|^User=memorybot|User=${APP_USER}|" \
    -e "s|^Group=memorybot|Group=${APP_USER}|" \
    "${SOURCE_DIR}/deploy/memorybot.service" >"${UNIT}"
echo "юнит: ${UNIT}"

if systemctl daemon-reload 2>/dev/null; then
    systemctl enable "${SERVICE}" >/dev/null
    echo "включён в автозапуск"
    if systemctl is-active --quiet "${SERVICE}"; then
        systemctl restart "${SERVICE}"
        echo "перезапущен"
    fi
    SYSTEMD=1
else
    echo "systemd недоступен (контейнер?) — юнит записан, но не активирован"
    SYSTEMD=0
fi

printf '\n\033[1mГотово.\033[0m\n'
if [[ ${SYSTEMD} -eq 0 ]]; then
    cat <<EOF

Здесь systemd не запущен, поэтому проверить бота можно вручную:
  sudo -u ${APP_USER} env -C ${APP_DIR} ${APP_DIR}/.venv/bin/memorybot run
EOF
elif [[ ${NEEDS_TOKEN} -eq 1 ]]; then
    cat <<EOF

Осталось два шага:

  1. Впишите токен от @BotFather:
       sudoedit ${APP_DIR}/.env
  2. Запустите бота:
       sudo systemctl start ${SERVICE}
       sudo journalctl -u ${SERVICE} -f

Архив прошлых лет поднимается из экспорта Telegram Desktop:
  sudo -u ${APP_USER} ${APP_DIR}/.venv/bin/memorybot import /путь/к/result.json
EOF
else
    echo
    echo "Настройки в ${APP_DIR}/.env сохранены. Состояние: systemctl status ${SERVICE}"
fi
