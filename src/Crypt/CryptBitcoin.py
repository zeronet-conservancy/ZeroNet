import logging
import base64
import binascii
import time
import hashlib

from collections.abc import Container
from typing import Optional, Union

from util.Electrum import dbl_format
from Config import config

import util.OpensslFindPatch

lib_verify_best = "sslcrypto"

from lib import sslcrypto
sslcurve_native = sslcrypto.ecc.get_curve("secp256k1")
sslcurve_fallback = sslcrypto.fallback.ecc.get_curve("secp256k1")
sslcurve = sslcurve_native

def loadLib(lib_name, silent=False):
    global sslcurve, libsecp256k1message, lib_verify_best
    if lib_name == "libsecp256k1":
        s = time.time()
        from lib import libsecp256k1message
        import coincurve
        lib_verify_best = "libsecp256k1"
        if not silent:
            logging.info(
                "Libsecpk256k1 loaded: %s in %.3fs" %
                (type(coincurve._libsecp256k1.lib).__name__, time.time() - s)
            )
    elif lib_name == "sslcrypto":
        sslcurve = sslcurve_native
        if sslcurve_native == sslcurve_fallback:
            logging.warning("SSLCurve fallback loaded instead of native")
    elif lib_name == "sslcrypto_fallback":
        sslcurve = sslcurve_fallback

try:
    if not config.use_libsecp256k1:
        raise Exception("Disabled by config")
    loadLib("libsecp256k1")
    lib_verify_best = "libsecp256k1"
except Exception as err:
    logging.info("Libsecp256k1 load failed: %s" % err)


def newPrivatekey():  # Return new private key
    return sslcurve.private_to_wif(sslcurve.new_private_key()).decode()


def newSeed():
    return binascii.hexlify(sslcurve.new_private_key()).decode()


def hdPrivatekey(seed, child):
    # Too large child id could cause problems
    privatekey_bin = sslcurve.derive_child(seed.encode(), child % 100000000)
    return sslcurve.private_to_wif(privatekey_bin).decode()


def privatekeyToAddress(privatekey):  # Return address from private key
    try:
        if len(privatekey) == 64:
            privatekey_bin = bytes.fromhex(privatekey)
        else:
            privatekey_bin = sslcurve.wif_to_private(privatekey.encode())
        return sslcurve.private_to_address(privatekey_bin).decode()
    except Exception:  # Invalid privatekey
        return False


def sign(data: str, privatekey: str) -> str:
    """Sign data with privatekey, return base64 string signature"""
    if privatekey.startswith("23") and len(privatekey) > 52:
        return None  # Old style private key not supported
    return base64.b64encode(sslcurve.sign(
        data.encode(),
        sslcurve.wif_to_private(privatekey.encode()),
        recoverable=True,
        hash=dbl_format
    )).decode()

def get_sign_address_64(data: str, sign: str, lib_verify=None) -> Optional[str]:
    """Returns pubkey/address of signer if any"""
    if not lib_verify:
        lib_verify = lib_verify_best

    if not sign:
        return None

    if lib_verify == "libsecp256k1":
        sign_address = libsecp256k1message.recover_address(data.encode("utf8"), sign).decode("utf8")
    elif lib_verify in ("sslcrypto", "sslcrypto_fallback"):
        publickey = sslcurve.recover(base64.b64decode(sign), data.encode(), hash=dbl_format)
        sign_address = sslcurve.public_to_address(publickey).decode()
    else:
        raise Exception("No library enabled for signature verification")

    return sign_address

def verify(*args, **kwargs):
    """Default verify, see verify64"""
    return verify64(*args, **kwargs)

def verify64(data: str, addresses: Union[str, Container[str]], sign: str, lib_verify=None) -> bool:
    """Verify that sign is a valid signature for data by one of addresses

    Expecting signature to be in base64
    """
    sign_address = get_sign_address_64(data, sign, lib_verify)

    if isinstance(addresses, str):
        return sign_address == addresses
    else:
        return sign_address in addresses

def isValidAddress(addr):
    '''Check if provided address is valid bitcoin address'''
    if addr[0] != '1':
        # no support for new-style addrs
        return False
    from base58 import b58decode
    bs = b58decode(addr)
    main = bs[:-4]
    checksum = bs[-4:]
    h1 = hashlib.sha256(main).digest()
    h2 = hashlib.sha256(h1).digest()
    return h2[:4] == checksum
