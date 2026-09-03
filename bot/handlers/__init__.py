from . import entry, onboarding

# Порядок важен: онбординг перехватывает свои состояния раньше общего ввода.
routers = (onboarding.router, entry.router)

__all__ = ["routers"]
