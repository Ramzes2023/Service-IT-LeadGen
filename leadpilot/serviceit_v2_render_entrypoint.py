"""Fail-closed Render entry point for independent ServiceIT Gmail intake."""
import base64
import binascii
import os
from pathlib import Path
import tempfile

MAILBOX = "ramzesisayev444@gmail.com"


def configure():
    enabled = os.environ.get("SERVICEIT_V2_APPS_RECEIVER_ENABLED", "NO")
    if enabled not in ("NO", "YES"):
        raise ValueError("INVALID_SWITCH")
    if enabled == "NO":
        os.environ["SERVICEIT_V2_APPS_RECEIVER_ENABLED"] = "NO"
        return "OFF"
    if os.environ.get("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL") != MAILBOX:
        raise ValueError("INCORRECT_MAILBOX")
    if not os.environ.get("SERVICEIT_V2_DB_DSN"):
        db = os.environ.get("DATABASE_URL", "")
        if not db.startswith(("postgresql://", "postgres://")):
            raise ValueError("POSTGRES_NOT_CONFIGURED")
        os.environ["SERVICEIT_V2_DB_DSN"] = db
    encoded = os.environ.get("SERVICEIT_V2_APPS_HMAC_SECRET_B64", "")
    file = os.environ.get("SERVICEIT_V2_APPS_HMAC_SECRET_FILE", "")
    if bool(encoded) == bool(file):
        raise ValueError("HMAC_SOURCE_NOT_UNIQUE")
    if encoded:
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("INVALID_HMAC_ENCODING") from None
        if not (32 <= len(key) <= 512) or key.strip() != key or b"\r" in key or b"\n" in key:
            raise ValueError("INVALID_HMAC_KEY")
        old = os.umask(0o077)
        try:
            fd, path = tempfile.mkstemp(prefix="serviceit-hmac-")
            with os.fdopen(fd, "wb") as f:
                f.write(key)
            os.chmod(path, 0o600)
        finally:
            os.umask(old)
        os.environ["SERVICEIT_V2_APPS_HMAC_SECRET_FILE"] = path
        os.environ.pop("SERVICEIT_V2_APPS_HMAC_SECRET_B64", None)
    elif not Path(file).is_file() or Path(file).is_symlink():
        raise ValueError("HMAC_FILE_NOT_PRIVATE")
    return "CONFIGURED"


def main():
    try:
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError("INVALID_PORT")
        status = configure()
    except (ValueError, TypeError) as exc:
        print("SERVICEIT_V2_PREFLIGHT=BLOCKED:" + str(exc), flush=True)
        raise SystemExit(2) from None
    print("SERVICEIT_V2_PREFLIGHT=" + status, flush=True)
    print("SERVICEIT_V2_CRM_WRITES=0", flush=True)
    import uvicorn
    uvicorn.run("leadpilot.serviceit_v2_apps_receiver:app",
                host="0.0.0.0", port=port, access_log=False)


if __name__ == "__main__":
    main()
