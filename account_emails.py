"""Account emails: welcome, approval outcome and password reset."""
import config
import notifications

PROJECT_NAME = getattr(config, "DASHBOARD_TITLE", "AGC Trader")

_RISK_FOOTER = (
    "\n\nCapital at risk: trading involves risk and you may lose some or all of the money "
    "you contribute. Returns are not guaranteed."
)


def send_welcome(name: str, email: str) -> bool:
    body = (
        f"Hello {name},\n\n"
        "Welcome! Your interest in creating an account and becoming part of this project/venture "
        "has been noted. In due time you will receive a response on whether your registration "
        "was successful or not."
        f"{_RISK_FOOTER}\n\n{PROJECT_NAME}"
    )
    return notifications.send_user_email(email, f"Welcome to {PROJECT_NAME}", body)


def send_approved(name: str, email: str) -> bool:
    body = (
        f"Hello {name},\n\n"
        "Good news: your registration has been approved. You can now log in with your email "
        f"address and password.{_RISK_FOOTER}\n\n{PROJECT_NAME}"
    )
    return notifications.send_user_email(email, f"Your {PROJECT_NAME} registration was approved", body)


def send_declined(name: str, email: str) -> bool:
    body = (
        f"Hello {name},\n\n"
        "Thank you for your interest. Unfortunately we are unable to approve your registration "
        f"at this time.\n\n{PROJECT_NAME}"
    )
    return notifications.send_user_email(email, f"Your {PROJECT_NAME} registration", body)


def send_password_reset(name: str, email: str, link: str, minutes: int) -> bool:
    body = (
        f"Hello {name},\n\n"
        f"We received a request to reset your password. Use this link within {minutes} minutes:\n\n"
        f"{link}\n\n"
        "If you did not request this, you can ignore this email; your password will not change."
        f"\n\n{PROJECT_NAME}"
    )
    return notifications.send_user_email(email, f"Reset your {PROJECT_NAME} password", body)
