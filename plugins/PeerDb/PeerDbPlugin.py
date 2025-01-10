import time
import sqlite3
import random
import atexit

import gevent
from Plugin import PluginManager

@PluginManager.registerTo('ContentDb')
class PeerDbPlugin:
    """Plugin that occasionally saves peers to db in order to speed up discovery on next startup

    NOTE: plugins are deprecated, it would be better to rewrite this; also, not sure why saving
    is once per hour, need to improve the logic.
    """

    def __init__(self, *args, **kwargs):
        atexit.register(self.saveAllPeers)
        super().__init__(*args, **kwargs)

    def getSchema(self):
        schema = super().getSchema()

        schema['tables']['peer'] = {
            'cols': [
                ['site_address', 'TEXT NOT NULL'],
                ['peer_address', 'TEXT NOT NULL'],
                ['port', 'INTEGER NOT NULL'],
                ['hashfield', 'BLOB'],
                ['reputation', 'INTEGER NOT NULL'],
                ['time_added', 'INTEGER NOT NULL'],
                ['time_found', 'INTEGER NOT NULL']
            ],
            'indexes': [
                'CREATE UNIQUE INDEX peer_key ON peer (site_address, peer_address, port)'
            ],
            'schema_changed': 5,
        }

        return schema

    def loadPeers(self, site):
        site_address = site.address
        res = self.execute('SELECT * FROM peer WHERE ?', {'site_address': site_address})
        num = 0
        num_hashfield = 0
        for row in res:
            peer = site.addPeer(row['peer_address'], row['port'])
            if not peer:
                continue
            if row["hashfield"]:
                peer.hashfield.replaceFromBytes(row["hashfield"])
                num_hashfield += 1
            peer.time_added = row["time_added"]
            peer.time_found = row["time_found"]
            peer.reputation = row["reputation"]
            if row['peer_address'].endswith('.onion'):
                # Onion peers less likely working after reloading
                peer.reputation = peer.reputation - 5
            num += 1
        if num_hashfield:
            site.content_manager.has_optional_files = True

    def iteratePeers(self, site):
        for key, peer in list(site.peers.items()):
            peer_address, port = key.rsplit(":", 1)
            if peer.has_hashfield:
                hashfield = sqlite3.Binary(peer.hashfield.tobytes())
            else:
                hashfield = ""
            yield (site.address, peer_address, port, hashfield, peer.reputation, int(peer.time_added), int(peer.time_found))

    def keepSaving(self, site):
        """Save peers and spawn self to save later again

        Delay is randomized in order to reduce probability of all sites
        saving their peers at the same time.
        """
        self.savePeers(site)
        delay = 60 * 60 + random.randint(-60, 60)
        site.greenlet_manager.spawnLater(delay, self.keepSaving, site)

    def savePeers(self, site):
        """Save peers to db"""
        if not site.peers:
            site.log.debug("Peers not saved: No peers found")
            return
        site_address = site.address
        cur = self.getCursor()
        try:
            cur.execute('DELETE FROM peer WHERE ?', {'site_address': site_address})
            cur.executemany(
                "INSERT INTO peer (site_address, peer_address, port, hashfield, reputation, time_added, time_found) VALUES (?, ?, ?, ?, ?, ?, ?)",
                self.iteratePeers(site)
            )
        except Exception as err:
            site.log.error("Save peer error: %s" % err)

    def initSite(self, site):
        super().initSite(site)
        site.greenlet_manager.spawnLater(0.5, self.loadPeers, site)
        site.greenlet_manager.spawnLater(60*60, self.keepSaving, site)

    def saveAllPeers(self):
        for site in list(self.sites.values()):
            try:
                self.savePeers(site)
            except Exception as err:
                site.log.error("Save peer error: %s" % err)
