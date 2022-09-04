# -*- encoding: utf8 -*-
#
# This module implements the recipe to launch a Jupyter Notebook from IDA and
# connect to it.
# See README.adoc for more details.
#
# Copyright (c) 2022 ESET
# Author: Marc-Etienne M.Léveillé <leveille@eset.com>
# See LICENSE file for redistribution.

import sys
import os
import subprocess
import time
import json
import threading
import webbrowser

import idaapi
import nbformat
from jupyter_client.kernelspec import find_kernel_specs
from jupyter_client import find_connection_file

def get_python_executable_path():
    # We can't rely on sys.executable because it's set to ida{q,t}{.exe,} in IDA
    if sys.platform == 'win32':
        return os.path.join(sys.prefix, 'Python.exe')
    else:
        python = os.path.join(sys.prefix, 'bin', 'python')
        if sys.version_info.major >= 3:
            python += str(sys.version_info.major)
        return python

class NotebookManager(object):

    def __init__(self, connection_file):
        self.connection_file = connection_file
        self.nb_proc = None
        self.nb_pipe_thread = None
        self.nb_pipe_buffer = []
        self.nb_pipe_lock = threading.Lock()

    @staticmethod
    def ensure_kernel_proxy_installed():
        try:
            import jupyter_kernel_proxy
        except ImportError:
            print("-> Installing jupyter-kernel-proxy...")
            return subprocess.Popen([
                get_python_executable_path(),
                "-m", "pip", "install", "jupyter-kernel-proxy"
            ]).wait() == 0
        else:
            return True

    @staticmethod
    def ensure_kernelspec_installed():
        if "proxy" not in find_kernel_specs():
            print("-> Installing jupyter-kernel-proxy kernelspec...")
            return subprocess.Popen([
                get_python_executable_path(),
                "-m", "jupyter_kernel_proxy", "install"
            ]).wait() == 0
        else:
            return True

    @staticmethod
    def ensure_notebook_installed():
        try:
            import notebook
        except ImportError:
            print("-> Installing jupyter-notebook...")
            return subprocess.Popen([
                get_python_executable_path(),
                "-m", "pip", "install", "notebook<7"
            ]).wait() == 0
        else:
            return True

    def _get_running_notebook_config(self):
        from notebook.notebookapp import list_running_servers
        idb_path = idaapi.get_path(idaapi.PATH_TYPE_IDB)
        is_idb_under_nb_dir = lambda c: idb_path.startswith(c.get("notebook_dir"))
        return next(filter(is_idb_under_nb_dir, list_running_servers()), None)

    def open_notebook(self, line):
        """
        Open a Jupyter Notebook in the same directory where the currently open
        .idb (or .i64) is located. The notebook file (.ipynb) will have the
        same name as the IDA database file.
        """
        idb_path = idaapi.get_path(idaapi.PATH_TYPE_IDB)
        if len(idb_path) == 0:
            raise Exception("No file currently open")

        if not self.ensure_notebook_installed() or \
           not self.ensure_kernel_proxy_installed() or \
           not self.ensure_kernelspec_installed():
            raise Exception("Could not find or install all requirements")

        nb_server_info = self._get_running_notebook_config()

        if nb_server_info is None:
            print("-> Starting notebook")
            self.nb_proc = subprocess.Popen([
                get_python_executable_path(),
                "-m", "jupyter", "notebook", "--no-browser", "-y"
            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try_count = 0
            while nb_server_info is None and try_count < 10:
                time.sleep(0.5)
                nb_server_info = self._get_running_notebook_config()
            if nb_server_info is None:
                self.nb_proc.terminate()
                raise Exception("Couldn't start Jupyter Notebook")
            else:
                self.nb_pipe_thread = threading.Thread(target=self._notebook_stdout_thread)
                self.nb_pipe_thread.start()

        ipynb_filename = os.path.basename(idb_path).rsplit(".", 1)[0] + ".ipynb"
        ipynb_path = os.path.join(os.path.dirname(idb_path), ipynb_filename)
        if not os.path.exists(ipynb_path):
            # Create the file, the notebook won't do it for us
            with open(ipynb_path, "w") as f:
                nb = nbformat.versions[nbformat.current_nbformat].new_notebook()
                json.dump(nb, f)
        relative_path = os.path.relpath(ipynb_path, nb_server_info.get("notebook_dir"))
        url = nb_server_info.get("url") + "notebooks/" + "/".join(relative_path.split(os.path.sep)) + '?kernel_type=proxy&token=' + nb_server_info.get("token")
        # Update access time of the file so it's picked up by the proxy.
        # jupyter-kernel-proxy will use the file with the most recent access
        # time (like `jupyter console --existing`)
        with open(find_connection_file(self.connection_file), "r"): pass
        webbrowser.open(url)
        print("Opening " + url)

    def _notebook_stdout_thread(self):
        while self.nb_proc.poll() is None:
            r = self.nb_proc.stdout.readline()
            with self.nb_pipe_lock:
                self.nb_pipe_buffer.append(r)

    def notebook_log(self, line):
        "Print output from Jupyter Notebook started by IPyIDA"
        if self.nb_proc:
            with self.nb_pipe_lock:
                for s in self.nb_pipe_buffer:
                    print(s)
                self.nb_pipe_buffer = []
        else:
            print("Notebook isn't running or managed by this IPyIDA instance")

    @property
    def magic_functions(self):
        return [self.open_notebook, self.notebook_log]

    def shutdown(self):
        if self.nb_proc:
            self.nb_proc.terminate()
        if self.nb_pipe_thread:
            self.nb_pipe_thread.join()