import os

from Db.Db import Db, DbTableError
from Config import config
from Plugin import PluginManager
from Debug import Debug
from pathlib import Path

@PluginManager.acceptPlugins
class ContentDb(Db):
    """Interface for global content.db. This stores sites' content.json data and other info"""
    def __init__(self, path):
        Db.__init__(self, {"db_name": "ContentDb", "tables": {}}, path)
        self.foreign_keys = True

    def init(self):
        try:
            self.schema = self.getSchema()
            try:
                self.checkTables()
            except DbTableError:
                pass
            self.log.debug("Checking foreign keys...")
            foreign_key_error = self.execute("PRAGMA foreign_key_check").fetchone()
            if foreign_key_error:
                raise Exception("Database foreign key error: %s" % foreign_key_error)
        except Exception as err:
            self.log.error("Error loading content.db: %s, rebuilding..." % Debug.formatException(err))
            self.close()
            os.unlink(self.db_path)  # Remove and try again
            Db.__init__(self, {"db_name": "ContentDb", "tables": {}}, self.db_path)
            self.foreign_keys = True
            self.schema = self.getSchema()
            try:
                self.checkTables()
            except DbTableError:
                pass
        self.sites = {}

    def getSchema(self):
        schema = {}
        schema["db_name"] = "ContentDb"
        schema["version"] = 3
        schema["tables"] = {}

        if not self.getTableVersion("site"):
            self.log.debug("Migrating from table version-less content.db")
            version = int(self.execute("PRAGMA user_version").fetchone()[0])
            if version > 0:
                self.checkTables()
                self.execute("INSERT INTO keyvalue ?", {"json_id": 0, "key": "table.site.version", "value": 1})
                self.execute("INSERT INTO keyvalue ?", {"json_id": 0, "key": "table.content.version", "value": 1})
                self.execute('INSERT INTO keyvalue ?', {'json_id': 0, 'key': 'table.size_limit.version', 'value': 1})

        schema["tables"]["content"] = {
            "cols": [
                ["content_id", "INTEGER PRIMARY KEY UNIQUE NOT NULL"],
                ["address", "TEXT NOT NULL"],
                ["inner_path", "TEXT"],
                ["size", "INTEGER"],
                ["size_files", "INTEGER"],
                ["size_files_optional", "INTEGER"],
                ["modified", "INTEGER"],
                ["owner_address", "TEXT NOT NULL"], # should be the same as address if not user content
            ],
            "indexes": [
                "CREATE UNIQUE INDEX content_key ON content (address, inner_path)",
                "CREATE INDEX content_modified ON content (address, modified)"
            ],
            "schema_changed": 3,
        }

        schema['tables']['size_limit'] = {
            'cols': [
                ['limit_id', 'INTEGER PRIMARY KEY UNIQUE NOT NULL'],
                ['address', 'TEXT NOT NULL'],
                ['source', 'TEXT NOT NULL'],
                ['is_private', 'INTEGER'],
                ['rule', 'TEXT NOT NULL'],
                ['value', 'INTEGER'],
                ['priority', 'INTEGER'],
            ],
            'indexes': [],
            'schema_changed': 3,
        }

        return schema

    def initSite(self, site):
        self.sites[site.address] = site

    def deleteSite(self, site):
        address = site.address
        self.execute('DELETE FROM content WHERE ?', {"address": address})
        del self.sites[address]

    def getAllSiteOwnedContentPaths(self, site):
        """"Get list of all content.json files owned by this site

        Does not include user content on the site
        """
        res = self.execute(
            'SELECT inner_path FROM content WHERE ?',
            {
                'address': site.address,
                'owner_address': site.address,
            }
        )
        return [Path(x['inner_path']) for x in res]

    def setContent(self, site, inner_path, content, size):
        """Record content.json data of a site into DB"""
        try:
            signs = content.get('signs').items()
        except Exception as exc:
            self.log.warning('Exception while setting content (see debug log for the content)')
            self.log.debug(content)
            raise(exc)
        if len(signs) < 1:
            # TODO: check what/how we should report
            raise RuntimeError('Content not signed')
        if len(signs) > 1:
            raise RuntimeError("Multi-sig not supported (and probably shouldn't on this level)")
        owner_address = list(signs)[0][0]
        self.insertOrUpdate("content", {
            "size": size,
            "size_files": sum([val["size"] for key, val in content.get("files", {}).items()]),
            "size_files_optional": sum([val["size"] for key, val in content.get("files_optional", {}).items()]),
            "modified": int(content.get("modified", 0)),
            "owner_address": owner_address,
        }, {
            "address": site.address,
            "inner_path": inner_path,
        })

    def deleteContent(self, site, inner_path):
        self.execute('DELETE FROM content WHERE ?', {
            'address': site.address,
            'inner_path': inner_path,
        })

    def loadDbDict(self, site):
        res = self.execute(
            "SELECT GROUP_CONCAT(inner_path, '|') AS inner_paths FROM content WHERE ?",
            { 'address': site.address }
        )
        row = res.fetchone()
        if row and row["inner_paths"]:
            inner_paths = row["inner_paths"].split("|")
            return dict.fromkeys(inner_paths, False)
        else:
            return {}

    def getTotalSize(self, site, ignore=None):
        params = { 'address': site.address }
        if ignore:
            params["not__inner_path"] = ignore
        res = self.execute("SELECT SUM(size) + SUM(size_files) AS size, SUM(size_files_optional) AS size_optional FROM content WHERE ?", params)
        row = dict(res.fetchone())

        if not row["size"]:
            row["size"] = 0
        if not row["size_optional"]:
            row["size_optional"] = 0

        return row["size"], row["size_optional"]

    def getAllSigners(self):
        """Returns all known public public and whether they have related site and user profile"""
        query = '''
            SELECT
                owner_address as address,
            MAX(CASE
                WHEN owner_address = address THEN 1 ELSE 0
            END) AS has_content_record,
	    MAX(CASE
                WHEN inner_path = "profile/content.json" THEN 1 ELSE 0
            END) AS has_profile_content
            FROM
                content
            GROUP BY owner_address;
        '''

        res = self.execute(query)
        return [dict(x) for x in res.fetchall()]

    def getTotalSignedSize(self, address):
        """Get total size of files signed by address"""
        params = {
            'owner_address': address,
        }
        res = self.execute('SELECT SUM(size_files) FROM content WHERE ?', params)
        return res.fetchone()[0]

    def getSizeLimitRules(self):
        """Get all size limit rules"""
        query = '''SELECT * FROM size_limit ORDER BY priority DESC'''
        res = self.execute(query)
        return [dict(x) for x in res.fetchall()]

    def getSizeLimitRulesFor(self, address):
        """Get size limit rules for a user/site"""
        query = '''SELECT * FROM size_limit WHERE address = '*' OR address = ? ORDER BY priority DESC'''
        res = self.execute(query, [address])
        return [dict(x) for x in res.fetchall()]

    def addPrivateSizeLimitRule(self, address, rule, value, priority):
        """Add private rule"""
        self.execute('''
            INSERT INTO size_limit(address, source, is_private, rule, value, priority)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', [
            address,
            '<local>',
            True,
            rule,
            value,
            priority,
        ])

    def removePrivateSizeLimitRule(self, rule_id):
        """Remove private limit size rule"""
        print(f'remove {rule_id}')
        res = self.execute('DELETE FROM size_limit WHERE limit_id = ?', (rule_id,))
        print('how so')
        print(res)

    def listModified(self, site, after=None, before=None):
        params = { 'address': site.address }
        if after:
            params["modified>"] = after
        if before:
            params["modified<"] = before
        res = self.execute("SELECT inner_path, modified FROM content WHERE ?", params)
        return {row["inner_path"]: row["modified"] for row in res}

content_dbs = {}


def getContentDb(path=None):
    if not path:
        path = config.start_dir / 'content.db'
    if path not in content_dbs:
        content_dbs[path] = ContentDb(path)
        content_dbs[path].init()
    return content_dbs[path]

getContentDb()  # Pre-connect to default one
