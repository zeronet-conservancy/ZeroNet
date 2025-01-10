import os
import sys
import stat
import time
import logging
from util.compat import *
from pathlib import Path

from rich import print

startup_errors = []
def startupError(msg):
    startup_errors.append(msg)
    print("Startup error: %s" % msg)

# Third party modules
import gevent
import gevent.monkey
gevent.monkey.patch_all(thread=False, subprocess=False)

update_after_shutdown = False  # If set True then update and restart zeronet after main loop ended
restart_after_shutdown = False  # If set True then restart zeronet after main loop ended

from Config import config

def load_config():
    config.parse(silent=True)  # Plugins need to access the configuration
    if not config.arguments:
        # Config parse failed completely, show the help screen and exit
        config.parse()

def importBundle(bundle):
    from zipfile import ZipFile
    from Crypt.CryptBitcoin import isValidAddress
    import json

    sites_json_path = config.private_dir / 'sites.json'
    try:
        with open(sites_json_path) as f:
            sites = json.load(f)
    except Exception as err:
        sites = {}

    with ZipFile(bundle) as zf:
        all_files = zf.namelist()
        top_files = list(set(map(lambda f: f.split('/')[0], all_files)))
        if len(top_files) == 1 and not isValidAddress(top_files[0]):
            prefix = top_files[0]+'/'
        else:
            prefix = ''
        top_2 = list(set(filter(lambda f: len(f)>0,
                                map(lambda f: removeprefix(f, prefix).split('/')[0], all_files))))
        for d in top_2:
            if isValidAddress(d):
                print(f'Unpacking {d} into {config.data_dir}')
                for fname in filter(lambda f: f.startswith(prefix+d) and not f.endswith('/'), all_files):
                    tgt = removeprefix(fname, prefix)
                    print(f'-- {fname} --> {tgt}')
                    info = zf.getinfo(fname)
                    info.filename = tgt
                    zf.extract(info, path=config.data_dir)
                logging.info(f'add site {d}')
                sites[d] = {}
            else:
                print(f'Warning: unknown file in a bundle: {prefix+d}')
    with open(sites_json_path, 'w') as f:
        json.dump(sites, f)

def init_dirs():
    data_dir = Path(config.data_dir)
    private_dir = Path(config.private_dir)
    need_bootstrap = (config.bootstrap
                      and not config.offline
                      and (not data_dir.is_dir() or not (private_dir / 'sites.json').is_file()))

    # old_users_json = data_dir / 'users.json'
    # if old_users_json.is_file():
        # print('Migrating existing users.json file to private/')
    # old_sites_json = data_dir / 'sites.json'
    # if old_sites_json.is_file():
        # print('Migrating existing sites.json file to private/')

    if not data_dir.is_dir():
        data_dir.mkdir(parents=True, exist_ok=True)

    if need_bootstrap:
        import requests
        from io import BytesIO

        print(f'fetching {config.bootstrap_url}')
        response = requests.get(config.bootstrap_url)
        if response.status_code != 200:
            startupError(f"Cannot load bootstrap bundle (response status: {response.status_code})")
        url = response.text
        print(f'got {url}')
        response = requests.get(url)
        if response.status_code < 200 or response.status_code >= 300:
            startupError(f"Cannot load boostrap bundle (response status: {response.status_code})")
        importBundle(BytesIO(response.content))

    sites_json = private_dir / 'sites.json'
    if not os.path.isfile(sites_json):
        with open(sites_json, "w") as f:
            f.write("{}")
    users_json = private_dir / 'users.json'
    if not os.path.isfile(users_json):
        with open(users_json, "w") as f:
            f.write("{}")

def load_plugins():
    from Plugin import PluginManager
    PluginManager.plugin_manager.loadPlugins()
    config.loadPlugins()
    config.parse()  # Parse again to add plugin configuration options

def init():
    load_config()
    config.initConsoleLogger()

    try:
        init_dirs()
    except:
        import traceback as tb
        print(tb.format_exc())
        # at least make sure to print help if we're otherwise so helpless
        # config.parser.print_help()
        sys.exit(1)

    if config.action == "main":
        from util import helper
        try:
            lock = helper.openLocked(config.start_dir / 'lock.pid', "w")
            lock.write(f"{os.getpid()}")
        except BlockingIOError as err:
            startupError(f"Can't open lock file, your 0net client is probably already running, exiting... ({err})")
            proc = helper.openBrowser(config.open_browser)
            r = proc.wait()
            sys.exit(r)

    config.initLogging(console_logging=False)

    # Debug dependent configuration
    from Debug import DebugHook

    if not config.no_plugins:
        load_plugins()

    # Log current config
    logging.debug("Config: %s" % config)

    # Modify stack size on special hardwares
    if config.stack_size:
        import threading
        threading.stack_size(config.stack_size)

    # Use pure-python implementation of msgpack to save CPU
    if config.msgpack_purepython:
        os.environ["MSGPACK_PUREPYTHON"] = "True"

    # Fix console encoding on Windows
    # TODO: check if this is still required
    if sys.platform.startswith("win"):
        import subprocess
        try:
            chcp_res = subprocess.check_output("chcp 65001", shell=True).decode(errors="ignore").strip()
            logging.debug("Changed console encoding to utf8: %s" % chcp_res)
        except Exception as err:
            logging.error("Error changing console encoding to utf8: %s" % err)

    # Socket monkey patch
    if config.proxy:
        from util import SocksProxy
        import urllib.request
        logging.info("Patching sockets to socks proxy: %s" % config.proxy)
        if config.fileserver_ip == "*":
            config.fileserver_ip = '127.0.0.1'  # Do not accept connections anywhere but localhost
        config.disable_udp = True  # UDP not supported currently with proxy
        SocksProxy.monkeyPatch(*config.proxy.split(":"))
    elif config.tor == "always":
        from util import SocksProxy
        import urllib.request
        logging.info("Patching sockets to tor socks proxy: %s" % config.tor_proxy)
        if config.fileserver_ip == "*":
            config.fileserver_ip = '127.0.0.1'  # Do not accept connections anywhere but localhost
        SocksProxy.monkeyPatch(*config.tor_proxy_split())
        config.disable_udp = True
    elif config.bind:
        bind = config.bind
        if ":" not in config.bind:
            bind += ":0"
        from util import helper
        helper.socketBindMonkeyPatch(*bind.split(":"))

init()

from Actions import Actions

actions = Actions()

# Starts here when running zeronet.py
def start():
    # Call function
    action_kwargs = config.getActionArguments()
    actions.call(config.action, action_kwargs)
