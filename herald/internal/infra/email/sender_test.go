package email

import (
	"net/mail"
	"strings"
	"testing"

	"herald/internal/config"
	"herald/internal/domain"
)

func TestNewSenderValidation(t *testing.T) {
	if _, err := NewSender(config.EmailConfig{From: "bad", Host: "smtp.example.com", Port: 587, TLSMode: "starttls"}); err == nil {
		t.Fatal("expected invalid from error")
	}
	if _, err := NewSender(config.EmailConfig{From: "noreply@example.com", Host: "smtp.example.com", Port: 587, TLSMode: "wrong"}); err == nil {
		t.Fatal("expected invalid TLS mode error")
	}
}

func TestBuildMultipartMessage(t *testing.T) {
	from, _ := mail.ParseAddress("Edium <noreply@example.com>")
	to, _ := mail.ParseAddress("candidate@example.com")
	raw, err := buildMessage(from, to, domain.EmailMessage{
		Subject:  "Заявка получена",
		TextBody: "Спасибо!",
		HTMLBody: "<p>Спасибо!</p>",
	})
	if err != nil {
		t.Fatalf("buildMessage: %v", err)
	}
	message := string(raw)
	for _, expected := range []string{`From: "Edium" <noreply@example.com>`, "To: <candidate@example.com>", "multipart/alternative", "text/plain", "text/html"} {
		if !strings.Contains(message, expected) {
			t.Fatalf("message does not contain %q:\n%s", expected, message)
		}
	}
}

func TestBuildMessageRejectsHeaderInjection(t *testing.T) {
	from, _ := mail.ParseAddress("noreply@example.com")
	to, _ := mail.ParseAddress("candidate@example.com")
	_, err := buildMessage(from, to, domain.EmailMessage{Subject: "ok\r\nBcc: x@example.com", TextBody: "body"})
	if err == nil {
		t.Fatal("expected subject validation error")
	}
}
