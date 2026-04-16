from __future__ import annotations

import os
from unittest.mock import patch

from aisisstant.config import Config


class TestConfigDefaults:
    def test_default_db_host(self):
        c = Config()
        assert c.db_host == "127.0.0.1"

    def test_default_db_port(self):
        c = Config()
        assert c.db_port == 5438

    def test_default_db_name(self):
        c = Config()
        assert c.db_name == "aisisstant"

    def test_default_db_user(self):
        c = Config()
        assert c.db_user == "aisisstant"

    def test_default_db_password(self):
        c = Config()
        assert c.db_password == "aisisstant_dev"

    def test_default_input_bucket_seconds(self):
        c = Config()
        assert c.input_bucket_seconds == 5

    def test_default_window_poll_seconds(self):
        c = Config()
        assert c.window_poll_seconds == 2.0

    def test_default_mic_poll_seconds(self):
        c = Config()
        assert c.mic_poll_seconds == 10.0

    def test_default_score_window_seconds(self):
        c = Config()
        assert c.score_window_seconds == 30


class TestConfigFromEnv:
    def test_custom_db_host(self):
        with patch.dict(os.environ, {"DB_HOST": "10.0.0.1"}):
            c = Config()
            assert c.db_host == "10.0.0.1"

    def test_custom_db_port(self):
        with patch.dict(os.environ, {"DB_PORT": "9999"}):
            c = Config()
            assert c.db_port == 9999

    def test_custom_db_name(self):
        with patch.dict(os.environ, {"DB_NAME": "mydb"}):
            c = Config()
            assert c.db_name == "mydb"

    def test_custom_db_user(self):
        with patch.dict(os.environ, {"DB_USER": "admin"}):
            c = Config()
            assert c.db_user == "admin"

    def test_custom_db_password(self):
        with patch.dict(os.environ, {"DB_PASSWORD": "secret123"}):
            c = Config()
            assert c.db_password == "secret123"

    def test_custom_input_bucket(self):
        with patch.dict(os.environ, {"INPUT_BUCKET_SEC": "10"}):
            c = Config()
            assert c.input_bucket_seconds == 10

    def test_custom_window_poll(self):
        with patch.dict(os.environ, {"WINDOW_POLL_SEC": "0.5"}):
            c = Config()
            assert c.window_poll_seconds == 0.5

    def test_custom_mic_poll(self):
        with patch.dict(os.environ, {"MIC_POLL_SEC": "30"}):
            c = Config()
            assert c.mic_poll_seconds == 30.0

    def test_custom_score_window(self):
        with patch.dict(os.environ, {"SCORE_WINDOW_SEC": "60"}):
            c = Config()
            assert c.score_window_seconds == 60


class TestConfigDatabaseUrl:
    def test_default_url(self):
        c = Config()
        assert c.database_url == "postgresql://aisisstant:aisisstant_dev@127.0.0.1:5438/aisisstant"

    def test_custom_url(self):
        with patch.dict(os.environ, {
            "DB_USER": "u",
            "DB_PASSWORD": "p",
            "DB_HOST": "db.local",
            "DB_PORT": "1234",
            "DB_NAME": "mydb",
        }):
            c = Config()
            assert c.database_url == "postgresql://u:p@db.local:1234/mydb"
