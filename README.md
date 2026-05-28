# Zabbix to Confluence DB Inventory Sync

Скрипт для автоматической выгрузки информации о хостах баз данных из Zabbix в Confluence. Запускается по расписанию через GitLab CI/CD.

## Описание

Скрипт подключается к API Zabbix, собирает информацию о хостах из указанных групп (MSSQL, Oracle, PostgreSQL), определяет их статус, IP-адреса, версии СУБД и наличие VIP-шаблонов. Полученные данные агрегируются и публикуются на соответствующих страницах Confluence в виде HTML-таблиц.

### Функционал:
- Подсчет общего количества серверов, нод и VIP-адресов по префиксам АС.
- Детальная таблица по каждому хосту:
  - Ссылка на редактирование хоста в Zabbix.
  - IP-адрес.
  - Версия СУБД (получается из истории элемента данных).
  - Присоединенные шаблоны.
  - Группы хоста.
  - Статус (Активирован/Деактивирован) с цветовой индикацией.
- Отправка email-уведомления в случае ошибки выполнения.

## Требования

- Python 3.6+
- Библиотеки:
  ```bash
  pip install pyzabbix atlassian-python-api
  ```

## Настройка переменных окружения (GitLab CI/CD)

Для безопасной работы скрипта необходимо настроить следующие переменные в настройках вашего проекта GitLab (**Settings > CI/CD > Variables**):

| Variable | Description | Protected | Masked |
| :--- | :--- | :---: | :---: |
| `SA_Zabbix_Confluence_Password` | Пароль пользователя Confluence для записи страниц | Yes | Yes |
| `ZABBIX_API_TOKEN` | API Token для доступа к Zabbix | Yes | Yes |
| `EMAIL_AUTH_PASS` | Пароль от SMTP аккаунта для отправки уведомлений об ошибках | Yes | Yes |

*Примечание: Если вы используете другие имена переменных, обновите их в начале файла `zabbix_db_list.py` или в блоке `script` файла `.gitlab-ci.yml`.*

## Конфигурация скрипта

Отредактируйте блок `CONFIG` в начале файла `zabbix_db_list.py`:

1. **URL-адреса**: Укажите адреса вашего Zabbix и Confluence.
2. **SMTP**: Настройте параметры SMTP-сервера.
3. **VIP Template ID**: Укажите ID шаблона, который маркирует хост как VIP (по умолчанию `11207`).
4. **TASKS**: Добавьте или измените список задач `(GroupID, DB_Name, Confluence_Page_ID)`.

## GitLab CI/CD

Скрипт настроен на запуск по расписанию (Schedule). Пример конфигурации `.gitlab-ci.yml`:

```yaml
stages:
  - deploy

infra_integration:
  tags:
      - your-runner-tag
  stage: deploy
  artifacts: {}
  script:
    - ls -la
    - /bin/python3 zabbix_db_list.py
    - rm -rf $BUILDS
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule"
      when: always
      allow_failure: false
```

Не забудьте заменить `your-runner-tag` на тег вашего раннера в `.gitlab-ci.yml`.

## Запуск локально (для отладки)

Если нужно запустить скрипт локально, экспортируйте переменные окружения вручную:

```bash
export SA_Zabbix_Confluence_Password="your_password"
export ZABBIX_API_TOKEN="your_token"
export EMAIL_AUTH_PASS="your_smtp_password"

python3 zabbix_db_list.py
```
