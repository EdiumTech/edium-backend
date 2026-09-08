# edium-backend

Серверная часть Edium - Микросервисы + ML-пайплайн.

## Архитектура

```mermaid
flowchart TD
    Client(["Клиент\n(iOS / Android)"])
    NATS(["NATS JetStream"])
    TG["Telegram API"]
    SMTP["SMTP provider"]
    DS["DeepSeek API"]
    Firebase["Firebase API"]

    Caddy["Caddy"]

    subgraph services ["Core"]
        Caesar["Caesar\nPG"]
        Riddler["Riddler\nPG · Redis"]
        Doorman["Doorman\nPG · Redis"]
        Herald["Herald\nPG"]
        Louvre["Louvre\nPG · S3"]
    end

    subgraph ml ["ML"]
        Sphinx["Sphinx\nPG"]
        Charon["Charon\nPG · Redis"]
    end



    Client --> Caddy
    Caddy --> Doorman & Caesar & Riddler & Louvre & Herald

    Caesar & Louvre & Riddler & Herald --> Doorman

    Doorman & Herald & Caesar & Riddler & Charon & Sphinx <-.-> NATS

    Herald --> TG
    Herald --> SMTP
    Charon --> DS
    Herald --> Firebase
```

> Пунктир — асинхронные события через NATS

> Сплошная - синхронные запросы

## Сервисы

| Сервис | Описание                                    |
|--------|---------------------------------------------|
| **Doorman** | Аутентификация: OTP, JWT (access + refresh) |
| **Caesar** | Пользователи, классы, курсы, модули         |
| **Riddler** | Квизы, сессии прохождения, результаты       |
| **Herald** | Уведомления, email (SMTP), Telegram bot, SMS |
| **Charon** | Прокси к LLM (DeepSeek / OpenAI)            |
| **Sphinx** | Генерация квизов по тексту (Python + LLM)   |
| **Louvre** | Хранение изображений                        |
