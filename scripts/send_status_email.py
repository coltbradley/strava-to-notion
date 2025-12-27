#!/usr/bin/env python3
"""
Send Weekly Status Report via Email

Sends the generated weekly_status.md report via SMTP.
"""

import os
import sys
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
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}", file=sys.stderr)
        return False


def markdown_to_html(text: str) -> str:
    """Convert markdown to simple HTML (basic conversion)."""
    html = text
    
    # Headers
    html = html.replace("# ", "<h1>").replace("\n# ", "</h1>\n<h1>")
    html = html.replace("## ", "<h2>").replace("\n## ", "</h2>\n<h2>")
    html = html.replace("### ", "<h3>").replace("\n### ", "</h3>\n<h3>")
    
    # Bold
    html = html.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
    
    # Code
    html = html.replace("`", "<code>").replace("`", "</code>")
    
    # Lists (simple - assumes * format)
    lines = html.split("\n")
    in_list = False
    result = []
    for line in lines:
        if line.strip().startswith("* "):
            if not in_list:
                result.append("<ul>")
                in_list = True
            content = line.strip()[2:]
            result.append(f"  <li>{content}</li>")
        else:
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(line)
    if in_list:
        result.append("</ul>")
    html = "\n".join(result)
    
    # Paragraphs
    html = html.replace("\n\n", "</p><p>")
    html = f"<p>{html}</p>"
    
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


