package telegram

import (
	"fmt"
	"herald/internal/config"
	"net/http"
	"strings"
	"time"

	tgbotapi "github.com/go-telegram-bot-api/telegram-bot-api/v5"
)

func New(cfg config.TelegramConfig) (*tgbotapi.BotAPI, error) {
	client := &http.Client{Timeout: 15 * time.Second}
	bot, err := tgbotapi.NewBotAPIWithClient(cfg.BotToken, tgbotapi.APIEndpoint, client)
	if err != nil {
		// telegram-bot-api includes the bot token in request URLs inside errors.
		// Redact it before the error reaches structured application logs.
		message := strings.ReplaceAll(err.Error(), cfg.BotToken, "[REDACTED]")
		return nil, fmt.Errorf("create telegram bot: %s", message)
	}
	return bot, nil
}
