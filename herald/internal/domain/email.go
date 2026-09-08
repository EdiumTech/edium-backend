package domain

// EmailMessage is the payload stored in the Herald outbox. From is deliberately
// absent: callers cannot override the verified sender configured on the server.
type EmailMessage struct {
	To       string `json:"to"`
	Subject  string `json:"subject"`
	TextBody string `json:"text_body"`
	HTMLBody string `json:"html_body,omitempty"`
}
