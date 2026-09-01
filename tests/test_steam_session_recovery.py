import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

from steampy.client import SteamClient
from steampy.exceptions import ApiException
from steampy.login import LoginExecutor
from utils.steam_client import TokenRefreshThread, accept_trade_offer


class SteamSessionRecoveryTest(unittest.TestCase):
    def test_login_executor_creates_session_id_when_cookie_is_missing(self):
        session = requests.Session()
        executor = LoginExecutor("user", "password", "secret", session)

        sessionid = executor._get_or_create_session_id()

        self.assertEqual(len(sessionid), 24)
        self.assertEqual(session.cookies.get_dict("steamcommunity.com")["sessionid"], sessionid)
        self.assertEqual(session.cookies.get_dict("store.steampowered.com")["sessionid"], sessionid)

    def test_password_login_uses_created_session_id(self):
        session = requests.Session()
        executor = LoginExecutor("user", "password", "secret", session)
        rsa_params = MagicMock(timestamp=1)
        auth_session = MagicMock(allowed_confirmations=[], client_id=2, request_id=b"request", steamid=3)
        polled_session = MagicMock(refresh_token="refresh-token")
        finalized_login = MagicMock(transfer_info=[])

        with (
            patch.object(executor, "_fetch_rsa_params_protobuf", return_value=rsa_params),
            patch.object(executor, "_encrypt_password_protobuf", return_value="encrypted"),
            patch.object(executor, "_begin_auth_session_protobuf", return_value=auth_session),
            patch.object(executor, "_poll_auth_session_status_protobuf", return_value=polled_session),
            patch.object(executor, "_finalize_login_protobuf", return_value=finalized_login) as finalize_login,
            patch.object(executor, "_acknowledge_new_trade"),
        ):
            executor._send_login_request_protobuf()

        sessionid = session.cookies.get_dict("steamcommunity.com")["sessionid"]
        finalize_login.assert_called_once_with(refresh_token="refresh-token", sessionid=sessionid)

    def test_relogin_restores_previous_session_after_failure(self):
        client = SteamClient("")
        client.username = "user"
        client._password = "password"
        client.steam_guard = {"shared_secret": "secret"}
        client.steamid = "old-steamid"
        client.refreshToken = "old-refresh-token"
        client.was_login_executed = True
        client._session.cookies.set("steamLoginSecure", "old-steamid%7C%7Cold-token", domain="steamcommunity.com")
        client._session.cookies.set("sessionid", "old-session", domain="steamcommunity.com")

        def failed_login(*args, **kwargs):
            client.steamid = "new-steamid"
            client._session.cookies.set("temporary", "cookie", domain="steamcommunity.com")
            raise RuntimeError("login failed")

        client.login = MagicMock(side_effect=failed_login)

        with self.assertRaisesRegex(RuntimeError, "login failed"):
            client.relogin()

        cookies = client._session.cookies.get_dict("steamcommunity.com")
        self.assertEqual(cookies["steamLoginSecure"], "old-steamid%7C%7Cold-token")
        self.assertEqual(cookies["sessionid"], "old-session")
        self.assertNotIn("temporary", cookies)
        self.assertEqual(client.steamid, "old-steamid")
        self.assertEqual(client.refreshToken, "old-refresh-token")
        self.assertTrue(client.was_login_executed)

    @patch("utils.steam_client.send_notification")
    @patch("utils.steam_client._refresh_steam_session", return_value=True)
    def test_missing_login_cookie_refreshes_session_and_retries_offer(self, refresh_session, send_notification):
        client = MagicMock(spec=SteamClient)
        client.accept_trade_offer.side_effect = [ApiException("Missing steamLoginSecure cookie"), None]

        result = accept_trade_offer(client, threading.Lock(), "123", reportToExternal=False)

        self.assertTrue(result)
        self.assertEqual(client.accept_trade_offer.call_count, 2)
        refresh_session.assert_called_once_with(client)
        send_notification.assert_called_once()

    def test_failed_background_refresh_retries_in_five_minutes(self):
        thread = TokenRefreshThread(MagicMock(), {})

        self.assertEqual(thread._compute_wait_interval(refresh_failed=True), 300)


if __name__ == "__main__":
    unittest.main()
