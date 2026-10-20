# Two-factor codes and encrypted backups. pyotp does the TOTP maths, cryptography does AES-GCM.
import base64
import hashlib
import hmac
import io
import secrets
import tarfile
import time

import pyotp
import qrcode
import qrcode.image.svg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ISSUER = "Miso Chat"
TOTP_WINDOW = 1                   # accept the codes just before and after now (30 seconds of clock drift)
BACKUP_MAGIC = b"MISOBK1\n"
BACKUP_SCRYPT = (2 ** 15, 8, 1)   # backups are rare, so a slower derivation is fine
SCRYPT_MAXMEM = 128 * 1024 * 1024  # OpenSSL's default 32 MB cap is too small for these costs


class SecureError(ValueError):
    pass


# ---- server key: encrypts 2FA secrets at rest. It lives in .env. A backup includes .env, so the
# backup password protects the key too.

def server_key(env_value):
    if not env_value:
        raise SecureError("MISO_SECRET_KEY is not set in .env, so two-factor secrets cannot be encrypted.")
    try:
        raw = base64.urlsafe_b64decode(env_value.encode("ascii") + b"=" * (-len(env_value) % 4))
    except ValueError:
        raw = b""
    if len(raw) != 32:
        raise SecureError("MISO_SECRET_KEY must be 32 random bytes, base64 encoded.")
    return raw


def new_server_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def seal(key, plaintext, aad=b""):
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)).decode("ascii")


def open_sealed(key, token, aad=b""):
    try:
        raw = base64.b64decode(token.encode("ascii"))
        return AESGCM(key).decrypt(raw[:12], raw[12:], aad).decode("utf-8")
    except (ValueError, InvalidTag):
        raise SecureError("The sealed value could not be opened.") from None


# ---- TOTP

def new_totp_secret():
    return pyotp.random_base32()


def provisioning_uri(secret, username):
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def qr_svg(uri):
    # SVG, so no image library is needed; the page embeds it directly.
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def check_code(secret, code, last_step=None):
    # Returns (ok, step). A step that was already used is refused, so each code works once.
    code = (code or "").replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return False, last_step
    totp = pyotp.TOTP(secret)
    now = time.time()
    for drift in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        t = now + drift * totp.interval
        if hmac.compare_digest(totp.at(t), code):
            step = int(t // totp.interval)
            if last_step is not None and step <= last_step:
                return False, last_step
            return True, step
    return False, last_step


# ---- backups: a tar archive, encrypted with a key derived from the backup password

def derive_backup_key(password, salt):
    n, r, p = BACKUP_SCRYPT
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=SCRYPT_MAXMEM)


def backup_verifier(password):
    # Stored once, so the backup password can be checked without storing it.
    salt = secrets.token_bytes(16)
    return base64.b64encode(salt + derive_backup_key(password, salt)).decode("ascii")


def check_backup_verifier(password, verifier):
    raw = base64.b64decode(verifier.encode("ascii"))
    salt, want = raw[:16], raw[16:]
    return hmac.compare_digest(derive_backup_key(password, salt), want)


def encrypt_archive(password, entries, manifest):
    # entries: [(arcname, bytes or a file path)]. manifest: text stored as MANIFEST.json, first in the archive.
    # Output: magic, salt, nonce, then the ciphertext of the tar archive.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        mdata = manifest.encode("utf-8")
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(mdata)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(mdata))
        for name, src in entries:
            if isinstance(src, (bytes, bytearray)):
                info = tarfile.TarInfo(name)
                info.size = len(src)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(bytes(src)))
            else:
                tar.add(src, arcname=name, recursive=False)
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    body = AESGCM(derive_backup_key(password, salt)).encrypt(nonce, buf.getvalue(), BACKUP_MAGIC)
    return BACKUP_MAGIC + salt + nonce + body


def decrypt_archive(password, blob):
    if not blob.startswith(BACKUP_MAGIC):
        raise SecureError("This is not a Miso Chat backup.")
    off = len(BACKUP_MAGIC)
    salt, nonce, body = blob[off:off + 16], blob[off + 16:off + 28], blob[off + 28:]
    try:
        plain = AESGCM(derive_backup_key(password, salt)).decrypt(nonce, body, BACKUP_MAGIC)
    except InvalidTag:
        raise SecureError("Wrong backup password, or the file was changed.") from None
    out = {}
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r") as tar:
        for member in tar.getmembers():
            if member.isfile():
                out[member.name] = tar.extractfile(member).read()
    return out


def new_backup_password_ok(password):
    # 12 characters: a backup is only as strong as its password, and scrypt can't rescue a short one.
    return isinstance(password, str) and len(password) >= 12
