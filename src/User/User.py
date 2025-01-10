import logging
import json
import binascii

import gevent

import util
from Crypt import CryptBitcoin
from Plugin import PluginManager
from Config import config
from util import helper
from Debug import Debug


@PluginManager.acceptPlugins
class User:
    """Represents user (which may have more than one key pair/nick/address)"""
    def __init__(self, master_address=None, master_seed=None, data={}):
        if master_seed:
            self.master_seed = master_seed
            self.master_address = CryptBitcoin.privatekeyToAddress(self.master_seed)
        elif master_address:
            self.master_address = master_address
            self.master_seed = data.get("master_seed")
        else:
            self.master_seed = CryptBitcoin.newSeed()
            self.master_address = CryptBitcoin.privatekeyToAddress(self.master_seed)
        self.accounts = self.loadAccounts(data)
        self.sites = self.loadSites(data)
        self.settings = data.get("settings", {})
        self.delayed_save_thread = None
        self.log = logging.getLogger(f"User:{self.master_address}")

    def loadSites(self, data):
        """Load sites together with their user settings"""
        sites = data.get('sites', {})
        for address, site in sites.items():
            if cert_issuer := site.get('cert'):
                del site['cert']
                if acc := self.getAccForCert(cert_issuer):
                    site['account'] = acc['auth_address']
        return sites

    def getAccForCert(self, issuer):
        """Get account associated with cert issuer

        Note that this doesn't resolve among multiple possible accounts in any meaningful
        way, so avoid using this in new code.
        """
        certs = [x for x in self.accounts if x['cert_issuer'] == issuer]
        if certs:
            return certs[0]
        return None

    def loadAccounts(self, data):
        """Load accounts (key pairs)"""
        certs_raw = data.get('certs')
        accounts = data.get('accounts', [])
        addrs = [acc['auth_address'] for acc in accounts]
        ads = []
        acs = []
        for acc in accounts:
            if acc['auth_address'] not in ads:
                acs.append(acc)
                ads.append(acc['auth_address'])
        accounts = acs
        if certs_raw:
            accounts += [
                {
                    'cert_issuer': issuer,
                    **acc,
                }
                for issuer, acc in certs_raw.items()
                if acc['auth_address'] not in addrs
            ]
        return accounts

    @util.Noparallel(queue=True, ignore_class=True)
    def save(self):
        """Save to data/private/users.json"""
        users_json = config.private_dir / 'users.json'
        users = json.load(open(users_json))
        if self.master_address not in users:
            users[self.master_address] = {}  # Create if not exist
        user_data = users[self.master_address]
        if self.master_seed:
            user_data['master_seed'] = self.master_seed
        if 'certs' in user_data:
            del user_data['certs']
        user_data['sites'] = self.sites
        user_data['accounts'] = self.accounts
        user_data['settings'] = self.settings
        helper.atomicWrite(users_json, helper.jsonDumps(users).encode("utf8"))
        self.delayed_save_thread = None

    def saveDelayed(self):
        if not self.delayed_save_thread:
            self.delayed_save_thread = gevent.spawn_later(5, self.save)

    def getAddressAuthIndex(self, address):
        return int(binascii.hexlify(address.encode()), 16)

    @util.Noparallel()
    def generateAuthAddress(self, address):
        address_id = self.getAddressAuthIndex(address)  # Convert site address to int
        auth_privatekey = CryptBitcoin.hdPrivatekey(self.master_seed, address_id)
        self.sites[address] = {
            "auth_address": CryptBitcoin.privatekeyToAddress(auth_privatekey),
            "auth_privatekey": auth_privatekey
        }
        self.saveDelayed()
        return self.sites[address]

    def getSiteData(self, address, create=True):
        """Get user site data

        Returns {"auth_address": "xxx", "auth_privatekey": "xxx", ...}
        Never returns None
        """
        if address not in self.sites:  # Generate new BIP32 child key based on site address
            if not create:
                return {"auth_address": None, "auth_privatekey": None}  # Dont create user yet
            self.generateAuthAddress(address)
        return self.sites[address]

    def deleteSiteData(self, address):
        if address in self.sites:
            del(self.sites[address])
            self.saveDelayed()
            self.log.debug("Deleted site: %s" % address)

    def setSiteSettings(self, address, settings):
        site_data = self.getSiteData(address)
        site_data["settings"] = settings
        self.saveDelayed()
        return site_data

    # Get data for a new, unique site
    # Return: [site_address, bip32_index, {"auth_address": "xxx", "auth_privatekey": "xxx", "privatekey": "xxx"}]
    def getNewSiteData(self):
        import random
        bip32_index = random.randrange(2 ** 256) % 100000000
        site_privatekey = CryptBitcoin.hdPrivatekey(self.master_seed, bip32_index)
        site_address = CryptBitcoin.privatekeyToAddress(site_privatekey)
        if site_address in self.sites:
            raise Exception("Random error: site exist!")
        # Save to sites
        self.getSiteData(site_address)
        self.sites[site_address]["privatekey"] = site_privatekey
        self.save()
        return site_address, bip32_index, self.sites[site_address]

    # Get BIP32 address from site address
    # Return: BIP32 auth address
    def getAuthAddress(self, address, create=True):
        cert = self.getCert(address)
        if cert:
            return cert["auth_address"]
        else:
            return self.getSiteData(address, create)["auth_address"]

    def getAuthPrivatekey(self, address, create=True):
        cert = self.getCert(address)
        if cert:
            return cert["auth_privatekey"]
        else:
            return self.getSiteData(address, create)["auth_privatekey"]

    def addCert(self, auth_address, domain, auth_type, auth_user_name, cert_sign):
        """Add cert for the user"""
        # Find privatekey by auth address
        auth_privatekey = [site['auth_privatekey'] for site in self.sites.values() if site['auth_address'] == auth_address][0]
        cert = {
            'cert_issuer': domain,
            "auth_address": auth_address,
            "auth_privatekey": auth_privatekey,
            "auth_type": auth_type,
            "auth_user_name": auth_user_name,
            "cert_sign": cert_sign
        }
        if cert in self.accounts:
            return None
        else:
            self.accounts.push(cert)
            self.save()
            return True

    def setCert(self, address, domain):
        """Set active cert for a site"""
        self.log.warning('certSet called')
        site_data = self.getSiteData(address)
        if domain:
            site_data['account'] = self.getAccForCert(domain)['auth_address']
        else:
            if "cert" in site_data:
                del site_data["cert"]
        self.saveDelayed()
        return site_data

    def getCert(self, address):
        """Get selected cert for the site address

        Returns { "auth_address":.., "auth_privatekey":.., "auth_type": "web", "auth_user_name": "nofish", "cert_sign":.. } or None
        """
        site_data = self.getSiteData(address, create=False)
        acc = site_data.get('account')
        if not acc:
            return None
        certs = [x for x in self.accounts if x['auth_address'] == acc]
        if certs:
            return certs[0]
        return None

    def getCertUserId(self, address):
        """Get cert user name for the site address

        Returns user@certprovider.bit or None
        """
        cert = self.getCert(address)
        if not cert:
            return None
        return f"{cert['auth_user_name']}@{cert['cert_issuer']}"
