package worker

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"herald/internal/domain"
)

type mockEmailSender struct {
	message domain.EmailMessage
	err     error
}

func (m *mockEmailSender) Send(_ context.Context, message domain.EmailMessage) error {
	m.message = message
	return m.err
}

func emailTask(message domain.EmailMessage) domain.Task {
	payload, _ := json.Marshal(message)
	return makeRawTask(payload)
}

func TestEmailProcessorSuccess(t *testing.T) {
	tasks := &mockTaskRepo{}
	sender := &mockEmailSender{}
	w := NewEmailProcessor(tasks, sender)
	message := domain.EmailMessage{To: "team@example.com", Subject: "New candidate", TextBody: "Saved"}
	if err := w.processTask(context.Background(), emailTask(message)); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if sender.message != message {
		t.Fatalf("unexpected message: %+v", sender.message)
	}
}

func TestEmailProcessorSendError(t *testing.T) {
	w := NewEmailProcessor(&mockTaskRepo{}, &mockEmailSender{err: errors.New("smtp unavailable")})
	if err := w.processTask(context.Background(), emailTask(domain.EmailMessage{To: "a@example.com", Subject: "S", TextBody: "B"})); err == nil {
		t.Fatal("expected send error")
	}
}

func TestEmailProcessorBadPayload(t *testing.T) {
	w := NewEmailProcessor(&mockTaskRepo{}, &mockEmailSender{})
	if err := w.processTask(context.Background(), makeRawTask([]byte("bad"))); err == nil {
		t.Fatal("expected decode error")
	}
}

func TestEmailRetryAfterIsBounded(t *testing.T) {
	if got := emailRetryAfter(1); got != 30*time.Second {
		t.Fatalf("first retry = %s", got)
	}
	if got := emailRetryAfter(100); got != 8*time.Minute {
		t.Fatalf("bounded retry = %s", got)
	}
}
