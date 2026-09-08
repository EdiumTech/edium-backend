package email

import (
	"bytes"
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"mime/quotedprintable"
	"net"
	"net/mail"
	"net/smtp"
	"net/textproto"
	"strings"
	"time"

	"herald/internal/config"
	"herald/internal/domain"
)

const smtpTimeout = 15 * time.Second

type Sender struct {
	cfg  config.EmailConfig
	from *mail.Address
}

func NewSender(cfg config.EmailConfig) (*Sender, error) {
	from, err := mail.ParseAddress(cfg.From)
	if err != nil {
		return nil, fmt.Errorf("invalid SMTP_FROM: %w", err)
	}
	if cfg.Port < 1 || cfg.Port > 65535 {
		return nil, fmt.Errorf("invalid SMTP_PORT")
	}
	switch cfg.TLSMode {
	case "starttls", "implicit", "none":
	default:
		return nil, fmt.Errorf("SMTP_TLS_MODE must be starttls, implicit or none")
	}
	return &Sender{cfg: cfg, from: from}, nil
}

func (s *Sender) Send(ctx context.Context, message domain.EmailMessage) error {
	to, err := mail.ParseAddress(message.To)
	if err != nil {
		return fmt.Errorf("invalid recipient: %w", err)
	}
	body, err := buildMessage(s.from, to, message)
	if err != nil {
		return err
	}

	address := net.JoinHostPort(s.cfg.Host, fmt.Sprintf("%d", s.cfg.Port))
	dialer := &net.Dialer{Timeout: smtpTimeout}
	var conn net.Conn
	if s.cfg.TLSMode == "implicit" {
		conn, err = tls.DialWithDialer(dialer, "tcp", address, s.tlsConfig())
	} else {
		conn, err = dialer.DialContext(ctx, "tcp", address)
	}
	if err != nil {
		return fmt.Errorf("connect SMTP: %w", err)
	}
	defer func() { _ = conn.Close() }()
	deadline := time.Now().Add(smtpTimeout)
	if contextDeadline, ok := ctx.Deadline(); ok && contextDeadline.Before(deadline) {
		deadline = contextDeadline
	}
	if err := conn.SetDeadline(deadline); err != nil {
		return fmt.Errorf("set SMTP deadline: %w", err)
	}

	client, err := smtp.NewClient(conn, s.cfg.Host)
	if err != nil {
		return fmt.Errorf("create SMTP client: %w", err)
	}
	defer func() { _ = client.Close() }()

	if s.cfg.TLSMode == "starttls" {
		if ok, _ := client.Extension("STARTTLS"); !ok {
			return fmt.Errorf("SMTP server does not support STARTTLS")
		}
		if err := client.StartTLS(s.tlsConfig()); err != nil {
			return fmt.Errorf("start SMTP TLS: %w", err)
		}
	}
	if s.cfg.Username != "" {
		auth := smtp.PlainAuth("", s.cfg.Username, s.cfg.Password, s.cfg.Host)
		if err := client.Auth(auth); err != nil {
			return fmt.Errorf("authenticate SMTP: %w", err)
		}
	}
	if err := client.Mail(s.from.Address); err != nil {
		return fmt.Errorf("SMTP MAIL FROM: %w", err)
	}
	if err := client.Rcpt(to.Address); err != nil {
		return fmt.Errorf("SMTP RCPT TO: %w", err)
	}
	w, err := client.Data()
	if err != nil {
		return fmt.Errorf("SMTP DATA: %w", err)
	}
	if _, err := w.Write(body); err != nil {
		_ = w.Close()
		return fmt.Errorf("write SMTP message: %w", err)
	}
	if err := w.Close(); err != nil {
		return fmt.Errorf("finish SMTP message: %w", err)
	}
	if err := client.Quit(); err != nil {
		return fmt.Errorf("quit SMTP: %w", err)
	}
	return nil
}

func (s *Sender) tlsConfig() *tls.Config {
	return &tls.Config{ServerName: s.cfg.Host, MinVersion: tls.VersionTLS12}
}

func buildMessage(from, to *mail.Address, message domain.EmailMessage) ([]byte, error) {
	if strings.ContainsAny(message.Subject, "\r\n") {
		return nil, fmt.Errorf("subject contains a newline")
	}
	var out bytes.Buffer
	fmt.Fprintf(&out, "From: %s\r\n", from.String())
	fmt.Fprintf(&out, "To: %s\r\n", to.String())
	fmt.Fprintf(&out, "Subject: %s\r\n", mime.QEncoding.Encode("utf-8", message.Subject))
	fmt.Fprint(&out, "MIME-Version: 1.0\r\n")

	if message.HTMLBody == "" {
		fmt.Fprint(&out, "Content-Type: text/plain; charset=utf-8\r\n")
		fmt.Fprint(&out, "Content-Transfer-Encoding: quoted-printable\r\n\r\n")
		if err := writeQuotedPrintable(&out, message.TextBody); err != nil {
			return nil, err
		}
		return out.Bytes(), nil
	}

	mw := multipart.NewWriter(&out)
	fmt.Fprintf(&out, "Content-Type: multipart/alternative; boundary=%q\r\n\r\n", mw.Boundary())
	if err := writePart(mw, "text/plain", message.TextBody); err != nil {
		return nil, err
	}
	if err := writePart(mw, "text/html", message.HTMLBody); err != nil {
		return nil, err
	}
	if err := mw.Close(); err != nil {
		return nil, fmt.Errorf("close MIME message: %w", err)
	}
	return out.Bytes(), nil
}

func writePart(mw *multipart.Writer, contentType, body string) error {
	header := make(textproto.MIMEHeader)
	header["Content-Type"] = []string{contentType + "; charset=utf-8"}
	header["Content-Transfer-Encoding"] = []string{"quoted-printable"}
	part, err := mw.CreatePart(header)
	if err != nil {
		return fmt.Errorf("create MIME part: %w", err)
	}
	return writeQuotedPrintable(part, body)
}

func writeQuotedPrintable(dst io.Writer, body string) error {
	w := quotedprintable.NewWriter(dst)
	if _, err := io.WriteString(w, body); err != nil {
		_ = w.Close()
		return fmt.Errorf("write MIME body: %w", err)
	}
	if err := w.Close(); err != nil {
		return fmt.Errorf("close MIME body: %w", err)
	}
	return nil
}
