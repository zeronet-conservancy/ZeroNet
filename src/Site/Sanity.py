"""Check sanity of a site and/or fix it"""

from dataclasses import dataclass
from typing import List, Tuple, Optional
from enum import Enum
import json

from Content import ContentDb
from Crypt.CryptBitcoin import isValidAddress

BadUserPermissionList = List[Tuple[str, str]]

@dataclass
class CheckSiteResult:
    bad_user_permissions: BadUserPermissionList

def checkSite(site) -> CheckSiteResult:
    """Check for sanity of a site"""
    bad_user_permissions = checkUserPermissionsAddresses(site)
    return CheckSiteResult(
        bad_user_permissions = bad_user_permissions,
    )

def checkUserPermissionsAddresses(site) -> BadUserPermissionList:
    cdb = ContentDb.getContentDb()
    res = []
    for inner_path in cdb.getAllSiteOwnedContentPaths(site):
        with site.storage.open(inner_path) as content_f:
            contents = json.load(content_f)
            user_permissions = contents.get('user_contents', {}).get('permissions')
            if user_permissions:
                for user_address in user_permissions:
                    address_result = checkAddress(user_address)
                    if address_result.bad:
                        res.append((
                            user_address,
                            address_result.error,
                        ))
    return res

@dataclass
class CheckAddressResult:
    bad: bool
    error: Optional[str] = None

def checkAddress(address) -> CheckAddressResult:
    if isValidAddress(address):
        return CheckAddressResult(bad = False)
    if '@' in address:
        return CheckAddressResult(bad = True, error = "non-unique")
    return CheckAddressResult(bad = True, error = "not-a-key")
