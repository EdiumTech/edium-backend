package email

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/mail"
	"regexp"
	"strings"

	"herald/internal/domain"

	"github.com/gin-gonic/gin"
)

const (
	maxSubjectLength = 200
	maxBodyLength    = 100_000
	maxRequestBytes  = 210_000
)

var idempotencyKeyPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)

type TaskScheduler interface {
	ScheduleUnique(ctx context.Context, taskType domain.TaskType, payload []byte, idempotencyKey string) (bool, error)
}

type Handler struct {
	tasks   TaskScheduler
	apiKey  string
	enabled bool
}

func NewHandler(tasks TaskScheduler, apiKey string, enabled bool) *Handler {
	return &Handler{tasks: tasks, apiKey: apiKey, enabled: enabled}
}

func (h *Handler) Register(rg *gin.RouterGroup) {
	rg.POST("/emails", h.authMiddleware(), h.enqueue)
}

func (h *Handler) authMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		token := strings.TrimPrefix(c.GetHeader("Authorization"), "Bearer ")
		expected := sha256.Sum256([]byte(h.apiKey))
		actual := sha256.Sum256([]byte(token))
		if token == "" || subtle.ConstantTimeCompare(actual[:], expected[:]) != 1 {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "unauthorized"})
			return
		}
		c.Next()
	}
}

type enqueueRequest struct {
	To       string `json:"to"`
	Subject  string `json:"subject"`
	TextBody string `json:"text_body"`
	HTMLBody string `json:"html_body"`
}

func (h *Handler) enqueue(c *gin.Context) {
	if !h.enabled {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "email_not_configured"})
		return
	}
	idempotencyKey := c.GetHeader("Idempotency-Key")
	if !idempotencyKeyPattern.MatchString(idempotencyKey) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid_idempotency_key"})
		return
	}

	var req enqueueRequest
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxRequestBytes)
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "bad_request"})
		return
	}
	message, validationError := validateRequest(req)
	if validationError != "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": validationError})
		return
	}
	payload, err := json.Marshal(message)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "internal_error"})
		return
	}

	created, err := h.tasks.ScheduleUnique(c.Request.Context(), domain.EmailDelivery, payload, idempotencyKey)
	if err != nil {
		// Do not log the request: it can contain contact data and message content.
		slog.ErrorContext(c.Request.Context(), "email-handler: enqueue", "err", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "internal_error"})
		return
	}
	status := "queued"
	if !created {
		status = "duplicate"
	}
	c.JSON(http.StatusAccepted, gin.H{"status": status})
}

func validateRequest(req enqueueRequest) (domain.EmailMessage, string) {
	to, err := mail.ParseAddress(strings.TrimSpace(req.To))
	if err != nil || to.Address == "" {
		return domain.EmailMessage{}, "invalid_recipient"
	}
	subject := strings.TrimSpace(req.Subject)
	if subject == "" || len([]rune(subject)) > maxSubjectLength || strings.ContainsAny(subject, "\r\n") {
		return domain.EmailMessage{}, "invalid_subject"
	}
	textBody := strings.TrimSpace(req.TextBody)
	if textBody == "" || len([]byte(textBody)) > maxBodyLength {
		return domain.EmailMessage{}, "invalid_text_body"
	}
	if len([]byte(req.HTMLBody)) > maxBodyLength {
		return domain.EmailMessage{}, "invalid_html_body"
	}
	return domain.EmailMessage{
		To:       to.Address,
		Subject:  subject,
		TextBody: textBody,
		HTMLBody: req.HTMLBody,
	}, ""
}
