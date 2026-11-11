"""Encrypted local session storage and authentication reuse tests."""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from autodl_session import SessionStore, SessionStoreError
from autodl_instance import Client, AuthenticationExpired, AutoDLError


@unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI required')
class SessionStoreTests(unittest.TestCase):
    def test_encrypted_round_trip_account_isolation_and_logout(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            state = {'token': 'synthetic-secret', 'version': 'v1'}
            store.save('sub', state, 'first@id')
            raw = next(store.root.iterdir()).read_bytes()
            self.assertNotIn(b'synthetic-secret', raw)
            self.assertEqual(store.load('sub', 'first@id'), state)
            self.assertIsNone(store.load('sub', 'second@id'))
            self.assertIsNone(store.load('wechat'))
            store.clear('sub', 'first@id')
            self.assertIsNone(store.load('sub', 'first@id'))

    def test_corrupt_session_requires_fresh_login(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store._path('wechat').write_bytes(b'not an encrypted session')
            with self.assertRaises(SessionStoreError):
                store.load('wechat')


class SessionReuseTests(unittest.TestCase):
    def setUp(self):
        self.transport = SimpleNamespace(headers={}, cookies=MagicMock(), close=Mock())
        self.client = Client(self.transport)
        self.client.store = Mock()
        self.saved = {'cookies': [], 'origins': []}
        self.client.store.load.return_value = self.saved

    def test_main_session_is_checked_and_reused_without_new_login(self):
        restored = MagicMock()
        with patch('autodl_auth.restore_browser', return_value=restored), \
             patch('autodl_auth.headless_login') as login, \
             patch('autodl_auth.browser_state', return_value=self.saved), \
             patch.object(self.client, 'instances', return_value=[]):
            self.client.login('wechat')
        self.assertEqual(self.client.account_type, 'main')
        login.assert_not_called()
        self.client.store.save.assert_called_once()

    def test_subaccount_session_reuses_token_without_password(self):
        from curl_cffi import requests
        client = Client(requests.Session())
        client.store = Mock()
        client.store.load.return_value = {'token': 'synthetic', 'version': 'v1', 'cookies': []}
        with patch.dict(os.environ, {'AUTODL_SUB_ACCOUNT': 'fixture@id'}, clear=True), \
                patch.object(client, '_subaccount_login') as login, \
                patch.object(client, 'instances', return_value=[]):
            client.login('sub')
        self.assertEqual(client.session.headers['Authorization'], 'synthetic')
        login.assert_not_called()
        client.close()

    def test_official_authorize_failed_is_expired(self):
        with self.assertRaises(AuthenticationExpired):
            self.client._result('/api/v1/instance', {'code': 'AuthorizeFailed'})

    def test_expired_session_cleared_before_login(self):
        restored, fresh = MagicMock(), MagicMock()
        with patch('autodl_auth.restore_browser', return_value=restored), \
             patch('autodl_auth.headless_login', return_value=fresh) as login, \
             patch('autodl_auth.browser_state', return_value=self.saved), \
             patch.object(self.client, 'instances', side_effect=AuthenticationExpired('expired')):
            self.client.login('wechat')
        self.client.store.clear.assert_called_once_with('wechat', '')
        login.assert_called_once_with('wechat')
        self.client.store.save.assert_called_once()

    def test_unknown_error_does_not_reauthenticate_or_delete_session(self):
        restored = MagicMock()
        with patch('autodl_auth.restore_browser', return_value=restored), \
             patch('autodl_auth.headless_login') as login, \
             patch.object(self.client, 'instances', side_effect=AutoDLError('network')):
            with self.assertRaises(AutoDLError):
                self.client.login('wechat')
        self.client.store.clear.assert_not_called()
        login.assert_not_called()
        self.client.close()

    def test_explicit_fresh_login_skips_saved_state(self):
        fresh = MagicMock()
        with patch('autodl_auth.headless_login', return_value=fresh), \
             patch('autodl_auth.browser_state', return_value=self.saved):
            self.client.login('sms', fresh=True)
        self.client.store.clear.assert_called_once_with('sms', '')
        self.client.store.load.assert_not_called()
