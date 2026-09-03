from . import entry, onboarding, recurring, report

# Порядок важен: свои состояния перехватываются раньше общего быстрого ввода.
routers = (onboarding.router, recurring.router, report.router, entry.router)

__all__ = ["routers"]
