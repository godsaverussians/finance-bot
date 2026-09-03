from . import entry, onboarding, recurring

# Порядок важен: свои состояния перехватываются раньше общего быстрого ввода.
routers = (onboarding.router, recurring.router, entry.router)

__all__ = ["routers"]
