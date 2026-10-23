import pytest

import threading
from app.runtime import settings as runtime_settings
from app.runtime.reader_preferences import ReaderPreferencesStore
from app.runtime.reader_preferences import load_reader_preferences
from app.runtime.reader_preferences import save_reader_preferences
from app.runtime.settings import experience_mode
from app.runtime.settings import load_settings
from app.runtime.settings import save_canon_validation
from app.runtime.settings import save_dice_display
from app.runtime.settings import save_experience_mode


def test_dice_display_setting_is_per_save_and_normalized(campaign_runtime):
    assert save_dice_display({"dice_display": "full"}) == {
        "dice_display": "full"
    }
    assert load_settings()["dice_display"] == "full"
    assert save_dice_display({"dice_display": "invalid"}) == {
        "dice_display": "outcome"
    }


def test_pipeline_settings_have_product_defaults_and_are_persisted_per_save(campaign_runtime):
    assert save_canon_validation({"enabled": True}) == {
        "canon_validation_enabled": True,
    }
    assert load_settings()["canon_validation_enabled"] is True


def test_experience_mode_is_per_save_and_defaults_to_player(campaign_runtime):
    assert experience_mode() == "player"
    assert save_experience_mode({"experience_mode": "creator"}) == {
        "experience_mode": "creator",
    }
    assert experience_mode() == "creator"
    assert load_settings()["experience_mode"] == "creator"
    assert save_experience_mode({"experience_mode": "invalid"}) == {
        "experience_mode": "player",
    }
    assert experience_mode() == "player"


def test_public_setting_updates_do_not_lose_each_other(campaign_runtime, monkeypatch):
    original_write = runtime_settings._write_settings
    first_write_started = threading.Event()
    allow_first_write = threading.Event()
    second_call_started = threading.Event()
    errors = []

    def blocking_write(settings):
        if (
            settings.get("experience_mode") == "creator"
            and not first_write_started.is_set()
        ):
            first_write_started.set()
            if not allow_first_write.wait(timeout=2):
                raise TimeoutError("test did not release first write")
        original_write(settings)

    monkeypatch.setattr(runtime_settings, "_write_settings", blocking_write)

    def run_first_write():
        try:
            save_experience_mode({"experience_mode": "creator"})
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def run_second_write():
        try:
            second_call_started.set()
            save_canon_validation({"enabled": True})
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first_thread = threading.Thread(target=run_first_write)
    first_thread.start()
    assert first_write_started.wait(timeout=2)

    second_thread = threading.Thread(target=run_second_write)
    second_thread.start()
    assert second_call_started.wait(timeout=2)
    second_thread.join(timeout=0.05)
    assert second_thread.is_alive()
    allow_first_write.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert load_settings()["experience_mode"] == "creator"
    assert load_settings()["canon_validation_enabled"] is True


def test_reader_preferences_are_application_global_and_normalized(tmp_path):
    store = ReaderPreferencesStore(tmp_path / ".app" / "reader_preferences.json")
    assert save_reader_preferences({
        "font_size": "18",
        "font_family": "kaiti",
        "theme": "sepia",
        "show_token_usage": True,
        "one_click_confirm": True,
    }, store=store) == {
        "font_size": "18",
        "font_family": "kaiti",
        "theme": "sepia",
        "show_token_usage": True,
        "one_click_confirm": True,
    }
    assert load_reader_preferences(store=store) == {
        "font_size": "18",
        "font_family": "kaiti",
        "theme": "sepia",
        "show_token_usage": True,
        "one_click_confirm": True,
    }
    assert not {
        "font_size", "font_family", "theme", "show_token_usage",
        "one_click_confirm",
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
        "one_click_confirm": False,
    }


def test_one_click_confirm_defaults_to_off(tmp_path):
    store = ReaderPreferencesStore(tmp_path / ".app" / "reader_preferences.json")
    assert load_reader_preferences(store=store)["one_click_confirm"] is False
    assert ReaderPreferencesStore(store.path).load().one_click_confirm is False
