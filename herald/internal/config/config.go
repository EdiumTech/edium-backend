package config

import (
	"github.com/caarlos0/env/v10"
	"github.com/joho/godotenv"
)

type Config struct {
	App      AppConfig
	Postgres PostgresConfig
	NATS     NATSConfig
	Telegram TelegramConfig
	SMS      SMSConfig
	Email    EmailConfig
	OTel     OTelConfig
	Doorman  DoormanConfig
	Firebase FirebaseConfig
}

type AppConfig struct {
	Port int `env:"APP_PORT" envDefault:"80"`
}

type PostgresConfig struct {
	DSN string `env:"POSTGRES_DSN,required"`
}

type NATSConfig struct {
	URL string `env:"NATS_URL" envDefault:"nats://nats:4222"`
}

type TelegramConfig struct {
	BotToken string `env:"TELEGRAM_BOT_TOKEN"`
}

// AllowedPhones — белый список телефонов, на которые разрешена отправка (пусто = без ограничения).
// BlockedPhones — номера, на которые SMS не отправляются; проверяется после белого списка.
type SMSConfig struct {
	APIKey        string   `env:"SMS_API_KEY"`
	AllowedPhones []string `env:"SMS_ALLOWED_PHONES" envSeparator:","`
	BlockedPhones []string `env:"SMS_BLOCKED_PHONES" envSeparator:","`
}

type EmailConfig struct {
	APIKey   string `env:"EMAIL_API_KEY"`
	Host     string `env:"SMTP_HOST"`
	Port     int    `env:"SMTP_PORT" envDefault:"587"`
	Username string `env:"SMTP_USERNAME"`
	Password string `env:"SMTP_PASSWORD"`
	From     string `env:"SMTP_FROM"`
	TLSMode  string `env:"SMTP_TLS_MODE" envDefault:"starttls"`
}

func (c EmailConfig) Enabled() bool {
	return c.Host != "" && c.From != ""
}

type OTelConfig struct {
	Endpoint    string `env:"OTEL_ENDPOINT" envDefault:"jaeger:4317"`
	ServiceName string `env:"OTEL_SERVICE_NAME" envDefault:"herald"`
}

type DoormanConfig struct {
	JWKSEndpoint string `env:"DOORMAN_JWKS_URL" envDefault:"http://doorman/.well-known/jwks.json"`
}

type FirebaseConfig struct {
	CredentialsJSON string `env:"FIREBASE_CREDENTIALS_JSON"`
}

func Load() (*Config, error) {
	_ = godotenv.Load()

	cfg := &Config{}
	if err := env.Parse(cfg); err != nil {
		return nil, err
	}

	return cfg, nil
}
