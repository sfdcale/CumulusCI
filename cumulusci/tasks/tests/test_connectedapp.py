""" Tests for the connectedapp tasks """

import logging
import mock
import os
import pytest
import re
import shutil
import tempfile
import unittest

try:
    from json.decoder import JSONDecodeError
except ImportError:
    JSONDecodeError = ValueError
from mock import MagicMock
from mock import patch

import cumulusci.core.tasks
from cumulusci.core.config import (
    BaseGlobalConfig,
    BaseProjectConfig,
    TaskConfig,
    OrgConfig,
    ServiceConfig,
)
from cumulusci.core.exceptions import ServiceNotConfigured, TaskOptionsError
from cumulusci.core.keychain import BaseProjectKeychain
from cumulusci.core.tests.utils import MockLoggingHandler

from cumulusci.tasks.salesforce.tests.util import create_task
from cumulusci.tasks.connectedapp import CreateConnectedApp


class TestCreateConnectedApp(unittest.TestCase):
    """ Tests for the CreateConnectedApp task """

    @classmethod
    def setUpClass(cls):
        super(TestCreateConnectedApp, cls).setUpClass()
        logger = logging.getLogger(cumulusci.core.tasks.__name__)
        logger.setLevel(logging.DEBUG)
        cls._task_log_handler = MockLoggingHandler(logging.DEBUG)
        logger.addHandler(cls._task_log_handler)

    def setUp(self):
        self.global_config = BaseGlobalConfig()
        self.project_config = BaseProjectConfig(
            self.global_config, config={"noyaml": True}
        )

        keychain = BaseProjectKeychain(self.project_config, "")
        self.project_config.set_keychain(keychain)

        self._task_log_handler.reset()
        self.task_log = self._task_log_handler.messages
        self.base_command = "sfdx force:mdapi:deploy --wait 5"
        self.label = "Test_Label"
        self.username = "TestUser@Name"
        self.email = "TestUser@Email"
        self.task_config = TaskConfig(
            {
                "options": {
                    "label": self.label,
                    "username": self.username,
                    "email": self.email,
                }
            }
        )

    def test_init_options(self):
        """ Passed options are correctly initialized """
        self.task_config.config["options"]["connect"] = True
        self.task_config.config["options"]["overwrite"] = True
        task = CreateConnectedApp(self.project_config, self.task_config)
        self.assertEqual(
            task.options["command"], "{} -u {}".format(self.base_command, self.username)
        )
        self.assertEqual(task.options["label"], self.label)
        self.assertEqual(task.options["username"], self.username)
        self.assertEqual(task.options["email"], self.email)
        self.assertIs(task.options["connect"], True)
        self.assertIs(task.options["overwrite"], True)

    def test_init_options_invalid_label(self):
        """ Non-alphanumeric + _ label raises TaskOptionsError """
        self.task_config.config["options"]["label"] = "Test Label"
        with pytest.raises(TaskOptionsError, match="^label value must contain only"):
            task = CreateConnectedApp(self.project_config, self.task_config)

    def test_init_options_email_default(self):
        """ email option defaults to email from github service """
        del self.task_config.config["options"]["email"]
        self.project_config.config["services"] = {
            "github": {"attributes": {"email": {}}}
        }
        self.project_config.keychain.set_service(
            "github", ServiceConfig({"email": self.email}), True
        )
        task = CreateConnectedApp(self.project_config, self.task_config)
        self.assertEqual(task.options["email"], self.email)

    def test_init_options_email_not_found(self):
        """ TaskOptionsError is raised if no email provided and no github service exists """
        del self.task_config.config["options"]["email"]
        self.project_config.config["services"] = {"github": {"attributes": {}}}
        with pytest.raises(TaskOptionsError, match="github"):
            task = CreateConnectedApp(self.project_config, self.task_config)

    @mock.patch("cumulusci.tasks.connectedapp.CreateConnectedApp._set_default_username")
    def test_init_options_default_username(self, set_mock):
        """ Not passing username calls _get_default_username """
        del self.task_config.config["options"]["username"]
        try:
            task = CreateConnectedApp(self.project_config, self.task_config)
        except:
            pass
        set_mock.assert_called_once()

    @mock.patch("cumulusci.tasks.connectedapp.CreateConnectedApp._run_command")
    def test_set_default_username(self, run_command_mock):
        """ _set_default_username calls _run_command """
        task = CreateConnectedApp(self.project_config, self.task_config)
        task._set_default_username()
        run_command_mock.assert_called_once()
        self.assertEqual(
            self.task_log["info"], ["Getting username for the default devhub from sfdx"]
        )

    def test_process_json_output(self):
        """ _process_json_output returns valid json """
        task = CreateConnectedApp(self.project_config, self.task_config)
        output = task._process_json_output('{"foo":"bar"}')
        self.assertEqual(output, {"foo": "bar"})

    def test_process_json_output_invalid(self):
        """ _process_json_output with invalid input logs output and raises JSONDecodeError """
        task = CreateConnectedApp(self.project_config, self.task_config)
        with pytest.raises(JSONDecodeError):
            output = task._process_json_output("invalid")
        self.assertEqual(
            self.task_log["error"], ["Failed to parse json from line: invalid"]
        )

    def test_process_devhub_output(self):
        """ username is parsed from json response """
        del self.task_config.config["options"]["username"]
        task = CreateConnectedApp(self.project_config, self.task_config)
        task._process_devhub_output('{"result":[{"value":"' + self.username + '"}]}')
        self.assertEqual(task.options.get("username"), self.username)

    def test_process_devhub_output_not_configured(self):
        """ TaskOptionsError is raised if no username provided and no default found """
        del self.task_config.config["options"]["username"]
        task = CreateConnectedApp(self.project_config, self.task_config)
        with pytest.raises(TaskOptionsError, match="^No sfdx config found"):
            task._process_devhub_output('{"result":[{}]}')

    def test_generate_id_and_secret(self):
        """ client_id and client_secret are generated correctly """
        task = CreateConnectedApp(self.project_config, self.task_config)
        task._generate_id_and_secret()
        self.assertEqual(len(task.client_id), 85)
        self.assertEqual(len(task.client_secret), 32)
        self.assertNotEqual(re.match(r"^\w+$", task.client_id), None)
        self.assertNotEqual(re.match(r"^\w+$", task.client_secret), None)

    def test_build_package(self):
        """ tempdir is populated with connected app and package.xml """
        task = CreateConnectedApp(self.project_config, self.task_config)
        task.tempdir = tempfile.mkdtemp()
        connected_app_path = os.path.join(
            task.tempdir, "connectedApps", "{}.connectedApp".format(self.label)
        )
        try:
            task._build_package()
        finally:
            self.assertTrue(os.path.isdir(os.path.join(task.tempdir, "connectedApps")))
            self.assertTrue(os.path.isfile(os.path.join(task.tempdir, "package.xml")))
            self.assertTrue(os.path.isfile(connected_app_path))
            with open(connected_app_path, "r") as f:
                connected_app = f.read()
                self.assertTrue("<label>{}<".format(self.label) in connected_app)
                self.assertTrue("<contactEmail>{}<".format(self.email) in connected_app)
                self.assertTrue(
                    "<consumerKey>{}<".format(task.client_id) in connected_app
                )
                self.assertTrue(
                    "<consumerSecret>{}<".format(task.client_secret) in connected_app
                )
            shutil.rmtree(task.tempdir)

    def test_connect_service(self):
        """ connected app gets added to the keychain connected_app service """
        self.project_config.config["services"] = {
            "connected_app": {
                "attributes": {"callback_url": {}, "client_id": {}, "client_secret": {}}
            }
        }
        task = CreateConnectedApp(self.project_config, self.task_config)
        task._connect_service()
        connected_app = self.project_config.keychain.get_service("connected_app")
        self.assertEqual(connected_app.callback_url, "http://localhost:8080/callback")
        self.assertEqual(connected_app.client_id, task.client_id)
        self.assertEqual(connected_app.client_secret, task.client_secret)

    def test_connect_service_overwrite_false(self):
        """ attempting to overwrite connected_app service without overwrite = False fails """
        self.project_config.config["services"] = {
            "connected_app": {
                "attributes": {"callback_url": {}, "client_id": {}, "client_secret": {}}
            }
        }
        self.project_config.keychain.set_service(
            "connected_app",
            ServiceConfig(
                {
                    "callback_url": "http://callback",
                    "client_id": "ClientId",
                    "client_secret": "ClientSecret",
                }
            ),
            True,
        )
        task = CreateConnectedApp(self.project_config, self.task_config)
        with pytest.raises(
            TaskOptionsError, match="^The CumulusCI keychain already contains"
        ):
            task._connect_service()

    @mock.patch("cumulusci.tasks.sfdx.SFDXBaseTask._run_task")
    def test_run_task(self, run_task_mock):
        """ _run_task formats command, calls SFDXBaseTask._run_task, and does not connect service by default """
        self.project_config.config["services"] = {
            "connected_app": {
                "attributes": {"callback_url": {}, "client_id": {}, "client_secret": {}}
            }
        }
        task = CreateConnectedApp(self.project_config, self.task_config)
        task._run_task()
        run_task_mock.assert_called_once()
        self.assertFalse(os.path.isdir(task.tempdir))
        self.assertEqual(
            task.options["command"],
            self.base_command + " -u {} -d {}".format(self.username, task.tempdir),
        )
        with pytest.raises(ServiceNotConfigured):
            self.project_config.keychain.get_service("connected_app")

    @mock.patch("cumulusci.tasks.sfdx.SFDXBaseTask._run_task")
    @mock.patch("cumulusci.tasks.connectedapp.CreateConnectedApp._connect_service")
    def test_run_task_connect(self, run_task_mock, connect_service_mock):
        """ _run_task calls _connect_service if connect option is True """
        self.task_config.config["options"]["connect"] = True
        task = CreateConnectedApp(self.project_config, self.task_config)
        task._run_task()
        run_task_mock.assert_called_once()
        connect_service_mock.assert_called_once()
