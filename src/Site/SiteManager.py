import json
import logging
import re
import os
import time
import atexit

import gevent

import util
from Plugin import PluginManager
from Content import ContentDb
from Config import config
from util import helper
from util import RateLimit
from util import Cached
from Debug import Debug

@PluginManager.acceptPlugins
class SiteManager(object):
    def __init__(self):
        self.log = logging.getLogger("SiteManager")
        self.log.debug("SiteManager created.")
        self.sites = {}
        self.sites_changed = int(time.time())
        self.loaded = False
        gevent.spawn(self.saveTimer)
        atexit.register(lambda: self.save(recalculate_size=True))

    # Load all sites from data/sites.json
    @util.Noparallel()
    def load(self, cleanup=True, startup=False):
        from .Site import Site
        self.log.info(f'Loading sites... ({cleanup=}, {startup=})')
        self.loaded = False
        address_found = []
        added = 0
        load_s = time.time()
        # Load new adresses
        try:
            json_path = f"{config.data_dir}/sites.json"
            data = json.load(open(json_path))
        except Exception as err:
            self.log.error(f"Unable to load {json_path}: {err}")
            data = {}

        sites_need = []

        for address, settings in data.items():
            if address not in self.sites:
                use_db_storage = settings.get('use_db_storage', False)
                if use_db_storage or os.path.isfile(f"{config.data_dir}/{address}/content.json"):
                    # Root content.json exists, try load site
                    s = time.time()
                    try:
                        site = Site(address, settings=settings, use_db_storage=use_db_storage)
                        site.content_manager.contents.get("content.json")
                    except Exception as err:
                        self.log.debug(f"Error loading site {address}: {err}")
                        continue
                    self.sites[address] = site
                    self.log.debug(f"Loaded site {address} in {time.time() - s:.3f}s")
                    added += 1
                elif startup:
                    # No site directory, start download
                    self.log.debug(f"Found new site in sites.json: {address}")
                    sites_need.append([address, settings])
                    added += 1

            address_found.append(address)

        # Remove deleted adresses
        if cleanup:
            for address in list(self.sites.keys()):
                if address not in address_found:
                    del(self.sites[address])
                    self.log.debug(f"Removed site: {address}")

            # Remove orpan sites from contentdb
            content_db = ContentDb.getContentDb()
            for row in content_db.execute("SELECT * FROM site").fetchall():
                address = row["address"]
                if address not in self.sites and address not in address_found:
                    self.log.info(f"Deleting orphan site from content.db: {address}")

                    try:
                        content_db.execute("DELETE FROM site WHERE ?", {"address": address})
                    except Exception as err:
                        self.log.error(f"Can't delete site {address} from content_db: {err}")

                    if address in content_db.site_ids:
                        del content_db.site_ids[address]
                    if address in content_db.sites:
                        del content_db.sites[address]

        self.loaded = True
        for address, settings in sites_need:
            gevent.spawn(self.need, address, settings=settings)
        if added:
            self.log.info(f"Added {added} sites in {time.time() - load_s:.3f}s")

    def saveDelayed(self):
        RateLimit.callAsync("Save sites.json", allowed_again=5, func=self.save)

    def save(self, recalculate_size=False):
        if not self.sites:
            self.log.debug("Save skipped: No sites found")
            return
        if not self.loaded:
            self.log.debug("Save skipped: Not loaded")
            return
        s = time.time()
        data = {}
        # Generate data file
        s = time.time()
        for address, site in list(self.list().items()):
            if recalculate_size:
                site.settings["size"], site.settings["size_optional"] = site.content_manager.getTotalSize()  # Update site size
            data[address] = site.settings
            data[address]["cache"] = site.getSettingsCache()
        time_generate = time.time() - s

        s = time.time()
        if data:
            helper.atomicWrite(f"{config.data_dir}/sites.json", helper.jsonDumps(data).encode("utf8"))
        else:
            self.log.debug("Save error: No data")
        time_write = time.time() - s

        # Remove cache from site settings
        for address, site in self.list().items():
            site.settings["cache"] = {}

        self.log.debug(f"Saved sites in {time.time() - s:.2f}s (generate: {time_generate:.2f}s, write: {time_write:.2f}s)")

    def saveTimer(self):
        while 1:
            time.sleep(60 * 10)
            self.save(recalculate_size=True)

    # Checks if its a valid address
    def isAddress(self, address):
        return re.match("^[A-Za-z0-9]{26,35}$", address)

    def isDomain(self, address):
        return False

    @Cached(timeout=10)
    def isDomainCached(self, address):
        return self.isDomain(address)

    def resolveDomain(self, domain):
        return False

    @Cached(timeout=10)
    def resolveDomainCached(self, domain):
        return self.resolveDomain(domain)

    # Return: Site object or None if not found
    def get(self, address):
        if self.isDomainCached(address):
            address_resolved = self.resolveDomainCached(address)
            if address_resolved:
                address = address_resolved

        if not self.loaded:  # Not loaded yet
            self.log.debug(f"Loading site: {address}...")
            self.load()
        site = self.sites.get(address)

        return site

    def add(self, address, all_file=True, settings=None):
        from .Site import Site
        self.sites_changed = int(time.time())
        # Try to find site with differect case
        for recover_address, recover_site in list(self.sites.items()):
            if recover_address.lower() == address.lower():
                return recover_site

        if not self.isAddress(address):
            return False  # Not address: %s % address
        self.log.debug(f"Added new site: {address}")
        config.loadTrackersFile()
        use_db_storage = bool(settings and settings.get('use_db_storage'))
        site = Site(address, settings=settings, use_db_storage=use_db_storage)
        self.sites[address] = site
        if not site.settings.get("serving", False):  # Maybe it was deleted before
            site.settings["serving"] = True
        site.saveSettings()
        if all_file:  # Also download user files on first sync
            site.download(check_size=True, blind_includes=True)
        return site

    # Return or create site and start download site files
    def need(self, address, *args, **kwargs):
        if self.isDomainCached(address):
            address_resolved = self.resolveDomainCached(address)
            if address_resolved:
                address = address_resolved

        site = self.get(address)
        if not site:  # Site is not loaded
            site = self.add(address, *args, **kwargs)
        return site

    def delete(self, address):
        self.sites_changed = int(time.time())
        self.log.debug(f"Deleted site: {address}")
        del(self.sites[address])
        # Delete from sites.json
        self.save()

    # Lazy load sites
    def list(self):
        if not self.loaded:  # Not loaded yet
            self.log.debug("Sites not loaded yet...")
            self.load(startup=True)
        return self.sites


site_manager = SiteManager()  # Singletone

if config.action == "main":  # Don't connect / add myself to peerlist
    peer_blacklist = [("127.0.0.1", config.fileserver_port), ("::1", config.fileserver_port)]
else:
    peer_blacklist = []

