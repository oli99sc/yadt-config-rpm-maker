# global
from Queue import Queue
import os
import shutil
import subprocess
import tempfile
import logging
import traceback
import config
from threading import Thread

# our package
from config_rpm_maker.hostRpmBuilder import HostRpmBuilder
from config_rpm_maker.segment import OVERLAY_ORDER


class BuildHostThread(Thread):

    def __init__(self, revision, host_queue, svn_service_queue, rpm_queue, failed_host_queue, work_dir, name=None, error_logging_handler=None):
        super( BuildHostThread, self).__init__(name=name)
        self.revision = revision
        self.host_queue = host_queue
        self.svn_service_queue = svn_service_queue
        self.rpm_queue = rpm_queue
        self.work_dir = work_dir
        self.failed_host_queue = failed_host_queue
        self.error_logging_handler = error_logging_handler

    def run(self):
        while not self.host_queue.empty():
            host = self.host_queue.get()
            self.host_queue.task_done()
            try:
                rpms = HostRpmBuilder(hostname=host, revision=self.revision, work_dir=self.work_dir, svn_service_queue=self.svn_service_queue, error_logging_handler=self.error_logging_handler).build()
                for rpm in rpms:
                    self.rpm_queue.put(rpm)

            except Exception:
                self.failed_host_queue.put((host, traceback.format_exc()))


class ConfigRpmMaker(object):

    ERROR_MSG = '\n\n\nYour commit as been accepted by the SVN server, but due to the\n' + \
                'errors that it contains no RPMs have been created.\n\n%s' + \
                'Please fix the issues and trigger the RPM creation with a dummy commit.\n\n'

    def __init__(self, revision, svn_service):
        self.revision = revision
        self.svn_service = svn_service
        self.temp_dir = config.get('temp_dir')
        self._assure_temp_dir_if_set()
        self._create_logger()
        self.work_dir=None

    def build(self):
        self.logger.info("Starting with revision %s", self.revision)
        try:
            change_set = self.svn_service.get_change_set(self.revision)
            available_hosts = self.svn_service.get_hosts(self.revision)

            affected_hosts = self._get_affected_hosts(change_set, available_hosts)
            if not affected_hosts:
                self.logger.info("We have nothing to do. No host affected from change set: %s", str(change_set))
                return

            self._prepare_work_dir()
            rpms = self._build_hosts(affected_hosts)
            self._upload_rpms(rpms)
            self._move_configviewer_dirs_to_final_destination(affected_hosts)
        except Exception:
            try:
                self.logger.exception('Last error during build:')
                error_msg = self.ERROR_MSG % 'See %s/%s.txt for details.\n\n' % (config.get('error_log_url', ''), self.revision)
                self.logger.error(error_msg)
                self._move_error_log_for_public_access()
                raise Exception('%s\n\n%s' % (traceback.format_exc(), error_msg))
            finally:
                self._clean_up_work_dir()

        self._clean_up_work_dir()
        return rpms

    def _clean_up_work_dir(self):
        if self.work_dir and os.path.exists(self.work_dir) and not self._keep_work_dir():
            shutil.rmtree(self.work_dir)

        if os.path.exists(self.error_log_file):
            os.remove(self.error_log_file)

    def _keep_work_dir(self):
        return os.environ.has_key('KEEPWORKDIR') and os.environ['KEEPWORKDIR']

    def _move_error_log_for_public_access(self):
        error_log_dir = os.path.join(config.get('error_log_dir'))
        if error_log_dir:
            if not os.path.exists(error_log_dir):
                os.makedirs(error_log_dir)
            shutil.move(self.error_log_file, os.path.join(error_log_dir, self.revision + '.txt'))

    def _move_configviewer_dirs_to_final_destination(self, hosts):
        for host in hosts:
            temp_path = HostRpmBuilder.get_config_viewer_host_dir(host, True)
            dest_path = HostRpmBuilder.get_config_viewer_host_dir(host)
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)
            shutil.move(temp_path, dest_path)

    def _build_hosts(self, hosts):
        if not hosts:
            return

        host_queue = Queue()
        for host in hosts:
            host_queue.put(host)

        failed_host_queue = Queue()
        rpm_queue = Queue()
        svn_service_queue = Queue()
        svn_service_queue.put(self.svn_service)

        thread_count = self._get_thread_count(hosts)
        thread_pool = [BuildHostThread(
                            name='host_rpm_thread_%d' % i,
                            revision=self.revision,
                            svn_service_queue=svn_service_queue,
                            rpm_queue=rpm_queue,
                            failed_host_queue=failed_host_queue,
                            host_queue=host_queue,
                            work_dir=self.work_dir,
                            error_logging_handler=self.error_handler
                        ) for i in range(thread_count)]




        for thread in thread_pool:
            thread.start()

        for thread in thread_pool:
            thread.join()

        failed_hosts = dict(self._consume_queue(failed_host_queue))
        if failed_hosts:
            failed_hosts_str = ['\n%s:\n\n%s\n\n' % (key, value) for (key, value) in failed_hosts.iteritems()]
            raise Exception("For some the host we could not build the config rpm: %s" % '\n'.join(failed_hosts_str))

        return self._consume_queue(rpm_queue)

    def _upload_rpms(self, rpms):
        rpm_upload_cmd = config.get('rpm_upload_cmd')
        chunk_size = self._get_chunk_size(rpms)

        if rpm_upload_cmd:
            pos = 0
            while pos < len(rpms):
                rpm_chunk = rpms[pos:pos + chunk_size]
                cmd = '%s %s' % (rpm_upload_cmd, ' '.join(rpm_chunk))
                returncode = subprocess.call(cmd, shell=True)
                if returncode:
                    raise Exception('Could not upload rpms. Called %s . Returned: %d', (cmd, returncode))
                pos += chunk_size

    def _get_affected_hosts(self, change_set, available_host):
        result = set()
        for segment in OVERLAY_ORDER:
            for change_path in change_set:
                result |= set(self._find_matching_hosts(segment, change_path, available_host))

        return result

    def _find_matching_hosts(self, segment, svn_path, available_hosts):
        result = []
        for host in available_hosts:
            for path in segment.get_svn_paths(host):
                if svn_path.startswith(path):
                    result.append(host)
                    break

        return result

    def _get_thread_count(self, affected_hosts):
        thread_count = int(config.get('thread_count', 1))
        if thread_count < 0:
            raise Exception('Thread count is %s, but smaller than zero is not allowed.')

        # thread_count is zero means one thread for affected host
        if not thread_count or thread_count > len(affected_hosts):
            thread_count = len(affected_hosts)
        return thread_count

    def _consume_queue(self, queue):
        items = []
        while not queue.empty():
            item = queue.get()
            queue.task_done()
            items.append(item)

        return items

    def _create_logger(self):
        self.logger = logging.getLogger('Config-Rpm-Maker')
        self.error_log_file = tempfile.mktemp(suffix='.error.log', prefix='yadt-config-rpm-maker', dir=config.get('temp_dir'))
        self.error_handler = logging.FileHandler(self.error_log_file)
        self.error_handler.setFormatter(logging.Formatter(HostRpmBuilder.LOG_FORMAT, HostRpmBuilder.DATE_FORMAT))
        self.error_handler.setLevel(logging.ERROR)
        self.logger.addHandler(self.error_handler)

    def _assure_temp_dir_if_set(self):
        if self.temp_dir and not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

    def _prepare_work_dir(self):
        self.work_dir = tempfile.mkdtemp(prefix='yadt-config-rpm-maker.', suffix='.' + self.revision, dir=self.temp_dir)
        self.rpm_build_dir = os.path.join(self.work_dir, 'rpmbuild')
        for name in ['tmp','RPMS','RPMS/x86_64,RPMS/noarch','BUILD','SRPMS','SPECS','SOURCES']:
            path = os.path.join(self.rpm_build_dir, name)
            if not os.path.exists(path):
                os.makedirs(path)

    def _get_chunk_size(self, rpms):
        chunk_size = int(config.get('rpm_upload_chunk_size', 0))
        if chunk_size < 0:
            raise Exception("Config param 'rpm_upload_cmd_chunk_size' needs to be greater or equal 0")

        if not chunk_size:
            chunk_size = len(rpms)

        return chunk_size