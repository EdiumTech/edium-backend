package domain

import (
	"time"

	"github.com/google/uuid"
)

type TaskStatus string

var (
	TaskStatusPending    TaskStatus = "pending"
	TaskStatusProcessing TaskStatus = "processing"
	TaskStatusDone       TaskStatus = "done"
	TaskStatusFailed     TaskStatus = "failed"
)

type TaskType string

var (
	OTPRequest       TaskType = "otp_request"
	OTPSent          TaskType = "otp_sent"
	PushNotification TaskType = "push_notification"
	EmailDelivery    TaskType = "email_delivery"
)

type Task struct {
	ID       uuid.UUID
	Type     TaskType
	Status   TaskStatus
	Payload  []byte
	TraceCtx string

	Attempts    int64
	MaxAttempts *int64
	AvailableAt time.Time
	LastError   *error
}
