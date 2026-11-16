"""Jev 分类等待预算与配置边界。"""
import time

import pytest

from backend import config, jev_client


@pytest.fixture
def approved(monkeypatch):
    monkeypatch.setattr(config, "JEV_ENABLED", True)
    monkeypatch.setattr(config, "JEV_API_KEY", "placeholder")
    monkeypatch.setattr(config, "JEV_APPROVED_QUESTIONS", frozenset({"q"}))
    yield


def _set_response(monkeypatch, delay: float, route: str = "disease"):
    def _request(question: str) -> str:
        time.sleep(delay)
        return route
    monkeypatch.setattr(jev_client, "_request_choice", _request)


def test_slow_response_within_budget_succeeds(approved, monkeypatch):
    monkeypatch.setattr(jev_client, "_TIMEOUT_SECONDS", 2.0)
    _set_response(monkeypatch, 0.2)
    assert jev_client.classify("q") == "disease"


def test_response_over_budget_returns_none_without_blocking(approved, monkeypatch):
    monkeypatch.setattr(jev_client, "_TIMEOUT_SECONDS", 0.1)
    _set_response(monkeypatch, 1.0)
    started = time.perf_counter()
    assert jev_client.classify("q") is None
    assert time.perf_counter() - started < 0.5


def test_non_approved_question_never_calls_api(approved, monkeypatch):
    called = []
    monkeypatch.setattr(jev_client, "_request_choice",
                        lambda question: called.append(question) or "disease")
    assert jev_client.classify("q，我的病历号是示例") is None
    assert called == []


def test_timeout_config_is_bounded(monkeypatch):
    monkeypatch.setenv("MEDGUARD_JEV_TIMEOUT_SEC", "999")
    assert config._bounded_float("MEDGUARD_JEV_TIMEOUT_SEC", 2.5, 0.1, 10.0) == 10.0
    monkeypatch.setenv("MEDGUARD_JEV_TIMEOUT_SEC", "0")
    assert config._bounded_float("MEDGUARD_JEV_TIMEOUT_SEC", 2.5, 0.1, 10.0) == 0.1
    monkeypatch.setenv("MEDGUARD_JEV_TIMEOUT_SEC", "not-a-number")
    assert config._bounded_float("MEDGUARD_JEV_TIMEOUT_SEC", 2.5, 0.1, 10.0) == 2.5
