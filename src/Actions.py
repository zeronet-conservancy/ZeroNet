import logging
import sys
import gevent
import time
from Config import config
from Plugin import PluginManager

@PluginManager.acceptPlugins
class Actions:
    def call(self, function_name, kwargs):
        logging.info(f'zeronet-conservancy {config.version_full} on Python {sys.version} Gevent {gevent.__version__}')

        func = getattr(self, function_name, None)
        back = func(**kwargs)
        if back:
            print(back)

    def ipythonThread(self):
        import IPython
        IPython.embed()
        self.gevent_quit.set()

    def initDHT(self):
        import main
        if config.dht:
            from DHT import DHTServer
            main.dht_server = DHTServer()
        else:
            main.dht_server = None

    # Default action: Start serving UiServer and FileServer
    def main(self):
        import main
        from File import FileServer
        from Ui import UiServer

        import API
        self.initDHT()

        main.file_server = FileServer()
        logging.info("Creating UiServer....")
        main.ui_server = UiServer()
        main.file_server.ui_server = main.ui_server

        # for startup_error in startup_errors:
            # logging.error("Startup error: %s" % startup_error)

        logging.info("Removing old SSL certs...")
        from Crypt import CryptConnection
        CryptConnection.manager.removeCerts()

        logging.info("Starting servers....")

        import threading
        self.gevent_quit = threading.Event()
        launched_greenlets = [
            gevent.spawn(main.ui_server.start),
            gevent.spawn(main.ui_server.startSiteServer),
            gevent.spawn(main.file_server.start),
        ]
        if main.dht_server is not None:
            launched_greenlets.append(gevent.spawn(main.dht_server.start))

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

        logging.info("All server stopped")

    # Site commands

    def siteCreate(self, use_master_seed=True):
        logging.info("Generating new privatekey (use_master_seed: %s)..." % config.use_master_seed)
        from Crypt import CryptBitcoin
        if use_master_seed:
            from User import UserManager
            user = UserManager.user_manager.get()
            if not user:
                user = UserManager.user_manager.create()
            address, address_index, site_data = user.getNewSiteData()
            privatekey = site_data["privatekey"]
            logging.info("Generated using master seed from users.json, site index: %s" % address_index)
        else:
            privatekey = CryptBitcoin.newPrivatekey()
            address = CryptBitcoin.privatekeyToAddress(privatekey)
        logging.info("----------------------------------------------------------------------")
        logging.info("Site private key: %s" % privatekey)
        logging.info("                  !!! ^ Save it now, required to modify the site ^ !!!")
        logging.info("Site address:     %s" % address)
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

        (config.data_dir / address).mkdir()
        (config.data_dir / address / 'index.html').open('w').write(f"Hello {address}!")

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
        logging.info("Signing site: %s..." % address)
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
            logging.error("Sign error: %s" % Debug.formatException(err))
            succ = False
        if succ and publish:
            self.sitePublish(address, inner_path=inner_path)

    def siteVerify(self, address):
        import time
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        s = time.time()
        logging.info("Verifing site: %s..." % address)
        site = Site(address)
        bad_files = []

        for content_inner_path in site.content_manager.contents:
            s = time.time()
            logging.info("Verifing %s signature..." % content_inner_path)
            error = None
            try:
                file_correct = site.content_manager.verifyFile(
                    content_inner_path, site.storage.open(content_inner_path, "rb"), ignore_same=False
                )
            except Exception as err:
                file_correct = False
                error = err

            if file_correct is True:
                logging.info("[OK] %s (Done in %.3fs)" % (content_inner_path, time.time() - s))
            else:
                logging.error("[ERROR] %s: invalid file: %s!" % (content_inner_path, error))
                input("Continue?")
                bad_files += content_inner_path

        logging.info("Verifying site files...")
        bad_files += site.storage.verifyFiles()["bad_files"]
        if not bad_files:
            logging.info("[OK] All file sha512sum matches! (%.3fs)" % (time.time() - s))
        else:
            logging.error("[ERROR] Error during verifying site files!")

    def dbRebuild(self, address):
        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        logging.info("Rebuilding site sql cache: %s..." % address)
        site = SiteManager.site_manager.get(address)
        s = time.time()
        try:
            site.storage.rebuildDb()
            logging.info("Done in %.3fs" % (time.time() - s))
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
        import main
        self.initDHT()

        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        logging.info("Opening a simple connection server")
        from File import FileServer
        main.file_server = FileServer("127.0.0.1", 1234)
        main.file_server.start()

        logging.info("Announcing site %s to tracker..." % address)
        site = Site(address)

        s = time.time()
        site.announce()
        print("Response time: %.3fs" % (time.time() - s))
        print(site.peers)

    def siteDownload(self, address):
        import main
        self.initDHT()

        from Site.Site import Site
        from Site import SiteManager
        SiteManager.site_manager.load()

        logging.info("Opening a simple connection server")
        from File import FileServer
        main.file_server = FileServer("127.0.0.1", 1234)

        launched_greenlets = [
            gevent.spawn(main.file_server.start, check_sites=False),
        ]
        if main.dht_server is not None:
            launched_greenlets.append(gevent.spawn(main.dht_server.start))

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

        print("Downloaded in %.3fs" % (time.time()-s))

    def siteNeedFile(self, address, inner_path):
        import main
        self.initDHT()

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
        from File import FileServer
        main.file_server = FileServer("127.0.0.1", 1234)
        file_server_thread = gevent.spawn(main.file_server.start, check_sites=False)

        site = Site(address)
        site.announce()
        print(site.needFile(inner_path, update=True))

    def siteCmd(self, address, cmd, parameters):
        import json
        from Site import SiteManager

        site = SiteManager.site_manager.get(address)

        if not site:
            logging.error("Site not found: %s" % address)
            return None

        ws = self.getWebsocket(site)

        ws.send(json.dumps({"cmd": cmd, "params": parameters, "id": 1}))
        res_raw = ws.recv()

        try:
            res = json.loads(res_raw)
        except Exception as err:
            return {"error": "Invalid result: %s" % err, "res_raw": res_raw}

        if "result" in res:
            return res["result"]
        else:
            return res

    def importBundle(self, bundle):
        import main
        main.importBundle(bundle)

    def getWebsocket(self, site):
        import websocket

        ws_address = "ws://%s:%s/Websocket?wrapper_key=%s" % (config.ui_ip, config.ui_port, site.settings["wrapper_key"])
        logging.info("Connecting to %s" % ws_address)
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
        import main
        if err is not None:
            logging.info(f"Can't connect to local websocket client: {err}")
        logging.info("Publish using fallback mechanism. "
                     "Note that there might be not enough time for peer discovery, "
                     "but you can specify target peer on command line.")
        logging.info("Creating FileServer....")
        file_server_thread = gevent.spawn(main.file_server.start, check_sites=False)  # Dont check every site integrity
        time.sleep(0.001)

        # Started fileserver
        main.file_server.portCheck()
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
            logging.error("Error: Invalid master seed length: %s (required: 64)" % len(master_seed))
            return False
        privatekey = CryptBitcoin.hdPrivatekey(master_seed, site_address_index)
        print("Requested private key: %s" % privatekey)

    # Peer
    def peerPing(self, peer_ip, peer_port=None):
        import main
        if not peer_port:
            peer_port = 15441
        logging.info("Opening a simple connection server")
        from Connection import ConnectionServer
        main.file_server = ConnectionServer("127.0.0.1", 1234)
        main.file_server.start(check_connections=False)
        from Crypt import CryptConnection
        CryptConnection.manager.loadCerts()

        from Peer import Peer
        logging.info("Pinging 5 times peer: %s:%s..." % (peer_ip, int(peer_port)))
        s = time.time()
        peer = Peer(peer_ip, peer_port)
        peer.connect()

        if not peer.connection:
            print("Error: Can't connect to peer (connection error: %s)" % peer.connection_error)
            return False
        if "shared_ciphers" in dir(peer.connection.sock):
            print("Shared ciphers:", peer.connection.sock.shared_ciphers())
        if "cipher" in dir(peer.connection.sock):
            print("Cipher:", peer.connection.sock.cipher()[0])
        if "version" in dir(peer.connection.sock):
            print("TLS version:", peer.connection.sock.version())
        print("Connection time: %.3fs  (connection error: %s)" % (time.time() - s, peer.connection_error))

        for i in range(5):
            ping_delay = peer.ping()
            print("Response time: %.3fs" % ping_delay)
            time.sleep(1)
        peer.remove()
        print("Reconnect test...")
        peer = Peer(peer_ip, peer_port)
        for i in range(5):
            ping_delay = peer.ping()
            print("Response time: %.3fs" % ping_delay)
            time.sleep(1)

    def peerGetFile(self, peer_ip, peer_port, site, filename, benchmark=False):
        import main
        logging.info("Opening a simple connection server")
        from Connection import ConnectionServer
        main.file_server = ConnectionServer("127.0.0.1", 1234)
        main.file_server.start(check_connections=False)
        from Crypt import CryptConnection
        CryptConnection.manager.loadCerts()

        from Peer import Peer
        logging.info("Getting %s/%s from peer: %s:%s..." % (site, filename, peer_ip, peer_port))
        peer = Peer(peer_ip, peer_port)
        s = time.time()
        if benchmark:
            for i in range(10):
                peer.getFile(site, filename),
            print("Response time: %.3fs" % (time.time() - s))
            input("Check memory")
        else:
            print(peer.getFile(site, filename).read())

    def peerCmd(self, peer_ip, peer_port, cmd, parameters):
        import main
        logging.info("Opening a simple connection server")
        from Connection import ConnectionServer
        main.file_server = ConnectionServer()
        main.file_server.start(check_connections=False)
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
            print("Unknown response (%s): %s" % (err, res))

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
                    print("- %s: %s" % (test_name, func.__doc__.strip()))
                else:
                    print("- %s" % test_name)
            return None

        # Run tests
        func_name = "test" + test_name[0].upper() + test_name[1:]
        if hasattr(self, func_name):
            func = getattr(self, func_name)
            print("- Running test: %s" % test_name, end="")
            s = time.time()
            ret = func(*args, **kwargs)
            if type(ret) is types.GeneratorType:
                for progress in ret:
                    print(progress, end="")
                    sys.stdout.flush()
            print("\n* Test %s done in %.3fs" % (test_name, time.time() - s))
        else:
            print("Unknown test: %r (choose from: %s)" % (
                test_name, test_names
            ))
