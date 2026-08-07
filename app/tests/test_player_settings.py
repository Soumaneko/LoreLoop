import pytest

import threading
from app.runtime import settings as runtime_settings
from app.runtime.reader_preferences import ReaderPreferencesStore
from app.runtime.reader_preferences import load_reader_preferences
from app.runtime.reader_preferences import save_reader_preferences
from app.runtime.settings import director_enabled
from app.runtime.settings import load_settings
from app.runtime.settings import save_canon_validation
from app.runtime.settings import save_dice_display
from app.runtime.settings import save_initialization_settings


def test_dice_display_setting_is_per_save_and_normalized():
    assert save_dice_display({"dice_display": "full"}) == {
        "dice_display": "full"
    }
    assert load_settings()["dice_display"] == "full"
    assert save_dice_display({"dice_display": "invalid"}) == {
        "dice_display": "outcome"
    }


def test_pipeline_settings_have_product_defaults_and_are_persisted_per_save():
    assert save_canon_validation({"enabled": True}) == {
        "canon_validation_enabled": True,
    }
    assert load_settings()["canon_validation_enabled"] is True

    save_initialization_settings(director_is_enabled=False)

    assert director_enabled() is False
    assert load_settings()["director_enabled"] is False


def test_initialization_and_public_setting_updates_do_not_lose_each_other(
    monkeypatch,
):
    original_write = runtime_settings._write_settings
    initialization_write_started = threading.Event()
    allow_initialization_write = threading.Event()
    public_call_started = threading.Event()
    errors = []

    def blocking_write(settings):
        if (
            settings.get("director_enabled") is False
            and not initialization_write_started.is_set()
        ):
            initialization_write_started.set()
            if not allow_initialization_write.wait(timeout=2):
                raise TimeoutError("test did not release initialization write")
        original_write(settings)

    monkeypatch.setattr(runtime_settings, "_write_settings", blocking_write)

    def run_initialization_write():
        try:
            save_initialization_settings(director_is_enabled=False)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def run_public_write():
        try:
            public_call_started.set()
            save_canon_validation({"enabled": True})
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    initialization_thread = threading.Thread(target=run_initialization_write)
    initialization_thread.start()
    assert initialization_write_started.wait(timeout=2)

    public_thread = threading.Thread(target=run_public_write)
    public_thread.start()
    assert public_call_started.wait(timeout=2)
    public_thread.join(timeout=0.05)
    assert public_thread.is_alive()
    allow_initialization_write.set()
    initialization_thread.join(timeout=2)
    public_thread.join(timeout=2)

    assert not initialization_thread.is_alive()
    assert not public_thread.is_alive()
    assert errors == []
    assert load_settings()["director_enabled"] is False
    assert load_settings()["canon_validation_enabled"] is True


def test_reader_preferences_are_application_global_and_normalized(tmp_path):
    store = ReaderPreferencesStore(tmp_path / ".app" / "reader_preferences.json")
    assert save_reader_preferences({
        "font_size": "18",
        "font_family": "kaiti",
        "theme": "sepia",
        "show_token_usage": True,
    }, store=store) == {
        "font_size": "18",
        "font_family": "kaiti",
        "theme": "sepia",
        "show_token_usage": True,
    }
    assert load_reader_preferences(store=store) == {
        "font_size": "18",
        "font_family": "kaiti",
        "theme": "sepia",
        "show_token_usage": True,
    }
    assert not {
        "font_size", "font_family", "theme", "show_token_usage",
    } & load_settings().keys()
    assert save_reader_preferences({
        "font_size": "invalid",
        "font_family": "bad",
        "theme": "none",
    }, store=store) == {
        "font_size": "16.5",
        "font_family": "serif",
        "theme": "dark",
        "show_token_usage": False,
    }
