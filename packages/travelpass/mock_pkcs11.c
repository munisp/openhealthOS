/* mock_pkcs11.c — declared TEST FIXTURE for vhpkcs11 ABI conformance.
 *
 * This is NOT cryptography and NOT an HSM. It is the smallest C module
 * that speaks the real Cryptoki v2.40 ABI (C_GetFunctionList → function
 * list → initialize/slots/sessions/find/sign/verify) so the test suite
 * can prove vhpkcs11.py's ctypes wiring against a compiled shared
 * object — the same path a vendor library (YubiHSM/SoftHSM/Thales)
 * takes at deployment. The "signature" is a deterministic byte
 * transform; the point under test is the ABI, not the math.
 *
 * Build (the test does this): cc -shared -fPIC -o mock_pkcs11.so mock_pkcs11.c
 */

#include <stddef.h>
#include <string.h>

typedef unsigned long CK_ULONG;
typedef unsigned char CK_BYTE;
typedef CK_ULONG CK_RV;
typedef struct { CK_BYTE major; CK_BYTE minor; } CK_VERSION;

#define CKR_OK                0x00UL
#define CKR_SIGNATURE_INVALID 0xC0UL

static CK_RV m_initialize(void *a) { (void)a; return CKR_OK; }
static CK_RV m_finalize(void *a) { (void)a; return CKR_OK; }

static CK_RV m_get_slot_list(CK_BYTE present, CK_ULONG *slots,
                             CK_ULONG *count) {
    (void)present;
    if (slots == NULL) { *count = 1; return CKR_OK; }
    if (*count < 1) { *count = 1; return 0x7UL; }
    slots[0] = 0; *count = 1; return CKR_OK;
}

static CK_RV m_open_session(CK_ULONG slot, CK_ULONG flags, void *app,
                            void *notify, CK_ULONG *session) {
    (void)slot; (void)flags; (void)app; (void)notify;
    *session = 42; return CKR_OK;
}

static CK_RV m_close_session(CK_ULONG s) { (void)s; return CKR_OK; }

static CK_RV m_login(CK_ULONG s, CK_ULONG u, CK_BYTE *p, CK_ULONG n) {
    (void)s; (void)u; (void)p; (void)n; return CKR_OK;
}

/* one labelled key, handle 7 */
static int g_find_done;

static CK_RV m_find_init(CK_ULONG s, void *templ, CK_ULONG n) {
    (void)s; (void)templ; (void)n; g_find_done = 0; return CKR_OK;
}

static CK_RV m_find(CK_ULONG s, CK_ULONG *objs, CK_ULONG max,
                    CK_ULONG *found) {
    (void)s;
    if (!g_find_done && max >= 1) {
        objs[0] = 7; *found = 1; g_find_done = 1;
    } else {
        *found = 0;
    }
    return CKR_OK;
}

static CK_RV m_find_final(CK_ULONG s) { (void)s; return CKR_OK; }

typedef struct { CK_ULONG mechanism; void *param; CK_ULONG param_len; }
    CK_MECHANISM;

static CK_RV m_sign_init(CK_ULONG s, CK_MECHANISM *m, CK_ULONG key) {
    (void)s; (void)m; (void)key; return CKR_OK;
}

#define SIG_LEN 32

static void transform(const CK_BYTE *data, CK_ULONG n, CK_BYTE *out) {
    CK_ULONG i;
    for (i = 0; i < SIG_LEN; i++)
        out[i] = (CK_BYTE)(data[i % n] ^ 0xA5 ^ (CK_BYTE)i);
}

static CK_RV m_sign(CK_ULONG s, CK_BYTE *data, CK_ULONG data_len,
                    CK_BYTE *sig, CK_ULONG *sig_len) {
    (void)s;
    if (data_len == 0) return 0x7UL;
    if (sig == NULL) { *sig_len = SIG_LEN; return CKR_OK; }
    if (*sig_len < SIG_LEN) { *sig_len = SIG_LEN; return 0x91UL; }
    transform(data, data_len, sig);
    *sig_len = SIG_LEN;
    return CKR_OK;
}

static CK_RV m_verify_init(CK_ULONG s, CK_MECHANISM *m, CK_ULONG key) {
    (void)s; (void)m; (void)key; return CKR_OK;
}

static CK_RV m_verify(CK_ULONG s, CK_BYTE *data, CK_ULONG data_len,
                      CK_BYTE *sig, CK_ULONG sig_len) {
    CK_BYTE expected[SIG_LEN];
    (void)s;
    if (data_len == 0 || sig_len != SIG_LEN) return CKR_SIGNATURE_INVALID;
    transform(data, data_len, expected);
    return memcmp(expected, sig, SIG_LEN) == 0
           ? CKR_OK : CKR_SIGNATURE_INVALID;
}

/* CK_FUNCTION_LIST (v2.40 order): version + 68 entry points. Only the
 * entries vhpkcs11 binds are populated; the rest are NULL — the loader
 * refuses a NULL verb it needs, which is itself under test. */
typedef struct { CK_VERSION version; void *fn[68]; } CK_FUNCTION_LIST;

static CK_FUNCTION_LIST g_fl = {
    {2, 40},
    {
        (void *)m_initialize,      /* 0  C_Initialize */
        (void *)m_finalize,        /* 1  C_Finalize */
        NULL,                      /* 2  C_GetInfo */
        NULL,                      /* 3  C_GetFunctionList */
        (void *)m_get_slot_list,   /* 4  C_GetSlotList */
        NULL, NULL, NULL, NULL,    /* 5-8 */
        NULL, NULL, NULL,          /* 9-11 */
        (void *)m_open_session,    /* 12 C_OpenSession */
        (void *)m_close_session,   /* 13 C_CloseSession */
        NULL, NULL, NULL, NULL,    /* 14-17 */
        (void *)m_login,           /* 18 C_Login */
        NULL, NULL, NULL, NULL,    /* 19-22 */
        NULL, NULL, NULL,          /* 23-25 */
        (void *)m_find_init,       /* 26 C_FindObjectsInit */
        (void *)m_find,            /* 27 C_FindObjects */
        (void *)m_find_final,      /* 28 C_FindObjectsFinal */
        NULL, NULL, NULL, NULL,    /* 29-32 */
        NULL, NULL, NULL, NULL,    /* 33-36 */
        NULL, NULL, NULL,          /* 37-39 */
        NULL, NULL,                /* 40-41 */
        (void *)m_sign_init,       /* 42 C_SignInit */
        (void *)m_sign,            /* 43 C_Sign */
        NULL, NULL, NULL, NULL,    /* 44-47 */
        (void *)m_verify_init,     /* 48 C_VerifyInit */
        (void *)m_verify,          /* 49 C_Verify */
        NULL, NULL, NULL, NULL,    /* 50-53 */
        NULL, NULL, NULL, NULL,    /* 54-57 */
        NULL, NULL, NULL, NULL,    /* 58-61 */
        NULL, NULL, NULL,          /* 62-64 */
        NULL, NULL, NULL           /* 65-67 */
    }
};

CK_RV C_GetFunctionList(CK_FUNCTION_LIST **pp) {
    *pp = &g_fl;
    return CKR_OK;
}
