import os

# Opt in to python-telegram-bot's timedelta time periods (the default in its next
# major version) so RetryAfter.retry_after is a timedelta and emits no deprecation
# warning. The dispatcher is the only reader and handles both forms.
os.environ.setdefault("PTB_TIMEDELTA", "true")
