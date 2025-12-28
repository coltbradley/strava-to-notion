#!/usr/bin/env python3
"""
Send Weekly Status Report via Email

Sends the generated weekly_status.md report via SMTP.
"""

import os
import sys
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path


def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    from_email: str,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str = None,
):
    """Send email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    
    # Add plain text part
    part1 = MIMEText(body_text, "plain")
    msg.attach(part1)
    
    # Add HTML part if provided
    if body_html:
        part2 = MIMEText(body_html, "html")
        msg.attach(part2)
    
    # Send
    try:
        print(f"Connecting to SMTP server {smtp_host}:{smtp_port}...", file=sys.stderr)
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.set_debuglevel(0)  # Set to 1 for verbose debugging
        
        print("Starting TLS...", file=sys.stderr)
        server.starttls()  # starttls() enables certificate verification by default in Python
        
        print(f"Logging in as {smtp_username}...", file=sys.stderr)
        server.login(smtp_username, smtp_password)
        
        print(f"Sending email from {from_email} to {to_email}...", file=sys.stderr)
        server.send_message(msg)
        
        print("Email sent successfully, closing connection...", file=sys.stderr)
        server.quit()
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"SMTP Authentication Error: {e}", file=sys.stderr)
        print("Check your SMTP_USERNAME and SMTP_PASSWORD credentials.", file=sys.stderr)
        return False
    except smtplib.SMTPConnectError as e:
        print(f"SMTP Connection Error: Could not connect to {smtp_host}:{smtp_port}", file=sys.stderr)
        print(f"Details: {e}", file=sys.stderr)
        return False
    except smtplib.SMTPException as e:
        print(f"SMTP Error: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Unexpected error sending email: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


def markdown_to_html(text: str) -> str:
    """Convert markdown to simple HTML (basic conversion)."""
    lines = text.split("\n")
    result = []
    in_list = False
    in_paragraph = False
    i = 0
    
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # Headers (check before other processing)
        if stripped.startswith("### "):
            if in_list:
                result.append("</ul>")
                in_list = False
            if in_paragraph:
                result.append("</p>")
                in_paragraph = False
            content = stripped[4:]
            result.append(f"<h3>{content}</h3>")
            i += 1
            continue
        elif stripped.startswith("## "):
            if in_list:
                result.append("</ul>")
                in_list = False
            if in_paragraph:
                result.append("</p>")
                in_paragraph = False
            content = stripped[3:]
            result.append(f"<h2>{content}</h2>")
            i += 1
            continue
        elif stripped.startswith("# "):
            if in_list:
                result.append("</ul>")
                in_list = False
            if in_paragraph:
                result.append("</p>")
                in_paragraph = False
            content = stripped[2:]
            result.append(f"<h1>{content}</h1>")
            i += 1
            continue
        
        # Horizontal rules
        if stripped == "---":
            if in_list:
                result.append("</ul>")
                in_list = False
            if in_paragraph:
                result.append("</p>")
                in_paragraph = False
            result.append("<hr>")
            i += 1
            continue
        
        # Lists
        if stripped.startswith("* "):
            if in_paragraph:
                result.append("</p>")
                in_paragraph = False
            if not in_list:
                result.append("<ul>")
                in_list = True
            content = stripped[2:]
            # Process inline formatting (bold, code)
            content = _process_inline_formatting(content)
            result.append(f"  <li>{content}</li>")
            i += 1
            continue
        else:
            if in_list:
                result.append("</ul>")
                in_list = False
        
        # Empty line - end paragraph
        if not stripped:
            if in_paragraph:
                result.append("</p>")
                in_paragraph = False
            i += 1
            continue
        
        # Regular paragraph content
        if not in_paragraph:
            result.append("<p>")
            in_paragraph = True
        
        # Process inline formatting (bold, code) for paragraph content
        formatted_line = _process_inline_formatting(line)
        result.append(formatted_line)
        i += 1
    
    # Close any open tags
    if in_list:
        result.append("</ul>")
    if in_paragraph:
        result.append("</p>")
    
    return "\n".join(result)


def _process_inline_formatting(text: str) -> str:
    """Process inline markdown formatting (bold, code, etc.)"""
    html = text
    
    # Convert bold (**text** or __text__)
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'__(.+?)__', r'<strong>\1</strong>', html)
    
    # Convert inline code (`code`)
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    
    return html


def main():
    """Main entry point."""
    # Get configuration from environment
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("STATUS_EMAIL_FROM")
    to_email = os.getenv("STATUS_EMAIL_TO")
    subject_prefix = os.getenv("STATUS_EMAIL_SUBJECT_PREFIX", "[Strava→Notion]")
    
    # Validate required env vars
    missing = []
    if not smtp_host:
        missing.append("SMTP_HOST")
    if not smtp_username:
        missing.append("SMTP_USERNAME")
    if not smtp_password:
        missing.append("SMTP_PASSWORD")
    if not from_email:
        missing.append("STATUS_EMAIL_FROM")
    if not to_email:
        missing.append("STATUS_EMAIL_TO")
    
    if missing:
        print(f"Error: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    
    # Load report (scripts/ is in repo root, report is also in repo root)
    repo_root = Path(__file__).parent.parent
    report_file = repo_root / "weekly_status.md"
    
    if not report_file.exists():
        print(f"Error: Report file not found: {report_file}", file=sys.stderr)
        sys.exit(1)
    
    with open(report_file, "r") as f:
        report_text = f.read()
    
    # Convert to HTML
    report_html = markdown_to_html(report_text)
    
    # Subject
    subject = f"{subject_prefix} Weekly Sync Status Report"
    
    # Send
    success = send_email(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        from_email=from_email,
        to_email=to_email,
        subject=subject,
        body_text=report_text,
        body_html=report_html,
    )
    
    if success:
        print(f"Email sent successfully to {to_email}")
        sys.exit(0)
    else:
        print("Failed to send email", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()


