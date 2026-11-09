"""vhpkcs11.py — real PKCS#11 (Cryptoki v2.40) binding over stdlib ctypes.

Closes the HSM custody residual: vhcover.pkcs11_provider expects a
module with open_session(slot) / sign(session, label, payload) /
verify(session, label, payload, signature). This module IS that
module — bound to the vendor's real shared library at deployment
(YubiHSM libyubihsm_pkcs11.so, SoftHSM libsofthsm2.so, Thales, Utimaco…
the library path is deployment configuration, never a code branch).

Doctrine:
  - An HSM that is not loaded is not an HSM: a missing library refuses
    at load, not at first signature.
  - The private key never leaves the device: we resolve a handle by
    CKA_LABEL and hand the device payloads; bytes out are signatures.
  - Every Cryptoki return code is checked; a non-OK code names itself
    (the vendor manual's code, not a guess).
  - Stdlib only (ctypes), ADR-0005.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os

VHPKCS11_VERSION = "vhpkcs11@1.0.0"

CKR_OK = 0x00000000

# Mechanisms (pkcs11t.h). The mechanism is deployment data — the device
# and key decide which one is legal.
CKM_SHA256_HMAC = 0x00000251
CKM_ECDSA = 0x00001041
CKM_RSA_PKCS = 0x00000001

CKA_LABEL = 0x00000003
CKF_SERIAL_SESSION = 0x00000004
CKF_RW_SESSION = 0x00000002
CKU_USER = 0x00000001


class Pkcs11Error(Exception):
    pass


def _refuse(msg):
    raise Pkcs11Error(msg)


class _CK_VERSION(ctypes.Structure):
    _fields_ = [("major", ctypes.c_ubyte), ("minor", ctypes.c_ubyte)]


# CK_FUNCTION_LIST (Cryptoki v2.40), in pkcs11f.h order. We bind by
# name; the layout must match the vendor library exactly.
_FP = ctypes.c_void_p


class _CK_FUNCTION_LIST(ctypes.Structure):
    _fields_ = [("version", _CK_VERSION)] + [
        (name, _FP) for name in (
            "C_Initialize", "C_Finalize", "C_GetInfo", "C_GetFunctionList",
            "C_GetSlotList", "C_GetSlotInfo", "C_GetTokenInfo",
            "C_GetMechanismList", "C_GetMechanismInfo", "C_InitToken",
            "C_InitPIN", "C_SetPIN", "C_OpenSession", "C_CloseSession",
            "C_CloseAllSessions", "C_GetSessionInfo", "C_GetOperationState",
            "C_SetOperationState", "C_Login", "C_Logout", "C_CreateObject",
            "C_CopyObject", "C_DestroyObject", "C_GetObjectSize",
            "C_GetAttributeValue", "C_SetAttributeValue",
            "C_FindObjectsInit", "C_FindObjects", "C_FindObjectsFinal",
            "C_EncryptInit", "C_Encrypt", "C_EncryptUpdate",
            "C_EncryptFinal", "C_DecryptInit", "C_Decrypt",
            "C_DecryptUpdate", "C_DecryptFinal", "C_DigestInit",
            "C_Digest", "C_DigestUpdate", "C_DigestKey", "C_DigestFinal",
            "C_SignInit", "C_Sign", "C_SignUpdate", "C_SignFinal",
            "C_SignRecoverInit", "C_SignRecover", "C_VerifyInit",
            "C_Verify", "C_VerifyUpdate", "C_VerifyFinal",
            "C_VerifyRecoverInit", "C_VerifyRecover",
            "C_DigestEncryptUpdate", "C_DecryptDigestUpdate",
            "C_SignEncryptUpdate", "C_DecryptVerifyUpdate",
            "C_GenerateKey", "C_GenerateKeyPair", "C_WrapKey",
            "C_UnwrapKey", "C_DeriveKey", "C_SeedRandom",
            "C_GenerateRandom", "C_GetFunctionStatus", "C_CancelFunction",
            "C_WaitForSlotEvent")]


class _CK_MECHANISM(ctypes.Structure):
    _fields_ = [("mechanism", ctypes.c_ulong),
                ("pParameter", ctypes.c_void_p),
                ("ulParameterLen", ctypes.c_ulong)]


class _CK_ATTRIBUTE(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong),
                ("pValue", ctypes.c_void_p),
                ("ulValueLen", ctypes.c_ulong)]


def _fn(fl, name, restype, argtypes):
    fp = getattr(fl, name)
    if not fp:
        _refuse(f"vendor library exports no {name} — custody is a "
                "contract, not a label")
    # the vtable field is a raw address (int); cast expects a ctypes
    # pointer object — casting the bare int crashes the interpreter
    f = ctypes.cast(ctypes.c_void_p(fp),
                    ctypes.CFUNCTYPE(restype, *argtypes))
    return f


class Pkcs11Module:
    """The loaded device. Exposes exactly the verbs
    vhcover.pkcs11_provider contracts for."""

    def __init__(self, library_path, *, mechanism=CKM_SHA256_HMAC,
                 pin=None):
        if not library_path or not os.path.exists(library_path):
            _refuse(f"no pkcs11 library at {library_path!r} — an HSM "
                    "that is not loaded is not an HSM")
        self.mechanism = mechanism
        self.pin = pin
        try:
            self.lib = ctypes.CDLL(library_path)
        except OSError as exc:
            _refuse(f"pkcs11 library failed to load: {exc}")
        # C_GetFunctionList must be called with a declared prototype:
        # a CDLL function invoked with byref() args but no argtypes
        # segfaults the interpreter. The out-param is held as a
        # c_void_p and only then cast — casting a bare int is the
        # other segfault. And the out variable receives a
        # CK_FUNCTION_LIST* directly (the device writes a vtable
        # pointer into it), so the cast is a SINGLE dereference:
        # casting to POINTER(POINTER(...)) and taking
        # .contents.contents reads the vtable's first 8 bytes as an
        # address — a wild pointer, and the third segfault.
        get_fl = self.lib_func("C_GetFunctionList")
        get_fl.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        fl_out = ctypes.c_void_p()
        rv = get_fl(ctypes.byref(fl_out))
        self._check(rv, "C_GetFunctionList")
        self._fl_ptr = ctypes.cast(fl_out,
                                   ctypes.POINTER(_CK_FUNCTION_LIST))
        self.fl = self._fl_ptr.contents

        ulong_p = ctypes.POINTER(ctypes.c_ulong)
        self.C_Initialize = _fn(self.fl, "C_Initialize", ctypes.c_ulong,
                                [ctypes.c_void_p])
        self.C_Finalize = _fn(self.fl, "C_Finalize", ctypes.c_ulong,
                              [ctypes.c_void_p])
        self.C_GetSlotList = _fn(self.fl, "C_GetSlotList",
                                 ctypes.c_ulong,
                                 [ctypes.c_ubyte, ulong_p, ulong_p])
        self.C_OpenSession = _fn(self.fl, "C_OpenSession", ctypes.c_ulong,
                                 [ctypes.c_ulong, ctypes.c_ulong,
                                  ctypes.c_void_p, ctypes.c_void_p,
                                  ulong_p])
        self.C_CloseSession = _fn(self.fl, "C_CloseSession",
                                  ctypes.c_ulong, [ctypes.c_ulong])
        self.C_Login = _fn(self.fl, "C_Login", ctypes.c_ulong,
                           [ctypes.c_ulong, ctypes.c_ulong,
                            ctypes.c_char_p, ctypes.c_ulong])
        self.C_FindObjectsInit = _fn(
            self.fl, "C_FindObjectsInit", ctypes.c_ulong,
            [ctypes.c_ulong, ctypes.POINTER(_CK_ATTRIBUTE),
             ctypes.c_ulong])
        self.C_FindObjects = _fn(self.fl, "C_FindObjects",
                                 ctypes.c_ulong,
                                 [ctypes.c_ulong, ulong_p,
                                  ctypes.c_ulong, ulong_p])
        self.C_FindObjectsFinal = _fn(self.fl, "C_FindObjectsFinal",
                                      ctypes.c_ulong, [ctypes.c_ulong])
        self.C_SignInit = _fn(self.fl, "C_SignInit", ctypes.c_ulong,
                              [ctypes.c_ulong,
                               ctypes.POINTER(_CK_MECHANISM),
                               ctypes.c_ulong])
        self.C_Sign = _fn(self.fl, "C_Sign", ctypes.c_ulong,
                          [ctypes.c_ulong, ctypes.c_char_p,
                           ctypes.c_ulong, ctypes.c_char_p, ulong_p])
        self.C_VerifyInit = _fn(self.fl, "C_VerifyInit", ctypes.c_ulong,
                                [ctypes.c_ulong,
                                 ctypes.POINTER(_CK_MECHANISM),
                                 ctypes.c_ulong])
        self.C_Verify = _fn(self.fl, "C_Verify", ctypes.c_ulong,
                            [ctypes.c_ulong, ctypes.c_char_p,
                             ctypes.c_ulong, ctypes.c_char_p,
                             ctypes.c_ulong])
        self._check(self.C_Initialize(None), "C_Initialize")
        self._sessions = []

    def lib_func(self, name):
        f = getattr(self.lib, name, None)
        if f is None:
            _refuse(f"vendor library exports no {name}")
        f.restype = ctypes.c_ulong
        return f

    @staticmethod
    def _check(rv, what):
        if rv != CKR_OK:
            _refuse(f"{what} returned CKR 0x{rv:08x} — the device said "
                    "no; the code is the vendor manual's, not a guess")

    def slots(self):
        count = ctypes.c_ulong(0)
        self._check(self.C_GetSlotList(1, None, ctypes.byref(count)),
                    "C_GetSlotList(count)")
        if count.value == 0:
            _refuse("no slots with a token present — the HSM is empty")
        arr = (ctypes.c_ulong * count.value)()
        self._check(self.C_GetSlotList(1, arr, ctypes.byref(count)),
                    "C_GetSlotList")
        return list(arr)

    def open_session(self, slot):
        handle = ctypes.c_ulong(0)
        flags = CKF_SERIAL_SESSION | CKF_RW_SESSION
        self._check(self.C_OpenSession(slot, flags, None, None,
                                       ctypes.byref(handle)),
                    "C_OpenSession")
        session = handle.value
        if self.pin is not None:
            self._check(self.C_Login(session, CKU_USER,
                                     self.pin.encode(), len(self.pin)),
                        "C_Login")
        self._sessions.append(session)
        return session

    def _key_handle(self, session, key_label):
        if not key_label:
            _refuse("a key without a label is a key nobody owns")
        buf = ctypes.create_string_buffer(key_label.encode())
        attr = _CK_ATTRIBUTE(CKA_LABEL,
                             ctypes.cast(buf, ctypes.c_void_p),
                             len(key_label))
        self._check(self.C_FindObjectsInit(session, ctypes.byref(attr), 1),
                    "C_FindObjectsInit")
        obj = ctypes.c_ulong(0)
        found = ctypes.c_ulong(0)
        try:
            self._check(self.C_FindObjects(session, ctypes.byref(obj), 1,
                                           ctypes.byref(found)),
                        "C_FindObjects")
        finally:
            self._check(self.C_FindObjectsFinal(session),
                        "C_FindObjectsFinal")
        if found.value == 0:
            _refuse(f"no key labelled {key_label!r} on the device — "
                    "silence about custody is how signatures get forged")
        return obj.value

    def sign(self, session, key_label, payload):
        key = self._key_handle(session, key_label)
        mech = _CK_MECHANISM(self.mechanism, None, 0)
        self._check(self.C_SignInit(session, ctypes.byref(mech), key),
                    "C_SignInit")
        siglen = ctypes.c_ulong(0)
        self._check(self.C_Sign(session, payload, len(payload),
                                None, ctypes.byref(siglen)),
                    "C_Sign(size)")
        buf = ctypes.create_string_buffer(siglen.value)
        self._check(self.C_Sign(session, payload, len(payload),
                                buf, ctypes.byref(siglen)), "C_Sign")
        return buf.raw[:siglen.value]

    def verify(self, session, key_label, payload, signature):
        key = self._key_handle(session, key_label)
        mech = _CK_MECHANISM(self.mechanism, None, 0)
        self._check(self.C_VerifyInit(session, ctypes.byref(mech), key),
                    "C_VerifyInit")
        rv = self.C_Verify(session, payload, len(payload),
                           signature, len(signature))
        return rv == CKR_OK  # CKR_SIGNATURE_INVALID is a verdict, not an error

    def close(self):
        for s in self._sessions:
            self.C_CloseSession(s)
        self._sessions = []
        self.C_Finalize(None)


def load(library_path, *, mechanism=CKM_SHA256_HMAC, pin=None):
    """The deployment door: path to the vendor's PKCS#11 library in,
    a module satisfying vhcover.pkcs11_provider's contract out."""
    return Pkcs11Module(library_path, mechanism=mechanism, pin=pin)
