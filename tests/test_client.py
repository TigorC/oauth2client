#!/usr/bin/python2.4
#
# Copyright 2014 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Oauth2client tests

Unit tests for oauth2client.
"""

import base64
import contextlib
import copy
import datetime
import json
import os
import socket
import sys

import mock
import six
from six.moves import http_client
from six.moves import urllib
import unittest2

from .http_mock import CacheMock
from .http_mock import HttpMock
from .http_mock import HttpMockSequence
from oauth2client import GOOGLE_REVOKE_URI
from oauth2client import GOOGLE_TOKEN_URI
from oauth2client import GOOGLE_TOKEN_INFO_URI
from oauth2client import client
from oauth2client import util as oauth2client_util
from oauth2client.client import AccessTokenCredentials
from oauth2client.client import AccessTokenCredentialsError
from oauth2client.client import HttpAccessTokenRefreshError
from oauth2client.client import ADC_HELP_MSG
from oauth2client.client import AssertionCredentials
from oauth2client.client import AUTHORIZED_USER
from oauth2client.client import Credentials
from oauth2client.client import DEFAULT_ENV_NAME
from oauth2client.client import Error
from oauth2client.client import ApplicationDefaultCredentialsError
from oauth2client.client import FlowExchangeError
from oauth2client.client import GoogleCredentials
from oauth2client.client import GOOGLE_APPLICATION_CREDENTIALS
from oauth2client.client import MemoryCache
from oauth2client.client import NonAsciiHeaderError
from oauth2client.client import OAuth2Credentials
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.client import OOB_CALLBACK_URN
from oauth2client.client import REFRESH_STATUS_CODES
from oauth2client.client import SERVICE_ACCOUNT
from oauth2client.client import Storage
from oauth2client.client import TokenRevokeError
from oauth2client.client import VerifyJwtTokenError
from oauth2client.client import _extract_id_token
from oauth2client.client import _get_application_default_credential_from_file
from oauth2client.client import _get_environment_variable_file
from oauth2client.client import _get_well_known_file
from oauth2client.client import _in_gae_environment
from oauth2client.client import _in_gce_environment
from oauth2client.client import _raise_exception_for_missing_fields
from oauth2client.client import _raise_exception_for_reading_json
from oauth2client.client import _update_query_params
from oauth2client.client import _WELL_KNOWN_CREDENTIALS_FILE
from oauth2client.client import credentials_from_clientsecrets_and_code
from oauth2client.client import credentials_from_code
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import save_to_well_known_file
from oauth2client.clientsecrets import _loadfile
from oauth2client.service_account import ServiceAccountCredentials

__author__ = 'jcgregorio@google.com (Joe Gregorio)'

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


# TODO(craigcitro): This is duplicated from
# googleapiclient.test_discovery; consolidate these definitions.
def assertUrisEqual(testcase, expected, actual):
    """Test that URIs are the same, up to reordering of query parameters."""
    expected = urllib.parse.urlparse(expected)
    actual = urllib.parse.urlparse(actual)
    testcase.assertEqual(expected.scheme, actual.scheme)
    testcase.assertEqual(expected.netloc, actual.netloc)
    testcase.assertEqual(expected.path, actual.path)
    testcase.assertEqual(expected.params, actual.params)
    testcase.assertEqual(expected.fragment, actual.fragment)
    expected_query = urllib.parse.parse_qs(expected.query)
    actual_query = urllib.parse.parse_qs(actual.query)
    for name in expected_query.keys():
        testcase.assertEqual(expected_query[name], actual_query[name])
    for name in actual_query.keys():
        testcase.assertEqual(expected_query[name], actual_query[name])


def datafile(filename):
    return os.path.join(DATA_DIR, filename)


def load_and_cache(existing_file, fakename, cache_mock):
    client_type, client_info = _loadfile(datafile(existing_file))
    cache_mock.cache[fakename] = {client_type: client_info}


class CredentialsTests(unittest2.TestCase):

    def test_to_from_json(self):
        credentials = Credentials()
        json = credentials.to_json()
        restored = Credentials.new_from_json(json)


@contextlib.contextmanager
def mock_module_import(module):
    """Place a dummy objects in sys.modules to mock an import test."""
    parts = module.split('.')
    entries = ['.'.join(parts[:i + 1]) for i in range(len(parts))]
    for entry in entries:
        sys.modules[entry] = object()

    try:
        yield

    finally:
        for entry in entries:
            del sys.modules[entry]


class GoogleCredentialsTests(unittest2.TestCase):

    def setUp(self):
        self.os_name = os.name
        from oauth2client import client
        client.SETTINGS.env_name = None

    def tearDown(self):
        self.reset_env('SERVER_SOFTWARE')
        self.reset_env(GOOGLE_APPLICATION_CREDENTIALS)
        self.reset_env('APPDATA')
        os.name = self.os_name

    def reset_env(self, env):
        """Set the environment variable 'env' to 'value'."""
        os.environ.pop(env, None)

    def validate_service_account_credentials(self, credentials):
        self.assertTrue(isinstance(credentials, ServiceAccountCredentials))
        self.assertEqual('123', credentials.client_id)
        self.assertEqual('dummy@google.com',
                         credentials._service_account_email)
        self.assertEqual('ABCDEF', credentials._private_key_id)
        self.assertEqual('', credentials._scopes)

    def validate_google_credentials(self, credentials):
        self.assertTrue(isinstance(credentials, GoogleCredentials))
        self.assertEqual(None, credentials.access_token)
        self.assertEqual('123', credentials.client_id)
        self.assertEqual('secret', credentials.client_secret)
        self.assertEqual('alabalaportocala', credentials.refresh_token)
        self.assertEqual(None, credentials.token_expiry)
        self.assertEqual(GOOGLE_TOKEN_URI, credentials.token_uri)
        self.assertEqual('Python client library', credentials.user_agent)

    def get_a_google_credentials_object(self):
        return GoogleCredentials(None, None, None, None,
                                 None, None, None, None)

    def test_create_scoped_required(self):
        self.assertFalse(
            self.get_a_google_credentials_object().create_scoped_required())

    def test_create_scoped(self):
        credentials = self.get_a_google_credentials_object()
        self.assertEqual(credentials, credentials.create_scoped(None))
        self.assertEqual(credentials,
                         credentials.create_scoped(['dummy_scope']))

    def test_environment_check_gae_production(self):
        with mock_module_import('google.appengine'):
            self._environment_check_gce_helper(
                server_software='Google App Engine/XYZ')

    def test_environment_check_gae_local(self):
        with mock_module_import('google.appengine'):
            self._environment_check_gce_helper(
                server_software='Development/XYZ')

    def test_environment_check_fastpath(self):
        with mock_module_import('google.appengine'):
            self._environment_check_gce_helper(
                server_software='Development/XYZ')

    def test_environment_caching(self):
        os.environ['SERVER_SOFTWARE'] = 'Development/XYZ'
        with mock_module_import('google.appengine'):
            self.assertTrue(_in_gae_environment())
            os.environ['SERVER_SOFTWARE'] = ''
            # Even though we no longer pass the environment check, it
            # is cached.
            self.assertTrue(_in_gae_environment())

    def _environment_check_gce_helper(self, status_ok=True, socket_error=False,
                                      server_software=''):
        response = mock.MagicMock()
        if status_ok:
            response.status = http_client.OK
            response.getheader = mock.MagicMock(
                name='getheader',
                return_value=client._DESIRED_METADATA_FLAVOR)
        else:
            response.status = http_client.NOT_FOUND

        connection = mock.MagicMock()
        connection.getresponse = mock.MagicMock(name='getresponse',
                                                return_value=response)
        if socket_error:
            connection.getresponse.side_effect = socket.error()

        with mock.patch('oauth2client.client.os') as os_module:
            os_module.environ = {client._SERVER_SOFTWARE: server_software}
            with mock.patch('oauth2client.client.six') as six_module:
                http_client_module = six_module.moves.http_client
                http_client_module.HTTPConnection = mock.MagicMock(
                    name='HTTPConnection', return_value=connection)

                if server_software == '':
                    self.assertFalse(_in_gae_environment())
                else:
                    self.assertTrue(_in_gae_environment())

                if status_ok and not socket_error and server_software == '':
                    self.assertTrue(_in_gce_environment())
                else:
                    self.assertFalse(_in_gce_environment())

                if server_software == '':
                    http_client_module.HTTPConnection.assert_called_once_with(
                        client._GCE_METADATA_HOST, timeout=1)
                    connection.getresponse.assert_called_once_with()
                    # Remaining calls are not "getresponse"
                    headers = {
                        client._METADATA_FLAVOR_HEADER: (
                            client._DESIRED_METADATA_FLAVOR),
                    }
                    self.assertEqual(connection.method_calls, [
                        mock.call.request('GET', '/',
                                          headers=headers),
                        mock.call.close(),
                    ])
                    self.assertEqual(response.method_calls, [])
                    if status_ok and not socket_error:
                        response.getheader.assert_called_once_with(
                            client._METADATA_FLAVOR_HEADER)
                else:
                    self.assertEqual(
                            http_client_module.HTTPConnection.mock_calls, [])
                    self.assertEqual(connection.getresponse.mock_calls, [])
                    # Remaining calls are not "getresponse"
                    self.assertEqual(connection.method_calls, [])
                    self.assertEqual(response.method_calls, [])
                    self.assertEqual(response.getheader.mock_calls, [])

    def test_environment_check_gce_production(self):
        self._environment_check_gce_helper(status_ok=True)

    def test_environment_check_gce_prod_with_working_gae_imports(self):
        with mock_module_import('google.appengine'):
            self._environment_check_gce_helper(status_ok=True)

    def test_environment_check_gce_timeout(self):
        self._environment_check_gce_helper(socket_error=True)

    def test_environ_check_gae_module_unknown(self):
        with mock_module_import('google.appengine'):
            self._environment_check_gce_helper(status_ok=False)

    def test_environment_check_unknown(self):
        self._environment_check_gce_helper(status_ok=False)

    def test_get_environment_variable_file(self):
        environment_variable_file = datafile(
            os.path.join('gcloud', _WELL_KNOWN_CREDENTIALS_FILE))
        os.environ[GOOGLE_APPLICATION_CREDENTIALS] = environment_variable_file
        self.assertEqual(environment_variable_file,
                         _get_environment_variable_file())

    def test_get_environment_variable_file_error(self):
        nonexistent_file = datafile('nonexistent')
        os.environ[GOOGLE_APPLICATION_CREDENTIALS] = nonexistent_file
        # we can't use self.assertRaisesRegexp() because it is only in
        # Python 2.7+
        try:
            _get_environment_variable_file()
            self.fail(nonexistent_file + ' should not exist.')
        except ApplicationDefaultCredentialsError as error:
            self.assertEqual('File ' + nonexistent_file +
                             ' (pointed by ' + GOOGLE_APPLICATION_CREDENTIALS +
                             ' environment variable) does not exist!',
                             str(error))

    def test_get_well_known_file_on_windows(self):
        ORIGINAL_ISDIR = os.path.isdir
        try:
            os.path.isdir = lambda path: True
            well_known_file = datafile(
                os.path.join(client._CLOUDSDK_CONFIG_DIRECTORY,
                             _WELL_KNOWN_CREDENTIALS_FILE))
            os.name = 'nt'
            os.environ['APPDATA'] = DATA_DIR
            self.assertEqual(well_known_file, _get_well_known_file())
        finally:
            os.path.isdir = ORIGINAL_ISDIR

    def test_get_well_known_file_with_custom_config_dir(self):
        ORIGINAL_ENVIRON = os.environ
        ORIGINAL_ISDIR = os.path.isdir
        CUSTOM_DIR = 'CUSTOM_DIR'
        EXPECTED_FILE = os.path.join(CUSTOM_DIR,
                                     _WELL_KNOWN_CREDENTIALS_FILE)
        try:
            os.environ = {client._CLOUDSDK_CONFIG_ENV_VAR: CUSTOM_DIR}
            os.path.isdir = lambda path: True
            well_known_file = _get_well_known_file()
            self.assertEqual(well_known_file, EXPECTED_FILE)
        finally:
            os.environ = ORIGINAL_ENVIRON
            os.path.isdir = ORIGINAL_ISDIR

    def test_get_adc_from_file_service_account(self):
        credentials_file = datafile(
            os.path.join('gcloud', _WELL_KNOWN_CREDENTIALS_FILE))
        credentials = _get_application_default_credential_from_file(
            credentials_file)
        self.validate_service_account_credentials(credentials)

    def test_save_to_well_known_file_service_account(self):
        credential_file = datafile(
            os.path.join('gcloud', _WELL_KNOWN_CREDENTIALS_FILE))
        credentials = _get_application_default_credential_from_file(
            credential_file)
        temp_credential_file = datafile(
            os.path.join('gcloud',
                         'temp_well_known_file_service_account.json'))
        save_to_well_known_file(credentials, temp_credential_file)
        with open(temp_credential_file) as f:
            d = json.load(f)
        self.assertEqual('service_account', d['type'])
        self.assertEqual('123', d['client_id'])
        self.assertEqual('dummy@google.com', d['client_email'])
        self.assertEqual('ABCDEF', d['private_key_id'])
        os.remove(temp_credential_file)

    def test_save_well_known_file_with_non_existent_config_dir(self):
        credential_file = datafile(
            os.path.join('gcloud', _WELL_KNOWN_CREDENTIALS_FILE))
        credentials = _get_application_default_credential_from_file(
            credential_file)
        ORIGINAL_ISDIR = os.path.isdir
        try:
            os.path.isdir = lambda path: False
            self.assertRaises(OSError, save_to_well_known_file, credentials)
        finally:
            os.path.isdir = ORIGINAL_ISDIR

    def test_get_adc_from_file_authorized_user(self):
        credentials_file = datafile(os.path.join(
            'gcloud',
            'application_default_credentials_authorized_user.json'))
        credentials = _get_application_default_credential_from_file(
            credentials_file)
        self.validate_google_credentials(credentials)

    def test_save_to_well_known_file_authorized_user(self):
        credentials_file = datafile(os.path.join(
            'gcloud',
            'application_default_credentials_authorized_user.json'))
        credentials = _get_application_default_credential_from_file(
            credentials_file)
        temp_credential_file = datafile(
            os.path.join('gcloud',
                         'temp_well_known_file_authorized_user.json'))
        save_to_well_known_file(credentials, temp_credential_file)
        with open(temp_credential_file) as f:
            d = json.load(f)
        self.assertEqual('authorized_user', d['type'])
        self.assertEqual('123', d['client_id'])
        self.assertEqual('secret', d['client_secret'])
        self.assertEqual('alabalaportocala', d['refresh_token'])
        os.remove(temp_credential_file)

    def test_get_application_default_credential_from_malformed_file_1(self):
        credentials_file = datafile(
            os.path.join('gcloud',
                         'application_default_credentials_malformed_1.json'))
        # we can't use self.assertRaisesRegexp() because it is only in
        # Python 2.7+
        try:
            _get_application_default_credential_from_file(credentials_file)
            self.fail('An exception was expected!')
        except ApplicationDefaultCredentialsError as error:
            self.assertEqual("'type' field should be defined "
                             "(and have one of the '" + AUTHORIZED_USER +
                             "' or '" + SERVICE_ACCOUNT + "' values)",
                             str(error))

    def test_get_application_default_credential_from_malformed_file_2(self):
        credentials_file = datafile(
            os.path.join('gcloud',
                         'application_default_credentials_malformed_2.json'))
        # we can't use self.assertRaisesRegexp() because it is only in
        # Python 2.7+
        try:
            _get_application_default_credential_from_file(credentials_file)
            self.fail('An exception was expected!')
        except ApplicationDefaultCredentialsError as error:
            self.assertEqual(
                'The following field(s) must be defined: private_key_id',
                str(error))

    def test_get_application_default_credential_from_malformed_file_3(self):
        credentials_file = datafile(
            os.path.join('gcloud',
                         'application_default_credentials_malformed_3.json'))
        self.assertRaises(ValueError,
                          _get_application_default_credential_from_file,
                          credentials_file)

    def test_raise_exception_for_missing_fields(self):
        missing_fields = ['first', 'second', 'third']
        # we can't use self.assertRaisesRegexp() because it is only in
        # Python 2.7+
        try:
            _raise_exception_for_missing_fields(missing_fields)
            self.fail('An exception was expected!')
        except ApplicationDefaultCredentialsError as error:
            self.assertEqual('The following field(s) must be defined: ' +
                             ', '.join(missing_fields),
                             str(error))

    def test_raise_exception_for_reading_json(self):
        credential_file = 'any_file'
        extra_help = ' be good'
        error = ApplicationDefaultCredentialsError('stuff happens')
        # we can't use self.assertRaisesRegexp() because it is only in
        # Python 2.7+
        try:
            _raise_exception_for_reading_json(credential_file,
                                              extra_help, error)
            self.fail('An exception was expected!')
        except ApplicationDefaultCredentialsError as ex:
            self.assertEqual('An error was encountered while reading '
                             'json file: ' + credential_file +
                             extra_help + ': ' + str(error),
                             str(ex))

    @mock.patch('oauth2client.client._in_gce_environment')
    @mock.patch('oauth2client.client._in_gae_environment', return_value=False)
    @mock.patch('oauth2client.client._get_environment_variable_file')
    @mock.patch('oauth2client.client._get_well_known_file')
    def test_get_adc_from_environment_variable_service_account(self, *stubs):
        # Set up stubs.
        get_well_known, get_env_file, in_gae, in_gce = stubs
        get_env_file.return_value = datafile(
            os.path.join('gcloud', _WELL_KNOWN_CREDENTIALS_FILE))

        credentials = GoogleCredentials.get_application_default()
        self.validate_service_account_credentials(credentials)

        get_well_known.assert_not_called()
        in_gce.assert_not_called()
        get_env_file.assert_called_once_with()
        in_gae.assert_called_once_with()

    def test_env_name(self):
        from oauth2client import client
        self.assertEqual(None, client.SETTINGS.env_name)
        self.test_get_adc_from_environment_variable_service_account()
        self.assertEqual(DEFAULT_ENV_NAME, client.SETTINGS.env_name)

    @mock.patch('oauth2client.client._in_gce_environment')
    @mock.patch('oauth2client.client._in_gae_environment', return_value=False)
    @mock.patch('oauth2client.client._get_environment_variable_file')
    @mock.patch('oauth2client.client._get_well_known_file')
    def test_get_adc_from_environment_variable_authorized_user(self, *stubs):
        # Set up stubs.
        get_well_known, get_env_file, in_gae, in_gce = stubs
        get_env_file.return_value = datafile(os.path.join(
            'gcloud',
            'application_default_credentials_authorized_user.json'))

        credentials = GoogleCredentials.get_application_default()
        self.validate_google_credentials(credentials)

        get_well_known.assert_not_called()
        in_gce.assert_not_called()
        get_env_file.assert_called_once_with()
        in_gae.assert_called_once_with()

    @mock.patch('oauth2client.client._in_gce_environment')
    @mock.patch('oauth2client.client._in_gae_environment', return_value=False)
    @mock.patch('oauth2client.client._get_environment_variable_file')
    @mock.patch('oauth2client.client._get_well_known_file')
    def test_get_adc_from_environment_variable_malformed_file(self, *stubs):
        # Set up stubs.
        get_well_known, get_env_file, in_gae, in_gce = stubs
        get_env_file.return_value = datafile(
            os.path.join('gcloud',
                         'application_default_credentials_malformed_3.json'))

        expected_err = ApplicationDefaultCredentialsError
        with self.assertRaises(expected_err) as exc_manager:
            GoogleCredentials.get_application_default()

        self.assertTrue(str(exc_manager.exception).startswith(
            'An error was encountered while reading json file: ' +
            get_env_file.return_value + ' (pointed to by ' +
            GOOGLE_APPLICATION_CREDENTIALS + ' environment variable):'))

        get_well_known.assert_not_called()
        in_gce.assert_not_called()
        get_env_file.assert_called_once_with()
        in_gae.assert_called_once_with()

    @mock.patch('oauth2client.client._in_gce_environment', return_value=False)
    @mock.patch('oauth2client.client._in_gae_environment', return_value=False)
    @mock.patch('oauth2client.client._get_environment_variable_file',
                return_value=None)
    @mock.patch('oauth2client.client._get_well_known_file',
                return_value='BOGUS_FILE')
    def test_get_application_default_environment_not_set_up(self, *stubs):
        # Unpack stubs.
        get_well_known, get_env_file, in_gae, in_gce = stubs
        # Make sure the well-known file actually doesn't exist.
        self.assertFalse(os.path.exists(get_well_known.return_value))

        expected_err = ApplicationDefaultCredentialsError
        with self.assertRaises(expected_err) as exc_manager:
            GoogleCredentials.get_application_default()

        self.assertEqual(ADC_HELP_MSG, str(exc_manager.exception))
        get_well_known.assert_called_once_with()
        get_env_file.assert_called_once_with()
        in_gae.assert_called_once_with()
        in_gce.assert_called_once_with()

    def test_from_stream_service_account(self):
        credentials_file = datafile(
            os.path.join('gcloud', _WELL_KNOWN_CREDENTIALS_FILE))
        credentials = self.get_a_google_credentials_object().from_stream(
            credentials_file)
        self.validate_service_account_credentials(credentials)

    def test_from_stream_authorized_user(self):
        credentials_file = datafile(os.path.join(
            'gcloud',
            'application_default_credentials_authorized_user.json'))
        credentials = self.get_a_google_credentials_object().from_stream(
            credentials_file)
        self.validate_google_credentials(credentials)

    def test_from_stream_malformed_file_1(self):
        credentials_file = datafile(
            os.path.join('gcloud',
                         'application_default_credentials_malformed_1.json'))
        # we can't use self.assertRaisesRegexp() because it is only in
        # Python 2.7+
        try:
            self.get_a_google_credentials_object().from_stream(
                credentials_file)
            self.fail('An exception was expected!')
        except ApplicationDefaultCredentialsError as error:
            self.assertEqual(
                "An error was encountered while reading json file: " +
                credentials_file +
                " (provided as parameter to the from_stream() method): "
                "'type' field should be defined (and have one of the '" +
                AUTHORIZED_USER + "' or '" + SERVICE_ACCOUNT +
                "' values)",
                str(error))

    def test_from_stream_malformed_file_2(self):
        credentials_file = datafile(
            os.path.join('gcloud',
                         'application_default_credentials_malformed_2.json'))
        # we can't use self.assertRaisesRegexp() because it is only in
        # Python 2.7+
        try:
            self.get_a_google_credentials_object().from_stream(
                credentials_file)
            self.fail('An exception was expected!')
        except ApplicationDefaultCredentialsError as error:
            self.assertEqual(
                'An error was encountered while reading json file: ' +
                credentials_file +
                ' (provided as parameter to the from_stream() method): '
                'The following field(s) must be defined: '
                'private_key_id',
                str(error))

    def test_from_stream_malformed_file_3(self):
        credentials_file = datafile(
            os.path.join('gcloud',
                         'application_default_credentials_malformed_3.json'))
        self.assertRaises(
            ApplicationDefaultCredentialsError,
            self.get_a_google_credentials_object().from_stream,
            credentials_file)

    def test_to_from_json_authorized_user(self):
        filename = 'application_default_credentials_authorized_user.json'
        credentials_file = datafile(os.path.join('gcloud', filename))
        creds = GoogleCredentials.from_stream(credentials_file)
        json = creds.to_json()
        creds2 = GoogleCredentials.from_json(json)

        self.assertEqual(creds.__dict__, creds2.__dict__)

    def test_to_from_json_service_account(self):
        credentials_file = datafile(
            os.path.join('gcloud', _WELL_KNOWN_CREDENTIALS_FILE))
        creds1 = GoogleCredentials.from_stream(credentials_file)
        # Convert to and then back from json.
        creds2 = GoogleCredentials.from_json(creds1.to_json())

        creds1_vals = creds1.__dict__
        creds1_vals.pop('_signer')
        creds2_vals = creds2.__dict__
        creds2_vals.pop('_signer')
        self.assertEqual(creds1_vals, creds2_vals)

    def test_parse_expiry(self):
        dt = datetime.datetime(2016, 1, 1)
        parsed_expiry = client._parse_expiry(dt)
        self.assertEqual('2016-01-01T00:00:00Z', parsed_expiry)

    def test_bad_expiry(self):
        dt = object()
        parsed_expiry = client._parse_expiry(dt)
        self.assertEqual(None, parsed_expiry)

class DummyDeleteStorage(Storage):
    delete_called = False

    def locked_delete(self):
        self.delete_called = True


def _token_revoke_test_helper(testcase, status, revoke_raise,
                              valid_bool_value, token_attr):
    current_store = getattr(testcase.credentials, 'store', None)

    dummy_store = DummyDeleteStorage()
    testcase.credentials.set_store(dummy_store)

    actual_do_revoke = testcase.credentials._do_revoke
    testcase.token_from_revoke = None

    def do_revoke_stub(http_request, token):
        testcase.token_from_revoke = token
        return actual_do_revoke(http_request, token)
    testcase.credentials._do_revoke = do_revoke_stub

    http = HttpMock(headers={'status': status})
    if revoke_raise:
        testcase.assertRaises(TokenRevokeError,
                              testcase.credentials.revoke, http)
    else:
        testcase.credentials.revoke(http)

    testcase.assertEqual(getattr(testcase.credentials, token_attr),
                         testcase.token_from_revoke)
    testcase.assertEqual(valid_bool_value, testcase.credentials.invalid)
    testcase.assertEqual(valid_bool_value, dummy_store.delete_called)

    testcase.credentials.set_store(current_store)


class BasicCredentialsTests(unittest2.TestCase):

    def setUp(self):
        access_token = 'foo'
        client_id = 'some_client_id'
        client_secret = 'cOuDdkfjxxnv+'
        refresh_token = '1/0/a.df219fjls0'
        token_expiry = datetime.datetime.utcnow()
        user_agent = 'refresh_checker/1.0'
        self.credentials = OAuth2Credentials(
            access_token, client_id, client_secret,
            refresh_token, token_expiry, GOOGLE_TOKEN_URI,
            user_agent, revoke_uri=GOOGLE_REVOKE_URI, scopes='foo',
            token_info_uri=GOOGLE_TOKEN_INFO_URI)

        # Provoke a failure if @util.positional is not respected.
        self.old_positional_enforcement = (
            oauth2client_util.positional_parameters_enforcement)
        oauth2client_util.positional_parameters_enforcement = (
            oauth2client_util.POSITIONAL_EXCEPTION)

    def tearDown(self):
        oauth2client_util.positional_parameters_enforcement = (
            self.old_positional_enforcement)

    def test_token_refresh_success(self):
        for status_code in REFRESH_STATUS_CODES:
            token_response = {'access_token': '1/3w', 'expires_in': 3600}
            http = HttpMockSequence([
                ({'status': status_code}, b''),
                ({'status': '200'}, json.dumps(token_response).encode(
                    'utf-8')),
                ({'status': '200'}, 'echo_request_headers'),
            ])
            http = self.credentials.authorize(http)
            resp, content = http.request('http://example.com')
            self.assertEqual(b'Bearer 1/3w', content[b'Authorization'])
            self.assertFalse(self.credentials.access_token_expired)
            self.assertEqual(token_response, self.credentials.token_response)

    def test_recursive_authorize(self):
        """Tests that OAuth2Credentials doesn't intro. new method constraints.

        Formerly, OAuth2Credentials.authorize monkeypatched the request method
        of its httplib2.Http argument with a wrapper annotated with
        @util.positional(1). Since the original method has no such annotation,
        that meant that the wrapper was violating the contract of the original
        method by adding a new requirement to it. And in fact the wrapper
        itself doesn't even respect that requirement. So before the removal of
        the annotation, this test would fail.
        """
        token_response = {'access_token': '1/3w', 'expires_in': 3600}
        encoded_response = json.dumps(token_response).encode('utf-8')
        http = HttpMockSequence([
            ({'status': '200'}, encoded_response),
        ])
        http = self.credentials.authorize(http)
        http = self.credentials.authorize(http)
        http.request('http://example.com')

    def test_token_refresh_failure(self):
        for status_code in REFRESH_STATUS_CODES:
            http = HttpMockSequence([
                ({'status': status_code}, b''),
                ({'status': http_client.BAD_REQUEST},
                 b'{"error":"access_denied"}'),
            ])
            http = self.credentials.authorize(http)
            try:
                http.request('http://example.com')
                self.fail('should raise HttpAccessTokenRefreshError exception')
            except HttpAccessTokenRefreshError as e:
                self.assertEqual(http_client.BAD_REQUEST, e.status)
            self.assertTrue(self.credentials.access_token_expired)
            self.assertEqual(None, self.credentials.token_response)

    def test_token_revoke_success(self):
        _token_revoke_test_helper(
            self, '200', revoke_raise=False,
            valid_bool_value=True, token_attr='refresh_token')

    def test_token_revoke_failure(self):
        _token_revoke_test_helper(
            self, '400', revoke_raise=True,
            valid_bool_value=False, token_attr='refresh_token')

    def test_token_revoke_fallback(self):
        original_credentials = self.credentials.to_json()
        self.credentials.refresh_token = None
        _token_revoke_test_helper(
            self, '200', revoke_raise=False,
            valid_bool_value=True, token_attr='access_token')
        self.credentials = self.credentials.from_json(original_credentials)

    def test_non_401_error_response(self):
        http = HttpMockSequence([
            ({'status': '400'}, b''),
        ])
        http = self.credentials.authorize(http)
        resp, content = http.request('http://example.com')
        self.assertEqual(http_client.BAD_REQUEST, resp.status)
        self.assertEqual(None, self.credentials.token_response)

    def test_to_from_json(self):
        json = self.credentials.to_json()
        instance = OAuth2Credentials.from_json(json)
        self.assertEqual(OAuth2Credentials, type(instance))
        instance.token_expiry = None
        self.credentials.token_expiry = None

        self.assertEqual(instance.__dict__, self.credentials.__dict__)

    def test_from_json_token_expiry(self):
        data = json.loads(self.credentials.to_json())
        data['token_expiry'] = None
        instance = OAuth2Credentials.from_json(json.dumps(data))
        self.assertTrue(isinstance(instance, OAuth2Credentials))

    def test_from_json_bad_token_expiry(self):
        data = json.loads(self.credentials.to_json())
        data['token_expiry'] = 'foobar'
        instance = OAuth2Credentials.from_json(json.dumps(data))
        self.assertTrue(isinstance(instance, OAuth2Credentials))

    def test_unicode_header_checks(self):
        access_token = u'foo'
        client_id = u'some_client_id'
        client_secret = u'cOuDdkfjxxnv+'
        refresh_token = u'1/0/a.df219fjls0'
        token_expiry = str(datetime.datetime.utcnow())
        token_uri = str(GOOGLE_TOKEN_URI)
        revoke_uri = str(GOOGLE_REVOKE_URI)
        user_agent = u'refresh_checker/1.0'
        credentials = OAuth2Credentials(access_token, client_id, client_secret,
                                        refresh_token, token_expiry, token_uri,
                                        user_agent, revoke_uri=revoke_uri)

        # First, test that we correctly encode basic objects, making sure
        # to include a bytes object. Note that oauth2client will normalize
        # everything to bytes, no matter what python version we're in.
        http = credentials.authorize(HttpMock())
        headers = {u'foo': 3, b'bar': True, 'baz': b'abc'}
        cleaned_headers = {b'foo': b'3', b'bar': b'True', b'baz': b'abc'}
        http.request(u'http://example.com', method=u'GET', headers=headers)
        for k, v in cleaned_headers.items():
            self.assertTrue(k in http.headers)
            self.assertEqual(v, http.headers[k])

        # Next, test that we do fail on unicode.
        unicode_str = six.unichr(40960) + 'abcd'
        self.assertRaises(
            NonAsciiHeaderError,
            http.request,
            u'http://example.com', method=u'GET',
            headers={u'foo': unicode_str})

    def test_no_unicode_in_request_params(self):
        access_token = u'foo'
        client_id = u'some_client_id'
        client_secret = u'cOuDdkfjxxnv+'
        refresh_token = u'1/0/a.df219fjls0'
        token_expiry = str(datetime.datetime.utcnow())
        token_uri = str(GOOGLE_TOKEN_URI)
        revoke_uri = str(GOOGLE_REVOKE_URI)
        user_agent = u'refresh_checker/1.0'
        credentials = OAuth2Credentials(access_token, client_id, client_secret,
                                        refresh_token, token_expiry, token_uri,
                                        user_agent, revoke_uri=revoke_uri)

        http = HttpMock()
        http = credentials.authorize(http)
        http.request(u'http://example.com', method=u'GET',
                     headers={u'foo': u'bar'})
        for k, v in six.iteritems(http.headers):
            self.assertTrue(isinstance(k, six.binary_type))
            self.assertTrue(isinstance(v, six.binary_type))

        # Test again with unicode strings that can't simply be converted
        # to ASCII.
        try:
            http.request(
                u'http://example.com', method=u'GET',
                headers={u'foo': u'\N{COMET}'})
            self.fail('Expected exception to be raised.')
        except NonAsciiHeaderError:
            pass

        self.credentials.token_response = 'foobar'
        instance = OAuth2Credentials.from_json(self.credentials.to_json())
        self.assertEqual('foobar', instance.token_response)

    @mock.patch('oauth2client.client._UTCNOW')
    def test_get_access_token(self, utcnow):
        # Configure the patch.
        seconds = 11
        NOW = datetime.datetime(1992, 12, 31, second=seconds)
        utcnow.return_value = NOW

        lifetime = 2  # number of seconds in which the token expires
        EXPIRY_TIME = datetime.datetime(1992, 12, 31,
                                        second=seconds + lifetime)

        token1 = u'first_token'
        token_response_first = {
            'access_token': token1,
            'expires_in': lifetime,
        }
        token2 = u'second_token'
        token_response_second = {
            'access_token': token2,
            'expires_in': lifetime,
        }
        http = HttpMockSequence([
            ({'status': '200'}, json.dumps(token_response_first).encode(
                'utf-8')),
            ({'status': '200'}, json.dumps(token_response_second).encode(
                'utf-8')),
        ])

        # Use the current credentials but unset the expiry and
        # the access token.
        credentials = copy.deepcopy(self.credentials)
        credentials.access_token = None
        credentials.token_expiry = None

        # Get Access Token, First attempt.
        self.assertEqual(credentials.access_token, None)
        self.assertFalse(credentials.access_token_expired)
        self.assertEqual(credentials.token_expiry, None)
        token = credentials.get_access_token(http=http)
        self.assertEqual(credentials.token_expiry, EXPIRY_TIME)
        self.assertEqual(token1, token.access_token)
        self.assertEqual(lifetime, token.expires_in)
        self.assertEqual(token_response_first, credentials.token_response)
        # Two utcnow calls are expected:
        # - get_access_token() -> _do_refresh_request (setting expires in)
        # - get_access_token() -> _expires_in()
        expected_utcnow_calls = [mock.call()] * 2
        self.assertEqual(expected_utcnow_calls, utcnow.mock_calls)

        # Get Access Token, Second Attempt (not expired)
        self.assertEqual(credentials.access_token, token1)
        self.assertFalse(credentials.access_token_expired)
        token = credentials.get_access_token(http=http)
        # Make sure no refresh occurred since the token was not expired.
        self.assertEqual(token1, token.access_token)
        self.assertEqual(lifetime, token.expires_in)
        self.assertEqual(token_response_first, credentials.token_response)
        # Three more utcnow calls are expected:
        # - access_token_expired
        # - get_access_token() -> access_token_expired
        # - get_access_token -> _expires_in
        expected_utcnow_calls = [mock.call()] * (2 + 3)
        self.assertEqual(expected_utcnow_calls, utcnow.mock_calls)

        # Get Access Token, Third Attempt (force expiration)
        self.assertEqual(credentials.access_token, token1)
        credentials.token_expiry = NOW  # Manually force expiry.
        self.assertTrue(credentials.access_token_expired)
        token = credentials.get_access_token(http=http)
        # Make sure refresh occurred since the token was not expired.
        self.assertEqual(token2, token.access_token)
        self.assertEqual(lifetime, token.expires_in)
        self.assertFalse(credentials.access_token_expired)
        self.assertEqual(token_response_second,
                         credentials.token_response)
        # Five more utcnow calls are expected:
        # - access_token_expired
        # - get_access_token -> access_token_expired
        # - get_access_token -> _do_refresh_request
        # - get_access_token -> _expires_in
        # - access_token_expired
        expected_utcnow_calls = [mock.call()] * (2 + 3 + 5)
        self.assertEqual(expected_utcnow_calls, utcnow.mock_calls)

    def test_has_scopes(self):
        self.assertTrue(self.credentials.has_scopes('foo'))
        self.assertTrue(self.credentials.has_scopes(['foo']))
        self.assertFalse(self.credentials.has_scopes('bar'))
        self.assertFalse(self.credentials.has_scopes(['bar']))

        self.credentials.scopes = set(['foo', 'bar'])
        self.assertTrue(self.credentials.has_scopes('foo'))
        self.assertTrue(self.credentials.has_scopes('bar'))
        self.assertFalse(self.credentials.has_scopes('baz'))
        self.assertTrue(self.credentials.has_scopes(['foo', 'bar']))
        self.assertFalse(self.credentials.has_scopes(['foo', 'baz']))

        self.credentials.scopes = set([])
        self.assertFalse(self.credentials.has_scopes('foo'))

    def test_retrieve_scopes(self):
        info_response_first = {'scope': 'foo bar'}
        info_response_second = {'error_description': 'abcdef'}
        http = HttpMockSequence([
            ({'status': '200'}, json.dumps(info_response_first).encode(
                'utf-8')),
            ({'status': '400'}, json.dumps(info_response_second).encode(
                'utf-8')),
            ({'status': '500'}, b''),
        ])

        self.credentials.retrieve_scopes(http)
        self.assertEqual(set(['foo', 'bar']), self.credentials.scopes)

        self.assertRaises(
            Error,
            self.credentials.retrieve_scopes,
            http)

        self.assertRaises(
            Error,
            self.credentials.retrieve_scopes,
            http)

    def test_refresh_updates_id_token(self):
        for status_code in REFRESH_STATUS_CODES:
            body = {'foo': 'bar'}
            body_json = json.dumps(body).encode('ascii')
            payload = base64.urlsafe_b64encode(body_json).strip(b'=')
            jwt = b'stuff.' + payload + b'.signature'

            token_response = (b'{'
                              b'  "access_token":"1/3w",'
                              b'  "expires_in":3600,'
                              b'  "id_token": "' + jwt + b'"'
                              b'}')
            http = HttpMockSequence([
                ({'status': status_code}, b''),
                ({'status': '200'}, token_response),
                ({'status': '200'}, 'echo_request_headers'),
            ])
            http = self.credentials.authorize(http)
            resp, content = http.request('http://example.com')
            self.assertEqual(self.credentials.id_token, body)


class AccessTokenCredentialsTests(unittest2.TestCase):

    def setUp(self):
        access_token = 'foo'
        user_agent = 'refresh_checker/1.0'
        self.credentials = AccessTokenCredentials(access_token, user_agent,
                                                  revoke_uri=GOOGLE_REVOKE_URI)

    def test_token_refresh_success(self):
        for status_code in REFRESH_STATUS_CODES:
            http = HttpMockSequence([
                ({'status': status_code}, b''),
            ])
            http = self.credentials.authorize(http)
            try:
                resp, content = http.request('http://example.com')
                self.fail('should throw exception if token expires')
            except AccessTokenCredentialsError:
                pass

    def test_token_revoke_success(self):
        _token_revoke_test_helper(
            self, '200', revoke_raise=False,
            valid_bool_value=True, token_attr='access_token')

    def test_token_revoke_failure(self):
        _token_revoke_test_helper(
            self, '400', revoke_raise=True,
            valid_bool_value=False, token_attr='access_token')

    def test_non_401_error_response(self):
        http = HttpMockSequence([
            ({'status': '400'}, b''),
        ])
        http = self.credentials.authorize(http)
        resp, content = http.request('http://example.com')
        self.assertEqual(http_client.BAD_REQUEST, resp.status)

    def test_auth_header_sent(self):
        http = HttpMockSequence([
            ({'status': '200'}, 'echo_request_headers'),
        ])
        http = self.credentials.authorize(http)
        resp, content = http.request('http://example.com')
        self.assertEqual(b'Bearer foo', content[b'Authorization'])


class TestAssertionCredentials(unittest2.TestCase):
    assertion_text = 'This is the assertion'
    assertion_type = 'http://www.google.com/assertionType'

    class AssertionCredentialsTestImpl(AssertionCredentials):

        def _generate_assertion(self):
            return TestAssertionCredentials.assertion_text

    def setUp(self):
        user_agent = 'fun/2.0'
        self.credentials = self.AssertionCredentialsTestImpl(
            self.assertion_type, user_agent=user_agent)

    def test_assertion_body(self):
        body = urllib.parse.parse_qs(
            self.credentials._generate_refresh_request_body())
        self.assertEqual(self.assertion_text, body['assertion'][0])
        self.assertEqual('urn:ietf:params:oauth:grant-type:jwt-bearer',
                         body['grant_type'][0])

    def test_assertion_refresh(self):
        http = HttpMockSequence([
            ({'status': '200'}, b'{"access_token":"1/3w"}'),
            ({'status': '200'}, 'echo_request_headers'),
        ])
        http = self.credentials.authorize(http)
        resp, content = http.request('http://example.com')
        self.assertEqual(b'Bearer 1/3w', content[b'Authorization'])

    def test_token_revoke_success(self):
        _token_revoke_test_helper(
            self, '200', revoke_raise=False,
            valid_bool_value=True, token_attr='access_token')

    def test_token_revoke_failure(self):
        _token_revoke_test_helper(
            self, '400', revoke_raise=True,
            valid_bool_value=False, token_attr='access_token')


class UpdateQueryParamsTest(unittest2.TestCase):
    def test_update_query_params_no_params(self):
        uri = 'http://www.google.com'
        updated = _update_query_params(uri, {'a': 'b'})
        self.assertEqual(updated, uri + '?a=b')

    def test_update_query_params_existing_params(self):
        uri = 'http://www.google.com?x=y'
        updated = _update_query_params(uri, {'a': 'b', 'c': 'd&'})
        hardcoded_update = uri + '&a=b&c=d%26'
        assertUrisEqual(self, updated, hardcoded_update)


class ExtractIdTokenTest(unittest2.TestCase):
    """Tests _extract_id_token()."""

    def test_extract_success(self):
        body = {'foo': 'bar'}
        body_json = json.dumps(body).encode('ascii')
        payload = base64.urlsafe_b64encode(body_json).strip(b'=')
        jwt = b'stuff.' + payload + b'.signature'

        extracted = _extract_id_token(jwt)
        self.assertEqual(extracted, body)

    def test_extract_failure(self):
        body = {'foo': 'bar'}
        body_json = json.dumps(body).encode('ascii')
        payload = base64.urlsafe_b64encode(body_json).strip(b'=')
        jwt = b'stuff.' + payload
        self.assertRaises(VerifyJwtTokenError, _extract_id_token, jwt)


class OAuth2WebServerFlowTest(unittest2.TestCase):

    def setUp(self):
        self.flow = OAuth2WebServerFlow(
            client_id='client_id+1',
            client_secret='secret+1',
            scope='foo',
            redirect_uri=OOB_CALLBACK_URN,
            user_agent='unittest-sample/1.0',
            revoke_uri='dummy_revoke_uri',
        )

    def test_construct_authorize_url(self):
        authorize_url = self.flow.step1_get_authorize_url(state='state+1')

        parsed = urllib.parse.urlparse(authorize_url)
        q = urllib.parse.parse_qs(parsed[4])
        self.assertEqual('client_id+1', q['client_id'][0])
        self.assertEqual('code', q['response_type'][0])
        self.assertEqual('foo', q['scope'][0])
        self.assertEqual(OOB_CALLBACK_URN, q['redirect_uri'][0])
        self.assertEqual('offline', q['access_type'][0])
        self.assertEqual('state+1', q['state'][0])

    def test_override_flow_via_kwargs(self):
        """Passing kwargs to override defaults."""
        flow = OAuth2WebServerFlow(
            client_id='client_id+1',
            client_secret='secret+1',
            scope='foo',
            redirect_uri=OOB_CALLBACK_URN,
            user_agent='unittest-sample/1.0',
            access_type='online',
            response_type='token'
        )
        authorize_url = flow.step1_get_authorize_url()

        parsed = urllib.parse.urlparse(authorize_url)
        q = urllib.parse.parse_qs(parsed[4])
        self.assertEqual('client_id+1', q['client_id'][0])
        self.assertEqual('token', q['response_type'][0])
        self.assertEqual('foo', q['scope'][0])
        self.assertEqual(OOB_CALLBACK_URN, q['redirect_uri'][0])
        self.assertEqual('online', q['access_type'][0])

    def test_scope_is_required(self):
        self.assertRaises(TypeError, OAuth2WebServerFlow, 'client_id+1')

    def test_exchange_failure(self):
        http = HttpMockSequence([
            ({'status': '400'}, b'{"error":"invalid_request"}'),
        ])

        try:
            credentials = self.flow.step2_exchange('some random code',
                                                   http=http)
            self.fail('should raise exception if exchange doesn\'t get 200')
        except FlowExchangeError:
            pass

    def test_urlencoded_exchange_failure(self):
        http = HttpMockSequence([
            ({'status': '400'}, b'error=invalid_request'),
        ])

        try:
            credentials = self.flow.step2_exchange('some random code',
                                                   http=http)
            self.fail('should raise exception if exchange doesn\'t get 200')
        except FlowExchangeError as e:
            self.assertEqual('invalid_request', str(e))

    def test_exchange_failure_with_json_error(self):
        # Some providers have 'error' attribute as a JSON object
        # in place of regular string.
        # This test makes sure no strange object-to-string coversion
        # exceptions are being raised instead of FlowExchangeError.
        payload = (b'{'
                   b'  "error": {'
                   b'    "message": "Error validating verification code.",'
                   b'    "type": "OAuthException"'
                   b'  }'
                   b'}')
        http = HttpMockSequence([({'status': '400'}, payload)])

        try:
            credentials = self.flow.step2_exchange('some random code',
                                                   http=http)
            self.fail('should raise exception if exchange doesn\'t get 200')
        except FlowExchangeError as e:
            pass

    def test_exchange_success(self):
        payload = (b'{'
                   b'  "access_token":"SlAV32hkKG",'
                   b'  "expires_in":3600,'
                   b'  "refresh_token":"8xLOxBtZp8"'
                   b'}')
        http = HttpMockSequence([({'status': '200'}, payload)])
        credentials = self.flow.step2_exchange('some random code', http=http)
        self.assertEqual('SlAV32hkKG', credentials.access_token)
        self.assertNotEqual(None, credentials.token_expiry)
        self.assertEqual('8xLOxBtZp8', credentials.refresh_token)
        self.assertEqual('dummy_revoke_uri', credentials.revoke_uri)
        self.assertEqual(set(['foo']), credentials.scopes)

    def test_exchange_dictlike(self):
        class FakeDict(object):
            def __init__(self, d):
                self.d = d

            def __getitem__(self, name):
                return self.d[name]

            def __contains__(self, name):
                return name in self.d

        code = 'some random code'
        not_a_dict = FakeDict({'code': code})
        payload = (b'{'
                   b'  "access_token":"SlAV32hkKG",'
                   b'  "expires_in":3600,'
                   b'  "refresh_token":"8xLOxBtZp8"'
                   b'}')
        http = HttpMockSequence([({'status': '200'}, payload)])

        credentials = self.flow.step2_exchange(not_a_dict, http=http)
        self.assertEqual('SlAV32hkKG', credentials.access_token)
        self.assertNotEqual(None, credentials.token_expiry)
        self.assertEqual('8xLOxBtZp8', credentials.refresh_token)
        self.assertEqual('dummy_revoke_uri', credentials.revoke_uri)
        self.assertEqual(set(['foo']), credentials.scopes)
        request_code = urllib.parse.parse_qs(
            http.requests[0]['body'])['code'][0]
        self.assertEqual(code, request_code)

    def test_exchange_using_authorization_header(self):
        auth_header = 'Basic Y2xpZW50X2lkKzE6c2VjcmV0KzE=',
        flow = OAuth2WebServerFlow(
            client_id='client_id+1',
            authorization_header=auth_header,
            scope='foo',
            redirect_uri=OOB_CALLBACK_URN,
            user_agent='unittest-sample/1.0',
            revoke_uri='dummy_revoke_uri',
        )
        http = HttpMockSequence([
            ({'status': '200'}, b'access_token=SlAV32hkKG'),
        ])

        credentials = flow.step2_exchange('some random code', http=http)
        self.assertEqual('SlAV32hkKG', credentials.access_token)

        test_request = http.requests[0]
        # Did we pass the Authorization header?
        self.assertEqual(test_request['headers']['Authorization'], auth_header)
        # Did we omit client_secret from POST body?
        self.assertTrue('client_secret' not in test_request['body'])

    def test_urlencoded_exchange_success(self):
        http = HttpMockSequence([
            ({'status': '200'}, b'access_token=SlAV32hkKG&expires_in=3600'),
        ])

        credentials = self.flow.step2_exchange('some random code', http=http)
        self.assertEqual('SlAV32hkKG', credentials.access_token)
        self.assertNotEqual(None, credentials.token_expiry)

    def test_urlencoded_expires_param(self):
        http = HttpMockSequence([
            # Note the 'expires=3600' where you'd normally
            # have if named 'expires_in'
            ({'status': '200'}, b'access_token=SlAV32hkKG&expires=3600'),
        ])

        credentials = self.flow.step2_exchange('some random code', http=http)
        self.assertNotEqual(None, credentials.token_expiry)

    def test_exchange_no_expires_in(self):
        payload = (b'{'
                   b'  "access_token":"SlAV32hkKG",'
                   b'  "refresh_token":"8xLOxBtZp8"'
                   b'}')
        http = HttpMockSequence([({'status': '200'}, payload)])

        credentials = self.flow.step2_exchange('some random code', http=http)
        self.assertEqual(None, credentials.token_expiry)

    def test_urlencoded_exchange_no_expires_in(self):
        http = HttpMockSequence([
            # This might be redundant but just to make sure
            # urlencoded access_token gets parsed correctly
            ({'status': '200'}, b'access_token=SlAV32hkKG'),
        ])

        credentials = self.flow.step2_exchange('some random code', http=http)
        self.assertEqual(None, credentials.token_expiry)

    def test_exchange_fails_if_no_code(self):
        payload = (b'{'
                   b'  "access_token":"SlAV32hkKG",'
                   b'  "refresh_token":"8xLOxBtZp8"'
                   b'}')
        http = HttpMockSequence([({'status': '200'}, payload)])

        code = {'error': 'thou shall not pass'}
        try:
            credentials = self.flow.step2_exchange(code, http=http)
            self.fail('should raise exception if no code in dictionary.')
        except FlowExchangeError as e:
            self.assertTrue('shall not pass' in str(e))

    def test_exchange_id_token_fail(self):
        payload = (b'{'
                   b'  "access_token":"SlAV32hkKG",'
                   b'  "refresh_token":"8xLOxBtZp8",'
                   b'  "id_token": "stuff.payload"'
                   b'}')
        http = HttpMockSequence([({'status': '200'}, payload)])

        self.assertRaises(VerifyJwtTokenError, self.flow.step2_exchange,
                          'some random code', http=http)

    def test_exchange_id_token(self):
        body = {'foo': 'bar'}
        body_json = json.dumps(body).encode('ascii')
        payload = base64.urlsafe_b64encode(body_json).strip(b'=')
        jwt = (base64.urlsafe_b64encode(b'stuff') + b'.' + payload + b'.' +
               base64.urlsafe_b64encode(b'signature'))

        payload = (b'{'
                   b'  "access_token":"SlAV32hkKG",'
                   b'  "refresh_token":"8xLOxBtZp8",'
                   b'  "id_token": "' + jwt + b'"'
                   b'}')
        http = HttpMockSequence([({'status': '200'}, payload)])
        credentials = self.flow.step2_exchange('some random code', http=http)
        self.assertEqual(credentials.id_token, body)


class FlowFromCachedClientsecrets(unittest2.TestCase):

    def test_flow_from_clientsecrets_cached(self):
        cache_mock = CacheMock()
        load_and_cache('client_secrets.json', 'some_secrets', cache_mock)

        flow = flow_from_clientsecrets(
            'some_secrets', '', redirect_uri='oob', cache=cache_mock)
        self.assertEqual('foo_client_secret', flow.client_secret)


class CredentialsFromCodeTests(unittest2.TestCase):

    def setUp(self):
        self.client_id = 'client_id_abc'
        self.client_secret = 'secret_use_code'
        self.scope = 'foo'
        self.code = '12345abcde'
        self.redirect_uri = 'postmessage'

    def test_exchange_code_for_token(self):
        token = 'asdfghjkl'
        payload = json.dumps({'access_token': token, 'expires_in': 3600})
        http = HttpMockSequence([
            ({'status': '200'}, payload.encode('utf-8')),
        ])
        credentials = credentials_from_code(self.client_id, self.client_secret,
                                            self.scope, self.code,
                                            redirect_uri=self.redirect_uri,
                                            http=http)
        self.assertEqual(credentials.access_token, token)
        self.assertNotEqual(None, credentials.token_expiry)
        self.assertEqual(set(['foo']), credentials.scopes)

    def test_exchange_code_for_token_fail(self):
        http = HttpMockSequence([
            ({'status': '400'}, b'{"error":"invalid_request"}'),
        ])

        try:
            credentials = credentials_from_code(self.client_id,
                                                self.client_secret,
                                                self.scope, self.code,
                                                redirect_uri=self.redirect_uri,
                                                http=http)
            self.fail('should raise exception if exchange doesn\'t get 200')
        except FlowExchangeError:
            pass

    def test_exchange_code_and_file_for_token(self):
        payload = (b'{'
                   b'  "access_token":"asdfghjkl",'
                   b'  "expires_in":3600'
                   b'}')
        http = HttpMockSequence([({'status': '200'}, payload)])
        credentials = credentials_from_clientsecrets_and_code(
            datafile('client_secrets.json'), self.scope,
            self.code, http=http)
        self.assertEqual(credentials.access_token, 'asdfghjkl')
        self.assertNotEqual(None, credentials.token_expiry)
        self.assertEqual(set(['foo']), credentials.scopes)

    def test_exchange_code_and_cached_file_for_token(self):
        http = HttpMockSequence([
            ({'status': '200'}, b'{ "access_token":"asdfghjkl"}'),
        ])
        cache_mock = CacheMock()
        load_and_cache('client_secrets.json', 'some_secrets', cache_mock)

        credentials = credentials_from_clientsecrets_and_code(
            'some_secrets', self.scope,
            self.code, http=http, cache=cache_mock)
        self.assertEqual(credentials.access_token, 'asdfghjkl')
        self.assertEqual(set(['foo']), credentials.scopes)

    def test_exchange_code_and_file_for_token_fail(self):
        http = HttpMockSequence([
            ({'status': '400'}, b'{"error":"invalid_request"}'),
        ])

        try:
            credentials = credentials_from_clientsecrets_and_code(
                datafile('client_secrets.json'), self.scope,
                self.code, http=http)
            self.fail('should raise exception if exchange doesn\'t get 200')
        except FlowExchangeError:
            pass


class MemoryCacheTests(unittest2.TestCase):

    def test_get_set_delete(self):
        m = MemoryCache()
        self.assertEqual(None, m.get('foo'))
        self.assertEqual(None, m.delete('foo'))
        m.set('foo', 'bar')
        self.assertEqual('bar', m.get('foo'))
        m.delete('foo')
        self.assertEqual(None, m.get('foo'))


class Test__save_private_file(unittest2.TestCase):

    def _save_helper(self, filename):
        contents = []
        contents_str = '[]'
        client._save_private_file(filename, contents)
        with open(filename, 'r') as f:
            stored_contents = f.read()
        self.assertEqual(stored_contents, contents_str)

        stat_mode = os.stat(filename).st_mode
        # Octal 777, only last 3 positions matter for permissions mask.
        stat_mode &= 0o777
        self.assertEqual(stat_mode, 0o600)

    def test_new(self):
        import tempfile
        filename = tempfile.mktemp()
        self.assertFalse(os.path.exists(filename))
        self._save_helper(filename)

    def test_existing(self):
        import tempfile
        filename = tempfile.mktemp()
        with open(filename, 'w') as f:
            f.write('a bunch of nonsense longer than []')
        self.assertTrue(os.path.exists(filename))
        self._save_helper(filename)


if __name__ == '__main__':  # pragma: NO COVER
    unittest2.main()
