# zvonkodigital

Этот репозиторий содержит вспомогательный Python-скрипт для выполнения PKCE OAuth-авторизации на `https://account.zvonkodigital.com`.

## Требования
- Python 3.11+
- Установленные пакеты: `requests`, `beautifulsoup4`

Установите зависимости:

```bash
pip install requests beautifulsoup4
```

## Использование
Запустите скрипт, передав логин и пароль:

```bash
python zvonkodigital_auth.py --username YOUR_LOGIN --password YOUR_PASSWORD
```

Скрипт:
1. Генерирует `code_verifier` и `code_challenge` для PKCE.
2. Загружает страницу логина, извлекает CSRF-токен и отправляет форму.
3. Извлекает `authorization_code` из редиректа.
4. Обменивает код на `access_token` и `refresh_token`, выводя ответ в формате JSON.

При неверных учётных данных скрипт вернёт сообщение об ошибке в stderr и завершится с кодом 1.
