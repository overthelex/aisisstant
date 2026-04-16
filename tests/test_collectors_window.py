from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aisisstant.collectors.window import WindowCollector, _SPINNER_RE
from aisisstant.models import WindowInfo, WindowSession


class TestNormalizeTitle:
    # --- Spinner ---
    def test_strips_braille_spinner(self):
        assert WindowCollector._normalize_title("⠐ my-project") == "my-project"

    def test_strips_star_spinner(self):
        assert WindowCollector._normalize_title("✳ Claude Code") == "Claude Code"

    def test_strips_multiple_braille(self):
        assert WindowCollector._normalize_title("⠹⠸ building") == "building"

    def test_different_spinners_normalize_same(self):
        t1 = WindowCollector._normalize_title("⠐ activity-tracker")
        t2 = WindowCollector._normalize_title("⠂ activity-tracker")
        t3 = WindowCollector._normalize_title("✳ activity-tracker")
        assert t1 == t2 == t3 == "activity-tracker"

    # --- Notification counts ---
    def test_strips_parenthesized_count(self):
        assert WindowCollector._normalize_title("(3) Inbox - Gmail") == "Inbox - Gmail"

    def test_strips_bracketed_count(self):
        assert WindowCollector._normalize_title("[2] WhatsApp") == "WhatsApp"

    def test_different_counts_normalize_same(self):
        t1 = WindowCollector._normalize_title("(1) Inbox - Gmail")
        t2 = WindowCollector._normalize_title("(99) Inbox - Gmail")
        assert t1 == t2

    # --- Browser suffixes ---
    def test_strips_chrome_suffix(self):
        assert WindowCollector._normalize_title(
            "GitHub - Google Chrome - Vladimir"
        ) == "GitHub"

    def test_strips_chrome_suffix_no_profile(self):
        assert WindowCollector._normalize_title(
            "GitHub - Google Chrome"
        ) == "GitHub"

    def test_strips_firefox_suffix(self):
        assert WindowCollector._normalize_title(
            "Stack Overflow — Mozilla Firefox"
        ) == "Stack Overflow"

    def test_strips_firefox_short_suffix(self):
        assert WindowCollector._normalize_title(
            "Docs - Firefox"
        ) == "Docs"

    def test_strips_edge_suffix(self):
        assert WindowCollector._normalize_title(
            "Outlook - Microsoft Edge"
        ) == "Outlook"

    def test_strips_brave_suffix(self):
        assert WindowCollector._normalize_title(
            "Reddit - Brave"
        ) == "Reddit"

    # --- Media state ---
    def test_strips_audio_playing(self):
        assert WindowCollector._normalize_title(
            "Song | YouTube Music - Audio playing - Google Chrome - user"
        ) == "Song | YouTube Music"

    def test_strips_paused(self):
        assert WindowCollector._normalize_title(
            "YouTube Music - Paused - Google Chrome - user"
        ) == "YouTube Music"

    # --- Combined ---
    def test_count_plus_chrome_suffix(self):
        assert WindowCollector._normalize_title(
            "(17) Facebook - Google Chrome - Profile 2"
        ) == "Facebook"

    # --- Preserved ---
    def test_preserves_vlc_title(self):
        title = "Movie.mkv - VLC media player"
        assert WindowCollector._normalize_title(title) == title

    def test_preserves_transmission(self):
        assert WindowCollector._normalize_title("Transmission") == "Transmission"

    def test_preserves_empty_string(self):
        assert WindowCollector._normalize_title("") == ""

    def test_preserves_slack_content(self):
        assert WindowCollector._normalize_title(
            "Slack | general | Workspace - Google Chrome - Work"
        ) == "Slack | general | Workspace"


class TestWindowCollectorTryDbus:
    @pytest.mark.asyncio
    async def test_parses_valid_dbus_output(self, mock_writer):
        wc = WindowCollector(mock_writer)
        json_data = json.dumps({"wm_class": "firefox", "title": "GitHub", "pid": 1234})
        gdbus_output = f"('{json_data}',)\n"

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (gdbus_output.encode(), b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.window.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await wc._try_dbus()

        assert result is not None
        assert result.wm_class == "firefox"
        assert result.title == "GitHub"
        assert result.pid == 1234

    @pytest.mark.asyncio
    async def test_returns_none_on_nonzero_exit(self, mock_writer):
        wc = WindowCollector(mock_writer)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1

        with patch("aisisstant.collectors.window.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await wc._try_dbus()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_malformed_output(self, mock_writer):
        wc = WindowCollector(mock_writer)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"garbage", b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.window.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await wc._try_dbus()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, mock_writer):
        wc = WindowCollector(mock_writer)

        with patch(
            "aisisstant.collectors.window.asyncio.create_subprocess_exec",
            side_effect=asyncio.TimeoutError,
        ):
            result = await wc._try_dbus()

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_empty_json(self, mock_writer):
        wc = WindowCollector(mock_writer)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"('{}',)\n", b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.window.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await wc._try_dbus()

        assert result is not None
        assert result.wm_class == ""
        assert result.title == ""
        assert result.pid == 0

    @pytest.mark.asyncio
    async def test_handles_title_with_special_chars(self, mock_writer):
        wc = WindowCollector(mock_writer)
        # Title with special characters but valid JSON inside gdbus quotes
        json_str = '{"wm_class": "vim", "title": "file - [edited]", "pid": 1}'
        gdbus_output = f"('{json_str}',)\n"

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (gdbus_output.encode(), b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.window.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await wc._try_dbus()

        assert result is not None
        assert result.wm_class == "vim"
        assert result.title == "file - [edited]"


class TestWindowCollectorTryXdotool:
    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self, mock_writer):
        wc = WindowCollector(mock_writer)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1

        with patch("aisisstant.collectors.window.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await wc._try_xdotool()

        assert result.wm_class == ""
        assert result.title == ""
        assert result.pid == 0

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self, mock_writer):
        wc = WindowCollector(mock_writer)

        with patch(
            "aisisstant.collectors.window.asyncio.create_subprocess_exec",
            side_effect=asyncio.TimeoutError,
        ):
            result = await wc._try_xdotool()

        assert result.wm_class == ""


class TestWindowCollectorGetActiveWindow:
    @pytest.mark.asyncio
    async def test_prefers_atspi_over_dbus(self, mock_writer):
        wc = WindowCollector(mock_writer)
        wc._atspi_inited = True
        atspi_info = WindowInfo(wm_class="from_atspi", title="AT-SPI", pid=1)

        with patch("aisisstant.collectors.window._get_active_window_atspi", return_value=atspi_info), \
             patch.object(wc, "_try_dbus") as mock_dbus:
            result = await wc._get_active_window()

        assert result.wm_class == "from_atspi"
        mock_dbus.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_dbus_when_atspi_fails(self, mock_writer):
        wc = WindowCollector(mock_writer)
        wc._atspi_inited = True
        dbus_info = WindowInfo(wm_class="from_dbus", title="DBus", pid=2)

        with patch("aisisstant.collectors.window._get_active_window_atspi", return_value=None), \
             patch.object(wc, "_try_dbus", return_value=dbus_info) as mock_dbus, \
             patch.object(wc, "_try_xdotool") as mock_xdotool:
            result = await wc._get_active_window()

        assert result.wm_class == "from_dbus"
        mock_xdotool.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_switchamba(self, mock_writer):
        wc = WindowCollector(mock_writer)
        wc._atspi_inited = True
        switchamba_info = WindowInfo(wm_class="from_switchamba")

        with patch("aisisstant.collectors.window._get_active_window_atspi", return_value=None), \
             patch.object(wc, "_try_dbus", return_value=None), \
             patch.object(wc, "_try_switchamba", return_value=switchamba_info), \
             patch.object(wc, "_try_xdotool") as mock_xdotool:
            result = await wc._get_active_window()

        assert result.wm_class == "from_switchamba"
        mock_xdotool.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_xdotool(self, mock_writer):
        wc = WindowCollector(mock_writer)
        wc._atspi_inited = True
        xdotool_info = WindowInfo(wm_class="from_xdotool", title="X", pid=3)

        with patch("aisisstant.collectors.window._get_active_window_atspi", return_value=None), \
             patch.object(wc, "_try_dbus", return_value=None), \
             patch.object(wc, "_try_switchamba", return_value=None), \
             patch.object(wc, "_try_xdotool", return_value=xdotool_info):
            result = await wc._get_active_window()

        assert result.wm_class == "from_xdotool"


class TestWindowCollectorHandleWindow:
    @pytest.mark.asyncio
    async def test_opens_new_session(self, mock_writer):
        wc = WindowCollector(mock_writer)
        info = WindowInfo(wm_class="firefox", title="Home", pid=100)

        await wc._handle_window(info)

        mock_writer.put.assert_called_once()
        table, session = mock_writer.put.call_args[0]
        assert table == "window_sessions"
        assert session.wm_class == "firefox"
        assert session.window_title == "Home"
        assert session.pid == 100

    @pytest.mark.asyncio
    async def test_ignores_empty_wm_class(self, mock_writer):
        wc = WindowCollector(mock_writer)
        info = WindowInfo(wm_class="", title="", pid=0)

        await wc._handle_window(info)

        mock_writer.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_change_same_window(self, mock_writer):
        wc = WindowCollector(mock_writer)
        info = WindowInfo(wm_class="firefox", title="Home", pid=100)

        await wc._handle_window(info)
        mock_writer.put.reset_mock()

        # Same window again
        await wc._handle_window(info)
        mock_writer.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_closes_and_opens_on_wm_class_change(self, mock_writer):
        wc = WindowCollector(mock_writer)

        await wc._handle_window(WindowInfo(wm_class="firefox", title="A", pid=1))
        mock_writer.put.reset_mock()

        await wc._handle_window(WindowInfo(wm_class="code", title="B", pid=2))

        assert mock_writer.put.call_count == 2
        calls = [(c[0][0], c[0][1]) for c in mock_writer.put.call_args_list]

        assert calls[0][0] == "window_session_close"
        assert calls[0][1]["wm_class"] == "firefox"

        assert calls[1][0] == "window_sessions"
        assert calls[1][1].wm_class == "code"

    @pytest.mark.asyncio
    async def test_closes_and_opens_on_title_change(self, mock_writer):
        wc = WindowCollector(mock_writer)

        await wc._handle_window(WindowInfo(wm_class="firefox", title="Tab 1", pid=1))
        mock_writer.put.reset_mock()

        await wc._handle_window(WindowInfo(wm_class="firefox", title="Tab 2", pid=1))

        assert mock_writer.put.call_count == 2
        calls = [(c[0][0], c[0][1]) for c in mock_writer.put.call_args_list]
        assert calls[0][0] == "window_session_close"
        assert calls[1][0] == "window_sessions"
        assert calls[1][1].window_title == "Tab 2"

    @pytest.mark.asyncio
    async def test_spinner_change_does_not_create_new_session(self, mock_writer):
        wc = WindowCollector(mock_writer)

        await wc._handle_window(WindowInfo(wm_class="terminal", title="⠐ my-project", pid=1))
        mock_writer.put.reset_mock()

        # Spinner changes but project name stays the same
        await wc._handle_window(WindowInfo(wm_class="terminal", title="⠂ my-project", pid=1))
        mock_writer.put.assert_not_called()

        await wc._handle_window(WindowInfo(wm_class="terminal", title="✳ my-project", pid=1))
        mock_writer.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_real_title_change_creates_new_session(self, mock_writer):
        wc = WindowCollector(mock_writer)

        await wc._handle_window(WindowInfo(wm_class="terminal", title="⠐ project-a", pid=1))
        mock_writer.put.reset_mock()

        await wc._handle_window(WindowInfo(wm_class="terminal", title="⠐ project-b", pid=1))
        assert mock_writer.put.call_count == 2  # close + open
