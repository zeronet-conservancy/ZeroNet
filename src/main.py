import os
import sys
import stat
import time
import logging

startup_errors = []
def startupError(msg):
    startup_errors.append(msg)
    print(f"Startup error: {msg}")

# Third party modules
import gevent
if gevent.version_info.major <= 1:  # Workaround for random crash when libuv used with threads
    try:
        if "libev" not in str(gevent.config.loop):
            gevent.config.loop = "libev-cext"
    except Exception as err:
        startupError(f"Unable to switch gevent loop to libev: {err}")

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

load_config()

def importBundle(bundle):
    from zipfile import ZipFile
    from Crypt.CryptBitcoin import isValidAddress
    import json

    sites_json_path = f"{config.data_dir}/sites.json"
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
                                map(lambda f: f.removeprefix(prefix).split('/')[0], all_files))))
        for d in top_2:
            if isValidAddress(d):
                logging.info(f'unpack {d} into {config.data_dir}')
                for fname in filter(lambda f: f.startswith(prefix+d) and not f.endswith('/'), all_files):
                    tgt = config.data_dir + '/' + fname.removeprefix(prefix)
                    logging.info(f'-- {fname} --> {tgt}')
                    info = zf.getinfo(fname)
                    info.filename = tgt
                    zf.extract(info)
                logging.info(f'add site {d}')
                sites[d] = {}
            else:
                logging.info(f'Warning: unknown file in a bundle: {prefix+d}')
    with open(sites_json_path, 'w') as f:
        json.dump(sites, f)

def init_dirs():
    data_dir = config.data_dir
    has_data_dir = os.path.isdir(data_dir)
    need_bootstrap = not config.disable_bootstrap and (not has_data_dir or not os.path.isfile(f'{data_dir}/sites.json')) and not config.offline

    if not has_data_dir:
        os.mkdir(data_dir)
        try:
            os.chmod(data_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        except Exception as err:
            startupError(f"Can't change permission of {data_dir}: {err}")

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

    sites_json = f"{data_dir}/sites.json"
    if not os.path.isfile(sites_json):
        with open(sites_json, "w") as f:
            f.write("{}")
    users_json = f"{data_dir}/users.json"
    if not os.path.isfile(users_json):
        with open(users_json, "w") as f:
            f.write("{}")

# TODO: GET RID OF TOP-LEVEL CODE!!!
config.initConsoleLogger()

try:
    init_dirs()
except:
    import traceback as tb
    print(tb.format_exc())
    # at least make sure to print help if we're otherwise so helpless
    config.parser.print_help()
    sys.exit(1)

if config.action == "main":
    from util import helper
    try:
        lock = helper.openLocked(f"{config.data_dir}/lock.pid", "w")
        lock.write(f"{os.getpid()}")
    except BlockingIOError as err:
        startupError(f"Can't open lock file, your 0net client is probably already running, exiting... ({err})")
        proc = helper.openBrowser(config.open_browser)
        r = proc.wait()
        sys.exit(r)

config.initLogging(console_logging=False)

# Debug dependent configuration
from Debug import DebugHook
from Plugin import PluginManager

def load_plugins():
    PluginManager.plugin_manager.loadPlugins()
    config.loadPlugins()
    config.parse()  # Parse again to add plugin configuration options

load_plugins()

# Log current config
logging.debug(f"Config: {config}")

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
        logging.debug(f"Changed console encoding to utf8: {chcp_res}")
    except Exception as err:
        logging.error(f"Error changing console encoding to utf8: {err}")

# Socket monkey patch
if config.proxy:
    from util import SocksProxy
    import urllib.request
    logging.info(f"Patching sockets to socks proxy: {config.proxy}")
    if config.fileserver_ip == "*":
        config.fileserver_ip = '127.0.0.1'  # Do not accept connections anywhere but localhost
    config.disable_udp = True  # UDP not supported currently with proxy
    SocksProxy.monkeyPatch(*config.proxy.split(":"))
elif config.tor == "always":
    from util import SocksProxy
    import urllib.request
    logging.info(f"Patching sockets to tor socks proxy: {config.tor_proxy}")
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

# -- Actions --


@PluginManager.acceptPlugins
class Actions:
    def call(self, function_name, kwargs):
        logging.info(f"Version: {config.version} r{config.rev}, Python {sys.version}, Gevent: {gevent.__version__}")

        func = getattr(self, function_name, None)
        back = func(**kwargs)
        if back:
            print(back)

    def ipythonThread(self):
        import IPython
        IPython.embed()
        self.gevent_quit.set()

    # Default action: Start serving UiServer and FileServer
    def main(self):
        global ui_server, file_server
        from File import FileServer
        from Ui import UiServer
        logging.info("Creating FileServer....")
        file_server = FileServer()
        logging.info("Creating UiServer....")
        ui_server = UiServer()
        file_server.ui_server = ui_server

        for startup_error in startup_errors:
            logging.error(f"Startup error: {startup_error}")

        logging.info("Removing old SSL certs...")
        from Crypt import CryptConnection
        CryptConnection.manager.removeCerts()

        logging.info("Starting servers....")

        import threading
        self.gevent_quit = threading.Event()
        launched_greenlets = [gevent.spawn(ui_server.start), gevent.spawn(file_server.start), gevent.spawn(ui_server.startSiteServer)]

        # if --repl, start ipython thread
        # FIXME: Unfortunately this leads to exceptions on exit so use with care
        if config.repl:
            threading.Thread(target=self.ipythonThread).start()

        stopped = 0
        # Process all greenlets in main thread
        while not self.gevent_quit.is_set() and stopped < len(launched_greenlets):
            stopped += len(gevent.joinall(launched_greenlets, timeout=1))

        # Exited due to repl, so must kill greenlets
        if stopped < len(launched_greenlets):
            gevent.killall(launched_greenlets, exception=KeyboardInterrupt)

        logging.info("All servers stopped")

    # Site commands

    def siteCreate(self, use_master_seed=True):
        logging.info(f"Generating new privatekey (use_master_seed: {config.use_master_seed})...")
        from Crypt import CryptBitcoin
        if use_master_seed:
            from User import UserManager
            user = UserManager.user_manager.get()
            if not user:
                user = UserManager.user_manager.create()
            address, address_index, site_data = user.getNewSiteData()
            privatekey = site_data["privatekey"]
            logging.info(f"Generated using master seed from users.json, site index: {address_index}")
        else:
            privatekey = CryptBitcoin.newPrivatekey()
            address = CryptBitcoin.privatekeyToAddress(privatekey)
        logging.info("----------------------------------------------------------------------")
        logging.info(f"Site private key: {privatekey}")
        logging.info("                  !!! ^ Save it now, required to modify the site ^ !!!")
        logging.info(f"Site address:     {address}")
        logging.info("----------------------------------------------------------------------")

        while True and not config.batch and not use_master_seed:
            if input("? Have you secured your private key? (yes, no) > ").lower() == "yes":
                break
            else:
                logging.info("Please, secure it now, you going to need it to modify your site!")

        logging.info("Creating directory structure...")
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        os.mkdir(f"{config.data_dir}/{address}")
        open(f"{config.data_dir}/{address}/index.html", "w").write(f"Hello {address}!")

        logging.info("Creating content.json...")
        site = Site(address)
        extend = {"postmessage_nonce_security": True}
        if use_master_seed:
            extend["address_index"] = address_index

        site.content_manager.sign(privatekey=privatekey, extend=extend)
        site.settings["own"] = True
        site.saveSettings()

        logging.info("Site created!")

    def siteSign(self, address, privatekey=None, inner_path="content.json", publish=False, remove_missing_optional=False):
        from Site.Site import Site
        from Site import SiteManager
        from Debug import Debug
        SiteManager.site_manager.load()
        logging.info(f"Signing site: {address}...")
        site = Site(address, allow_create=False)

        if not privatekey:  # If no privatekey defined
            from User import UserManager
            user = UserManager.user_manager.get()
            if user:
                site_data = user.getSiteData(address)
                privatekey = site_data.get("privatekey")
            else:
                privatekey = None
            if not privatekey:
                # Not found in users.json, ask from console
                import getpass
                privatekey = getpass.getpass("Private key (input hidden):")
        # inner_path can be either relative to site directory or absolute/relative path
        if os.path.isabs(inner_path):
            full_path = os.path.abspath(inner_path)
        else:
            full_path = os.path.abspath(config.working_dir + '/' + inner_path)
        print(full_path)
        if os.path.isfile(full_path):
            if address in full_path:
                # assuming site address is unique, keep only path after it
                inner_path = full_path.split(address+'/')[1]
            else:
                # oops, file that we found seems to be rogue, so reverting to old behaviour
                logging.warning(f'using {inner_path} relative to site directory')
        try:
            succ = site.content_manager.sign(
                inner_path=inner_path, privatekey=privatekey,
                update_changed_files=True, remove_missing_optional=remove_missing_optional
            )
        except Exception as err:
            logging.error(f"Sign error: {Debug.formatException(err)}")
            succ = False
        if succ and publish:
            self.sitePublish(address, inner_path=inner_path)

    def siteVerify(self, address):
        import time
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        s = time.time()
        logging.info(f"Verifing site: {address}...")
        site = Site(address)
        bad_files = []

        for content_inner_path in site.content_manager.contents:
            s = time.time()
            logging.info(f"Verifing {content_inner_path} signature...")
            error = None
            try:
                file_correct = site.content_manager.verifyFile(
                    content_inner_path, site.storage.open(content_inner_path, "rb"), ignore_same=False
                )
            except Exception as err:
                file_correct = False
                error = err

            if file_correct is True:
                logging.info(f"[OK] {content_inner_path} (Done in {time.time() - s:.3f}s)")
            else:
                logging.error(f"[ERROR] {content_inner_path}: invalid file: {error}!")
                input("Continue?")
                bad_files += content_inner_path

        logging.info("Verifying site files...")
        bad_files += site.storage.verifyFiles()["bad_files"]
        if not bad_files:
            logging.info(f"[OK] All file sha512sum matches! ({time.time() - s:.3f}s)")
        else:
            logging.error("[ERROR] Error during verifying site files!")

    def dbRebuild(self, address):
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        logging.info(f"Rebuilding site sql cache: {address}...")
        site = SiteManager.site_manager.get(address)
        s = time.time()
        try:
            site.storage.rebuildDb()
            logging.info(f"Done in {time.time() - s:.3f}s")
        except Exception as err:
            logging.error(err)

    def dbQuery(self, address, query):
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        import json
        site = Site(address)
        result = []
        for row in site.storage.query(query):
            result.append(dict(row))
        print(json.dumps(result, indent=4))

    def siteAnnounce(self, address):
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        logging.info("Opening a simple connection server")
        global file_server
        from File import FileServer
        file_server = FileServer("127.0.0.1", 1234)
        file_server.start()

        logging.info(f"Announcing site {address} to tracker...")
        site = Site(address)

        s = time.time()
        site.announce()
        print(f"Response time: {time.time() - s:.3f}s")
        print(site.peers)

    def siteDownload(self, address):
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        logging.info("Opening a simple connection server")
        global file_server
        from File import FileServer
        file_server = FileServer("127.0.0.1", 1234)
        file_server_thread = gevent.spawn(file_server.start, check_sites=False)

        site = Site(address)

        on_completed = gevent.event.AsyncResult()

        def onComplete(evt):
            evt.set(True)

        site.onComplete.once(lambda: onComplete(on_completed))
        print("Announcing...")
        site.announce()

        s = time.time()
        print("Downloading...")
        site.downloadContent("content.json", check_modifications=True)

        print(f"Downloaded in {time.time() - s:.3f}s")

    def siteNeedFile(self, address, inner_path):
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        def checker():
            while 1:
                s = time.time()
                time.sleep(1)
                print("Switch time:", time.time() - s)
        gevent.spawn(checker)

        logging.info("Opening a simple connection server")
        global file_server
        from File import FileServer
        file_server = FileServer("127.0.0.1", 1234)
        file_server_thread = gevent.spawn(file_server.start, check_sites=False)

        site = Site(address)
        site.announce()
        print(site.needFile(inner_path, update=True))

    def siteCmd(self, address, cmd, parameters):
        import json
        from Site import SiteManager

        site = SiteManager.site_manager.get(address)

        if not site:
            logging.error(f"Site not found: {address}")
            return None

        ws = self.getWebsocket(site)

        ws.send(json.dumps({"cmd": cmd, "params": parameters, "id": 1}))
        res_raw = ws.recv()

        try:
            res = json.loads(res_raw)
        except Exception as err:
            return {"error": f"Invalid result: {err}", "res_raw": res_raw}

        if "result" in res:
            return res["result"]
        else:
            return res

    def importBundle(self, bundle):
        importBundle(bundle)

    def getWebsocket(self, site):
        import websocket

        ws_address = f"ws://{config.ui_ip}:{config.ui_port}/Websocket?wrapper_key={site.settings['wrapper_key']}"
        logging.info(f"Connecting to {ws_address}")
        ws = websocket.create_connection(ws_address)
        return ws

    def sitePublish(self, address, peer_ip=None, peer_port=15441, inner_path="content.json", recursive=False):
        from Site import SiteManager
        logging.info("Loading site...")
        site = SiteManager.site_manager.get(address)
        site.settings["serving"] = True  # Serving the site even if its disabled

        if not recursive:
            inner_paths = [inner_path]
        else:
            inner_paths = list(site.content_manager.contents.keys())

        try:
            ws = self.getWebsocket(site)

        except Exception as err:
            self.sitePublishFallback(site, peer_ip, peer_port, inner_paths, err)

        else:
            logging.info("Sending siteReload")
            self.siteCmd(address, "siteReload", inner_path)

            for inner_path in inner_paths:
                logging.info(f"Sending sitePublish for {inner_path}")
                self.siteCmd(address, "sitePublish", {"inner_path": inner_path, "sign": False})
            logging.info("Done.")
            ws.close()

    def sitePublishFallback(self, site, peer_ip, peer_port, inner_paths, err):
        if err is not None:
            logging.info(f"Can't connect to local websocket client: {err}")
        logging.info("Publish using fallback mechanism. "
                     "Note that there might be not enough time for peer discovery, "
                     "but you can specify target peer on command line.")
        logging.info("Creating FileServer....")
        file_server_thread = gevent.spawn(file_server.start, check_sites=False)  # Dont check every site integrity
        time.sleep(0.001)

        # Started fileserver
        file_server.portCheck()
        if peer_ip:  # Announce ip specificed
            site.addPeer(peer_ip, peer_port)
        else:  # Just ask the tracker
            logging.info("Gathering peers from tracker")
            site.announce()  # Gather peers

        for inner_path in inner_paths:
            published = site.publish(5, inner_path)  # Push to peers

        if published > 0:
            time.sleep(3)
            logging.info("Serving files (max 60s)...")
            gevent.joinall([file_server_thread], timeout=60)
            logging.info("Done.")
        else:
            logging.info("No peers found, sitePublish command only works if you already have visitors serving your site")

    # Crypto commands
    def cryptPrivatekeyToAddress(self, privatekey=None):
        from Crypt import CryptBitcoin
        if not privatekey:  # If no privatekey in args then ask it now
            import getpass
            privatekey = getpass.getpass("Private key (input hidden):")

        print(CryptBitcoin.privatekeyToAddress(privatekey))

    def cryptSign(self, message, privatekey):
        from Crypt import CryptBitcoin
        print(CryptBitcoin.sign(message, privatekey))

    def cryptVerify(self, message, sign, address):
        from Crypt import CryptBitcoin
        print(CryptBitcoin.verify(message, address, sign))

    def cryptGetPrivatekey(self, master_seed, site_address_index=None):
        from Crypt import CryptBitcoin
        if len(master_seed) != 64:
            logging.error(f"Error: Invalid master seed length: {len(master_seed)} (required: 64)")
            return False
        privatekey = CryptBitcoin.hdPrivatekey(master_seed, site_address_index)
        print(f"Requested private key: {privatekey}")

    # Peer
    def peerPing(self, peer_ip, peer_port=None):
        if not peer_port:
            peer_port = 15441
        logging.info("Opening a simple connection server")
        global file_server
        from Connection import ConnectionServer
        file_server = ConnectionServer("127.0.0.1", 1234)
        file_server.start(check_connections=False)
        from Crypt import CryptConnection
        CryptConnection.manager.loadCerts()

        from Peer import Peer
        logging.info(f"Pinging 5 times peer: {peer_ip}:{int(peer_port)}...")
        s = time.time()
        peer = Peer(peer_ip, peer_port)
        peer.connect()

        if not peer.connection:
            print(f"Error: Can't connect to peer (connection error: {peer.connection_error})")
            return False
        if "shared_ciphers" in dir(peer.connection.sock):
            print("Shared ciphers:", peer.connection.sock.shared_ciphers())
        if "cipher" in dir(peer.connection.sock):
            print("Cipher:", peer.connection.sock.cipher()[0])
        if "version" in dir(peer.connection.sock):
            print("TLS version:", peer.connection.sock.version())
        print(f"Connection time: {time.time() - s:.3f}s  (connection error: {peer.connection_error})")

        for i in range(5):
            ping_delay = peer.ping()
            print(f"Response time: {ping_delay:.3f}s")
            time.sleep(1)
        peer.remove()
        print("Reconnect test...")
        peer = Peer(peer_ip, peer_port)
        for i in range(5):
            ping_delay = peer.ping()
            print(f"Response time: {ping_delay:.3f}s")
            time.sleep(1)

    def peerGetFile(self, peer_ip, peer_port, site, filename, benchmark=False):
        logging.info("Opening a simple connection server")
        global file_server
        from Connection import ConnectionServer
        file_server = ConnectionServer("127.0.0.1", 1234)
        file_server.start(check_connections=False)
        from Crypt import CryptConnection
        CryptConnection.manager.loadCerts()

        from Peer import Peer
        logging.info(f"Getting {site}/{filename} from peer: {peer_ip}:{peer_port}...")
        peer = Peer(peer_ip, peer_port)
        s = time.time()
        if benchmark:
            for i in range(10):
                peer.getFile(site, filename),
            print(f"Response time: {time.time() - s:.3f}s")
            input("Check memory")
        else:
            print(peer.getFile(site, filename).read())

    def peerCmd(self, peer_ip, peer_port, cmd, parameters):
        logging.info("Opening a simple connection server")
        global file_server
        from Connection import ConnectionServer
        file_server = ConnectionServer()
        file_server.start(check_connections=False)
        from Crypt import CryptConnection
        CryptConnection.manager.loadCerts()

        from Peer import Peer
        peer = Peer(peer_ip, peer_port)

        import json
        if parameters:
            parameters = json.loads(parameters.replace("'", '"'))
        else:
            parameters = {}
        try:
            res = peer.request(cmd, parameters)
            print(json.dumps(res, indent=2, ensure_ascii=False))
        except Exception as err:
            print(f"Unknown response ({err}): {res}")

    def getConfig(self):
        import json
        print(json.dumps(config.getServerInfo(), indent=2, ensure_ascii=False))

    def test(self, test_name, *args, **kwargs):
        import types
        def funcToName(func_name):
            test_name = func_name.replace("test", "")
            return test_name[0].lower() + test_name[1:]

        test_names = [funcToName(name) for name in dir(self) if name.startswith("test") and name != "test"]
        if not test_name:
            # No test specificed, list tests
            print("\nNo test specified, possible tests:")
            for test_name in test_names:
                func_name = "test" + test_name[0].upper() + test_name[1:]
                func = getattr(self, func_name)
                if func.__doc__:
                    print(f"- {test_name}: {func.__doc__.strip()}")
                else:
                    print(f"- {test_name}")
            return None

        # Run tests
        func_name = "test" + test_name[0].upper() + test_name[1:]
        if hasattr(self, func_name):
            func = getattr(self, func_name)
            print(f"- Running test: {test_name}", end="")
            s = time.time()
            ret = func(*args, **kwargs)
            if type(ret) is types.GeneratorType:
                for progress in ret:
                    print(progress, end="")
                    sys.stdout.flush()
            print(f"\n* Test {test_name} done in {time.time() - s:.3f}s")
        else:
            print(f"Unknown test: {test_name!r} (choose from: {test_names})")


actions = Actions()
# Starts here when running zeronet.py


def start():
    # Call function
    action_kwargs = config.getActionArguments()
    actions.call(config.action, action_kwargs)
