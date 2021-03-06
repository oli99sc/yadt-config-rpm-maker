# coding=utf-8
#
#   yadt-config-rpm-maker
#   Copyright (C) 2011-2013 Immobilien Scout GmbH
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.


from os import environ
from os.path import join
from subprocess import PIPE, Popen

from integration_test_support import IntegrationTest

from config_rpm_maker.config import ENVIRONMENT_VARIABLE_KEY_CONFIGURATION_FILE


class CommandLineInterfaceIntegrationTest(IntegrationTest):

    def setUp(self):
        IntegrationTest.setUp(self)

        self.environment = environ.copy()
        self.environment['PYTHONPATH'] = 'src'

    def test_should_print_version_and_return_with_exit_code_zero(self):

        self.config_rpm_maker('--version')

        self.assert_stdout_equal('yadt-config-rpm-maker 2.0\n')
        self.assert_exit_code(0)

    def test_should_return_with_exit_code_zero_when_displaying_help_screen(self):

        self.config_rpm_maker('--help')

        self.assert_exit_code(0)

    def test_should_return_with_exit_code_foure_strange_repository_url_is_given(self):

        self.config_rpm_maker(self.repository_directory, '1')

        self.assert_exit_code(0)
        self.assert_stderr_ends_with('[ INFO] Success.')

    def test_should_return_with_exit_code_one_when_not_enough_arguments_are_given(self):

        self.config_rpm_maker(self.repository_directory)

        self.assert_exit_code(1)

    def test_should_return_with_exit_code_two_when_strange_arguments_are_given(self):

        self.config_rpm_maker('--foo-bar')

        self.assert_exit_code(2)
        self.assert_stderr_ends_with('error: no such option: --foo-bar')

    def test_should_return_with_exit_code_three_when_a_bad_configuration_parameter_is_given(self):

        self.use_configuration_file('log_level: 1')

        self.config_rpm_maker(self.repository_directory, '1')

        self.assert_exit_code(3)
        self.assert_stderr_ends_with('[ERROR] Configuration error!')

    def test_should_return_with_exit_code_four_when_svn_client_throws_error_because_host_in_strange_repository_url_is_not_reachable(self):

        self.config_rpm_maker('file://spam/eggs', '1')

        self.assert_exit_code(4)
        self.assert_stderr_ends_with('[ERROR] An exception occurred!')

    def test_should_return_with_exit_code_six_when_invalid_repository_url_is_given(self):

        self.config_rpm_maker('rss://spam/eggs', '1')

        self.assert_exit_code(6)
        self.assert_stderr_ends_with('[ERROR] Given repository url "rss://spam/eggs" is invalid.')

    def config_rpm_maker(self, *args):
        process = Popen(['python', 'src/config_rpm_maker'] + list(args),
                        stdout=PIPE,
                        stderr=PIPE,
                        env=self.environment)

        self.stdout, self.stderr = process.communicate()

        self.exit_code = process.returncode

    def use_configuration_file(self, content):

        temporary_configuration_file_path = join(self.temporary_directory, 'configuration.yaml')

        with open(temporary_configuration_file_path, "w") as configuration_file:
            configuration_file.write(content + '\n')

        self.environment[ENVIRONMENT_VARIABLE_KEY_CONFIGURATION_FILE] = temporary_configuration_file_path

    def assert_stdout_equal(self, expected_output):
        self.assertEqual(self.stdout, expected_output)

    def assert_stderr_ends_with(self, expected_postfix):
        error_message = 'stderr was "%s" and did not end with "%s"' % (self.stderr, expected_postfix)
        self.assertTrue(self.stderr.endswith(expected_postfix + "\n"), error_message)

    def assert_stdout_ends_with(self, expected_postfix):
        error_message = 'stdout was "%s" and did not end with "%s"' % (self.stdout, expected_postfix)
        self.assertTrue(self.stdout.endswith(expected_postfix + "\n"), error_message)

    def assert_exit_code(self, expected_exit_code):
        self.assertEqual(self.exit_code, expected_exit_code)
