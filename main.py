import gui
from gui import screens, popups
from gui.decorators import queued
import gui.common

import utime as time
import os
import ujson as json
from ubinascii import hexlify, unhexlify
# base64 encoding
from ubinascii import a2b_base64, b2a_base64

from bitcoin import ec, hashes, bip39, bip32
from bitcoin.networks import NETWORKS
from bitcoin import psbt
from keystore import KeyStore

from qrscanner import QRScanner
from rng import get_random_bytes

qr_scanner = QRScanner()

# entropy that will be converted to mnemonic
entropy = None
# network we are using
network = None
# our key storage
keystore = KeyStore()

# detect if it's a hardware device or linuxport
try:
    import pyb
    simulator = False
except:
    simulator = True

# path to store #reckless entropy
if simulator:
    reckless_fname = "reckless.json"
else:
    reckless_fname = "/flash/reckless.json"

DEFAULT_XPUBS = []

def cancel_scan():
    print("Cancel scan!")
    qr_scanner.stop()
    show_main()

def select_wallet(w):
    popups.show_wallet(w)

def wallets_menu():
    buttons = []
    def wrapper(w):
        def cb():
            select_wallet(w)
        return cb
    for wallet in keystore.wallets:
        buttons.append((wallet.name, wrapper(wallet)))
    gui.create_menu(buttons=buttons, cb_back=show_main, title="Select the wallet")

def show_xpub(name, derivation):
    xpub = keystore.get_xpub(derivation).to_base58()
    fingerprint = hexlify(keystore.fingerprint).decode('utf-8')
    prefix = "[%s%s]" % (fingerprint, derivation[1:])
    popups.show_xpub(name, xpub, prefix=prefix)

def xpubs_menu():
    def selector(name, derivation):
        def cb():
            show_xpub(name, derivation)
        return cb
    buttons = []
    for name, derivation in DEFAULT_XPUBS:
        buttons.append((name, selector(name, derivation)))
    gui.create_menu(buttons=buttons, cb_back=show_main, title="Select the master key")

def sign_psbt(wallet=None, tx=None):
    keystore.sign(tx)
    # remove everything but partial sigs
    # to reduce QR code size
    tx.unknown = {}
    tx.xpubs = {}
    for i in range(len(tx.inputs)):
        tx.inputs[i].unknown = {}
        tx.inputs[i].non_witness_utxo = None
        tx.inputs[i].witness_utxo = None
        tx.inputs[i].sighash_type = None
        tx.inputs[i].bip32_derivations = {}
        tx.inputs[i].witness_script = None
        tx.inputs[i].redeem_script = None
    for i in range(len(tx.outputs)):
        tx.outputs[i].unknown = {}
        tx.outputs[i].bip32_derivations = {}
        tx.outputs[i].witness_script = None
        tx.outputs[i].redeem_script = None
    b64_tx = b2a_base64(tx.serialize()).decode('utf-8')
    if b64_tx[-1:] == "\n":
        b64_tx = b64_tx[:-1]
    popups.qr_alert("Signed transaction:", b64_tx, "Scan it with your software wallet")

def parse_transaction(b64_tx):
    # we will go to main afterwards
    show_main()
    # we need to update gui because screens are queued
    gui.update(100)
    try:
        raw = a2b_base64(b64_tx)
        tx = psbt.PSBT.parse(raw)
    except:
        gui.error("Failed at transaction parsing")
        return
    try:
        data = keystore.check_psbt(tx)
    except Exception as e:
        gui.error("Problem with the transaction: %r" % e)
        return
    title = "Spending %u\nfrom %s" % (data["spending"], data["wallet"].name)
    message = ""
    for out in data["send_outputs"]:
        message += "%u sat to %s\n" % (out["value"], out["address"])
    message += "\nFee: %u satoshi" % data["fee"]
    popups.prompt(title, message, ok=sign_psbt, wallet=data["wallet"], tx=tx)

def scan_transaction():
    screens.show_progress("Scan transaction to sign", "Scanning.. Click \"Cancel\" to stop.", callback=cancel_scan)
    gui.update(30)
    qr_scanner.start_scan(parse_transaction)

def verify_address(s):
    # we will go to main afterwards
    show_main()
    # we need to update gui because screens are queued
    gui.update(100)
    # verifies address in the form [bitcoin:]addr?index=i
    s = s.replace("bitcoin:", "")
    arr = s.split("?")
    index = None
    addr = None
    # check that ?index= is there
    if len(arr) > 1:
        addr = arr[0]
        meta_arr = arr[1].split("&")
        # search for `index=`
        for meta in meta_arr:
            if meta.startswith("index="):
                try:
                    index = int(meta.split("=")[1])
                except:
                    gui.error("Index is not an integer...")
                    return
    if index is None or addr is None:
        # where we will go next
        gui.error("No derivation index in the address metadata - can't verify.")
        return
    for w in keystore.wallets:
        if w.address(index) == addr:
            popups.qr_alert("Address #%d from wallet\n\"%s\"" % (index+1, w.name), addr, message_text=addr)
            return
    gui.error("Address doesn't belong to any wallet. Wrong device or network?")

def scan_address():
    screens.show_progress("Scan address to verify", "Scanning.. Click \"Cancel\" to stop.", callback=cancel_scan)
    gui.update(30)
    qr_scanner.start_scan(verify_address)

def set_default_xpubs(net):
    while len(DEFAULT_XPUBS) > 0:
        DEFAULT_XPUBS.pop()
    DEFAULT_XPUBS.append(("Single key", "m/84h/%dh/0h" % network["bip32"]))
    DEFAULT_XPUBS.append(("Miltisig", "m/48h/%dh/0h/2h" % network["bip32"]))

def select_network(name):
    global network
    if name in NETWORKS:
        network = NETWORKS[name]
        if keystore.is_initialized:
            set_default_xpubs(network)
            # load existing wallets for this network
            keystore.load_wallets(name)
            # create a default wallet if it doesn't exist
            if len(keystore.wallets) == 0:
                # create a wallet descriptor
                # this is not exactly compatible with Bitcoin Core though.
                # '_' means 0/* or 1/* - standard receive and change 
                #                        derivation patterns
                derivation = DEFAULT_XPUBS[0][1]
                xpub = keystore.get_xpub(derivation).to_base58()
                fingerprint = hexlify(keystore.fingerprint).decode('utf-8')
                prefix = "[%s%s]" % (fingerprint, derivation[1:])
                descriptor = "wpkh(%s%s/_)" % (prefix, xpub)
                keystore.create_wallet("Default", descriptor)
    else:
        raise RuntimeError("Unknown network")

def network_menu():
    def selector(name):
        def cb():
            try:
                select_network(name)
                show_main()
            except Exception as e:
                print(e)
                gui.error("%r" % e)
        return cb
    # could be done with iterator
    # but order is unknown then
    gui.create_menu(buttons=[
        ("Mainnet", selector("main")),
        ("Testnet", selector("test")),
        ("Regtest", selector("regtest")),
        ("Signet", selector("signet"))
    ], title="Select the network")


def show_mnemonic():
    # print(bip39.mnemonic_from_bytes(entropy))
    popups.show_mnemonic(bip39.mnemonic_from_bytes(entropy))


def save_entropy():
    with open(reckless_fname, "w") as f:
        f.write('{"entropy":"%s"}' % hexlify(entropy).decode('utf-8'))
    with open(reckless_fname, "r") as f:
        d = json.loads(f.read())
    if "entropy" in d  and d["entropy"] == hexlify(entropy).decode('utf-8'):
        gui.alert("Success!", "Your key is saved in the memory now")
    else:
        gui.error("Something went wrong")

def delete_entropy():
    try:
        os.remove(reckless_fname)
        gui.alert("Success!", "Your key is deleted")
    except:
        gui.error("Failed to delete the key")

def reckless_menu():
    gui.create_menu(buttons=[
        ("Show recovery phrase", show_mnemonic),
        ("Save key to memory", save_entropy),
        ("Delete key from memory", delete_entropy)
        ], cb_back=show_main,title="Careful. Think twice.")


def show_main():
    gui.create_menu(buttons=[
        ("Wallets", wallets_menu),
        ("Master keys", xpubs_menu),
        ("Sign transaction", scan_transaction),
        ("Verify address", scan_address),
        ("Use another password", ask_for_password),
        ("Switch network (%s)" % network["name"], network_menu),
        ("# Reckless", reckless_menu)
        ])

def get_new_mnemonic(words=12):
    entropy_len = words*4//3
    global entropy
    entropy = get_random_bytes(entropy_len)
    return bip39.mnemonic_from_bytes(entropy)

def gen_new_key(words=12):
    mnemonic = get_new_mnemonic(words)
    screens.new_mnemonic(mnemonic, cb_continue=ask_for_password, cb_back=show_init, cb_update=get_new_mnemonic)

def recover_key():
    screens.ask_for_mnemonic(cb_continue=mnemonic_entered, cb_back=show_init, check_mnemonic=bip39.mnemonic_is_valid, words_lookup=bip39.find_candidates)

def mnemonic_entered(mnemonic):
    global entropy
    entropy = bip39.mnemonic_to_bytes(mnemonic)
    ask_for_password()

def load_key():
    global entropy
    try:
        with open(reckless_fname, "r") as f:
            d = json.loads(f.read())
        entropy = unhexlify(d["entropy"])
        ask_for_password()
    except:
        gui.error("Something went wrong, sorry")

def show_init():
    buttons = [
        ("Generate new key", gen_new_key),
        ("Enter recovery phrase", recover_key)
    ]
    # check if reckless.json file exists
    # os.path is not implemented in micropython :(
    try:
        with open(reckless_fname,"r") as f:
            c = f.read()
            if len(c) == 0:
                raise RuntimeError("File is empty")
        # if ok - add an extra button
        buttons.append(("Load key from memory", load_key))
    except:
        pass
    screens.create_menu(buttons=buttons)

def ask_for_password():
    screens.ask_for_password(init_keys)

def init_keys(password):
    mnemonic = bip39.mnemonic_from_bytes(entropy)
    seed = bip39.mnemonic_to_seed(mnemonic, password)
    keystore.load_seed(seed)
    # choose testnet by default
    select_network("test")
    show_main()

def main(blocking=True):
    gui.init()
    show_init()
    if blocking:
        while True:
            time.sleep_ms(30)
            gui.update(30)
            qr_scanner.update()

if __name__ == '__main__':
    main()