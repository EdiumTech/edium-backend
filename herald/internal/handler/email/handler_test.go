package email

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"herald/internal/domain"

	"github.com/gin-gonic/gin"
)

type mockScheduler struct {
	created bool
	err     error
	typeGot domain.TaskType
	keyGot  string
	payload domain.EmailMessage
}

func (m *mockScheduler) ScheduleUnique(_ context.Context, taskType domain.TaskType, payload []byte, key string) (bool, error) {
	m.typeGot = taskType
	m.keyGot = key
	_ = json.Unmarshal(payload, &m.payload)
	return m.created, m.err
}

func setupRouter(tasks TaskScheduler, apiKey string, enabled bool) *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	NewHandler(tasks, apiKey, enabled).Register(r.Group("/herald/v1"))
	return r
}

func performRequest(r http.Handler, apiKey, idempotencyKey string, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/herald/v1/emails", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	if idempotencyKey != "" {
		req.Header.Set("Idempotency-Key", idempotencyKey)
	}
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func TestEnqueueEmail(t *testing.T) {
	tasks := &mockScheduler{created: true}
	r := setupRouter(tasks, "secret", true)
	w := performRequest(r, "secret", "candidate:abc:team", `{
		"to":"team@example.com",
		"subject":"Новая заявка",
		"text_body":"Заявка сохранена"
	}`)

	if w.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d: %s", w.Code, w.Body.String())
	}
	if tasks.typeGot != domain.EmailDelivery || tasks.keyGot != "candidate:abc:team" {
		t.Fatalf("unexpected scheduled task: type=%s key=%s", tasks.typeGot, tasks.keyGot)
	}
	if tasks.payload.To != "team@example.com" || tasks.payload.TextBody != "Заявка сохранена" {
		t.Fatalf("unexpected payload: %+v", tasks.payload)
	}
}

func TestEnqueueEmailDuplicateIsAccepted(t *testing.T) {
	r := setupRouter(&mockScheduler{created: false}, "secret", true)
	w := performRequest(r, "secret", "candidate:abc:team", `{"to":"team@example.com","subject":"Subject","text_body":"Body"}`)
	if w.Code != http.StatusAccepted || !bytes.Contains(w.Body.Bytes(), []byte(`"duplicate"`)) {
		t.Fatalf("expected accepted duplicate, got %d: %s", w.Code, w.Body.String())
	}
}

func TestEnqueueEmailAuthAndConfiguration(t *testing.T) {
	t.Run("wrong API key", func(t *testing.T) {
		w := performRequest(setupRouter(&mockScheduler{}, "secret", true), "wrong", "key", `{"to":"a@example.com","subject":"S","text_body":"B"}`)
		if w.Code != http.StatusUnauthorized {
			t.Fatalf("expected 401, got %d", w.Code)
		}
	})
	t.Run("SMTP disabled", func(t *testing.T) {
		w := performRequest(setupRouter(&mockScheduler{}, "secret", false), "secret", "key", `{"to":"a@example.com","subject":"S","text_body":"B"}`)
		if w.Code != http.StatusServiceUnavailable {
			t.Fatalf("expected 503, got %d", w.Code)
		}
	})
}

func TestEnqueueEmailValidation(t *testing.T) {
	tests := []struct {
		name string
		key  string
		body string
	}{
		{"missing idempotency key", "", `{"to":"a@example.com","subject":"S","text_body":"B"}`},
		{"invalid recipient", "key", `{"to":"not-an-email","subject":"S","text_body":"B"}`},
		{"header injection", "key", `{"to":"a@example.com","subject":"hello\r\nBcc: x@example.com","text_body":"B"}`},
		{"empty body", "key", `{"to":"a@example.com","subject":"S","text_body":""}`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := performRequest(setupRouter(&mockScheduler{}, "secret", true), "secret", tt.key, tt.body)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestEnqueueEmailRejectsOversizedBody(t *testing.T) {
	body, _ := json.Marshal(enqueueRequest{
		To:       "a@example.com",
		Subject:  "Subject",
		TextBody: string(bytes.Repeat([]byte("a"), maxBodyLength+1)),
	})
	w := performRequest(setupRouter(&mockScheduler{}, "secret", true), "secret", "key", string(body))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}
