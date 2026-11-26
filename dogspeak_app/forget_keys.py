"""Remove every API key Dogspeak saved in Windows Credential Manager.

The uninstaller runs this only when the person asks it to delete their data:
    python.exe -m dogspeak_app.forget_keys
The keyring package names entries "Dogspeak" (the first key) or
"<address>@Dogspeak" (later ones); nothing else is touched.
"""
from .credentials_keyring import SERVICE


def ours(target):
    return target == SERVICE or target.endswith("@" + SERVICE)


def windows_credentials():
    """(list_targets, delete) over the generic credentials of the signed-in user."""
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.c_void_p), ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredEnumerateW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD),
                                        ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(CREDENTIAL))))
    advapi32.CredDeleteW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
    advapi32.CredFree.argtypes = (ctypes.c_void_p,)
    generic = 1  # CRED_TYPE_GENERIC

    def list_targets():
        count = wintypes.DWORD()
        found = ctypes.POINTER(ctypes.POINTER(CREDENTIAL))()
        if not advapi32.CredEnumerateW(None, 0, ctypes.byref(count), ctypes.byref(found)):
            return []  # ERROR_NOT_FOUND: no credentials at all
        try:
            return [found[i].contents.TargetName for i in range(count.value)
                    if found[i].contents.Type == generic]
        finally:
            advapi32.CredFree(found)

    def delete(target):
        return bool(advapi32.CredDeleteW(target, generic, 0))

    return list_targets, delete


def forget(list_targets, delete):
    removed = []
    for target in list_targets():
        if ours(target) and delete(target):
            removed.append(target)
    return removed


def main():
    removed = forget(*windows_credentials())
    print(f"Removed {len(removed)} saved API key(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
