def test_app_imports() -> None:
    from app.main import app
    assert app is not None


def test_settings_imports() -> None:
    from app.core.config import settings
    assert settings is not None
