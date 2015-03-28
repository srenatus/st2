# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import uuid
from subprocess import list2cmdline

from eventlet.green import subprocess

from st2common import log as logging
from st2common.util.green_shell import run_command
from st2common.constants.action import LIVEACTION_STATUS_SUCCEEDED
from st2common.constants.action import LIVEACTION_STATUS_FAILED
from st2common.constants.runners import PYTHON_RUNNER_DEFAULT_ACTION_TIMEOUT
from st2actions.runners.windows_runner import BaseWindowsRunner
from st2actions.runners.windows_runner import WINEXE_EXISTS
from st2actions.runners.windows_runner import SMBCLIENT_EXISTS

__all__ = [
    'get_runner',

    'WindowsScriptRunner'
]

LOG = logging.getLogger(__name__)

PATH_SEPARATOR = '\\'

# constants to lookup in runner_parameters
RUNNER_HOST = 'host'
RUNNER_USERNAME = 'username'
RUNNER_PASSWORD = 'password'
RUNNER_COMMAND = 'cmd'
RUNNER_TIMEOUT = 'timeout'
RUNNER_SHARE_NAME = 'share'

UPLOAD_FILE_TIMEOUT = 30
CREATE_DIRECTORY_TIMEOUT = 10
DELETE_FILE_TIMEOUT = 10
DELETE_DIRECTORY_TIMEOUT = 10


def quote(value):
    # Note: pipes.quote only work on Linux
    result = list2cmdline([value])
    return result


def get_runner():
    return WindowsScriptRunner(str(uuid.uuid4()))


class WindowsScriptRunner(BaseWindowsRunner):
    """
    Runner which executes power shell scripts on a remote Windows machine.
    """

    def __init__(self, runner_id, timeout=PYTHON_RUNNER_DEFAULT_ACTION_TIMEOUT):
        """
        :param timeout: Action execution timeout in seconds.
        :type timeout: ``int``
        """
        super(WindowsScriptRunner, self).__init__(runner_id=runner_id)
        self._timeout = timeout

    def pre_run(self):
        # TODO :This is awful, but the way "runner_parameters" and other variables get
        # assigned on the runner instance is even worse. Those arguments should
        # be passed to the constructor.
        self._host = self.runner_parameters.get(RUNNER_HOST, None)
        self._username = self.runner_parameters.get(RUNNER_USERNAME, None)
        self._password = self.runner_parameters.get(RUNNER_PASSWORD, None)
        self._command = self.runner_parameters.get(RUNNER_COMMAND, None)
        self._timeout = self.runner_parameters.get(RUNNER_TIMEOUT, self._timeout)

        self._share = self.runner_parameters.get(RUNNER_SHARE_NAME, 'C$')

    def run(self, action_parameters):
        if not WINEXE_EXISTS:
            msg = ('Could not find "winexe" binary. Make sure it\'s installed and available'
                   'in $PATH')
            raise Exception(msg)

        if not SMBCLIENT_EXISTS:
            msg = ('Could not find "smbclient" binary. Make sure it\'s installed and available'
                   'in $PATH')
            raise Exception(msg)

        # 1. Upload script file to a temporary location
        local_path = self.entry_point
        script_path, temporary_directory_path = self._upload_file(local_path=local_path)

        # 2. Execute the script
        exit_code, stdout, stderr, timed_out = self._run_script(script_path=script_path)

        # 3. Delete temporary directory
        self._delete_directory(directory_path=temporary_directory_path)

        if timed_out:
            error = 'Action failed to complete in %s seconds' % (self._timeout)
        else:
            error = None

        result = stdout

        output = {
            'stdout': stdout,
            'stderr': stderr,
            'exit_code': exit_code,
            'result': result
        }

        if error:
            output['error'] = error

        status = LIVEACTION_STATUS_SUCCEEDED if exit_code == 0 else LIVEACTION_STATUS_FAILED
        LOG.debug('Action output : %s. exit_code : %s. status : %s', str(output), exit_code, status)
        return (status, output, None)

    def _run_script(self, script_path):
        """
        :param script_path: Full path to the script on the remote server.
        :type script_path: ``str``
        """
        command = 'powershell.exe %s' % (quote(script_path))
        args = self._get_winexe_command_args(host=self._host, username=self._username,
                                             password=self._password,
                                             command=command)

        LOG.debug('Running script "%s"' % (script_path))

        exit_code, stdout, stderr, timed_out = run_command(cmd=args, stdout=subprocess.PIPE,
                                                           stderr=subprocess.PIPE, shell=False,
                                                           timeout=self._timeout)

        extra = {'exit_code': exit_code, 'stdout': stdout, 'stderr': stderr}
        LOG.debug('Command returned', extra=extra)

        return exit_code, stdout, stderr, timed_out

    def _upload_file(self, local_path):
        """
        Upload provided file to the remote server in a temporary directory.

        :param local_path: Local path to the file to upload.
        :type local_path: ``str``
        """
        file_name = os.path.basename(local_path)

        temporary_directory_name = str(uuid.uuid4())
        command = 'mkdir %s' % (quote(temporary_directory_name))

        # 1. Create a temporary dir for out scripts (ignore errors if it already exists)
        # Note: We don't necessary have access to $TEMP so we create a temporary directory for our
        # us in the root of the share we are using and have access to
        args = self._get_smbclient_command_args(host=self._host, username=self._username,
                                                password=self._password, command=command,
                                                share=self._share)

        LOG.debug('Creating temp directory "%s"' % (temporary_directory_name))

        exit_code, stdout, stderr, timed_out = run_command(cmd=args, stdout=subprocess.PIPE,
                                                           stderr=subprocess.PIPE, shell=False,
                                                           timeout=CREATE_DIRECTORY_TIMEOUT)

        extra = {'exit_code': exit_code, 'stdout': stdout, 'stderr': stderr}
        LOG.debug('Directory created', extra=extra)

        # 2. Upload file to temporary directory
        remote_path = PATH_SEPARATOR.join([temporary_directory_name, file_name])

        values = {'local_path': quote(local_path), 'remote_path': quote(remote_path)}
        command = 'put %(local_path)s %(remote_path)s' % values
        args = self._get_smbclient_command_args(host=self._host, username=self._username,
                                                password=self._password, command=command,
                                                share=self._share)

        extra = {'local_path': local_path, 'remote_path': remote_path}
        LOG.debug('Uploading file to "%s"' % (remote_path))

        exit_code, stdout, stderr, timed_out = run_command(cmd=args, stdout=subprocess.PIPE,
                                                           stderr=subprocess.PIPE, shell=False,
                                                           timeout=UPLOAD_FILE_TIMEOUT)

        extra = {'exit_code': exit_code, 'stdout': stdout, 'stderr': stderr}
        LOG.debug('File uploaded to "%s"' % (remote_path), extra=extra)

        # TODO: Get full path, use share name, etc.
        full_remote_file_path = 'C:\\\\' + remote_path
        full_temporary_directory_path = 'C:\\\\' + temporary_directory_name

        return full_remote_file_path, full_temporary_directory_path

    def _delete_file(self, file_path):
        command = 'rm %(file_path)s' % {'file_path': quote(file_path)}
        args = self._get_smbclient_command_args(host=self._host, username=self._username,
                                                password=self._password, command=command,
                                                share=self._share)

        exit_code, _, _, _ = run_command(cmd=args, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, shell=False,
                                         timeout=DELETE_FILE_TIMEOUT)

        return exit_code == 0

    def _delete_directory(self, directory_path):
        command = 'rmdir %(directory_path)s' % {'directory_path': quote(directory_path)}
        args = self._get_smbclient_command_args(host=self._host, username=self._username,
                                                password=self._password, command=command,
                                                share=self._share)

        LOG.debug('Removing directory "%s"' % (directory_path))
        exit_code, _, _, _ = run_command(cmd=args, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, shell=False,
                                         timeout=DELETE_DIRECTORY_TIMEOUT)

        return exit_code == 0