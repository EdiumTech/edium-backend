package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	"herald/internal/domain"
	"herald/internal/infra/telemetry"

	"github.com/google/uuid"
	"go.opentelemetry.io/otel"
)

const (
	emailPollInterval = 2 * time.Second
	emailBatchSize    = 10
)

type emailTaskRepo interface {
	ClaimPending(ctx context.Context, taskType domain.TaskType, limit int) ([]domain.Task, error)
	MarkDone(ctx context.Context, id uuid.UUID) error
	MarkFailed(ctx context.Context, id uuid.UUID, reason string, retryAfter time.Duration) error
}

type EmailSender interface {
	Send(ctx context.Context, message domain.EmailMessage) error
}

type EmailProcessor struct {
	tasks  emailTaskRepo
	sender EmailSender
}

func NewEmailProcessor(tasks emailTaskRepo, sender EmailSender) *EmailProcessor {
	return &EmailProcessor{tasks: tasks, sender: sender}
}

func (w *EmailProcessor) Run(ctx context.Context) error {
	slog.Info("email-processor: запущен", "interval", emailPollInterval)
	ticker := time.NewTicker(emailPollInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if err := w.processBatch(ctx); err != nil {
				slog.Error("email-processor: ошибка батча", "err", err)
			}
		}
	}
}

func (w *EmailProcessor) processBatch(ctx context.Context) error {
	tasks, err := w.tasks.ClaimPending(ctx, domain.EmailDelivery, emailBatchSize)
	if err != nil {
		return fmt.Errorf("ClaimPending: %w", err)
	}
	for i := range tasks {
		t := tasks[i]
		if err := w.processTask(ctx, t); err != nil {
			slog.Error("email-processor: ошибка задачи", "task_id", t.ID, "err", err)
			if markErr := w.tasks.MarkFailed(context.WithoutCancel(ctx), t.ID, err.Error(), emailRetryAfter(t.Attempts)); markErr != nil {
				slog.Error("email-processor: не удалось сохранить ошибку задачи", "task_id", t.ID, "err", markErr)
			}
		}
	}
	return nil
}

func (w *EmailProcessor) processTask(ctx context.Context, task domain.Task) error {
	var message domain.EmailMessage
	if err := json.Unmarshal(task.Payload, &message); err != nil {
		return fmt.Errorf("decode payload: %w", err)
	}

	ctx, span := otel.Tracer("herald").Start(telemetry.Extract(ctx, task.TraceCtx), "worker.email_processor")
	defer span.End()

	// Deliberately do not log recipient, subject or body: email tasks can contain PII.
	slog.InfoContext(ctx, "email-processor: обработка", "task_id", task.ID)
	if err := w.sender.Send(ctx, message); err != nil {
		// SMTP responses can echo a recipient address. Keep contacts out of logs
		// and the task's last_error field.
		return fmt.Errorf("email delivery failed")
	}
	return w.tasks.MarkDone(ctx, task.ID)
}

func emailRetryAfter(attempt int64) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	delay := 30 * time.Second * time.Duration(1<<min(attempt-1, 4))
	return delay
}
