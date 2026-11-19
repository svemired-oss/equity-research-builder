"""Start one protected service with a writable private Render disk."""
import os
import pwd
from pathlib import Path
import re

from equity_research.spend_budget import MODEL, daily_limit


def main():
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME","")
    if not re.fullmatch(r"[a-z0-9-]+\.onrender\.com",host):
        raise SystemExit("A Render-provided service hostname is required.")
    if os.environ.get("RESEARCH_MODEL") != MODEL:
        raise SystemExit("Configure the priced OpenAI snapshot.")
    daily_limit(os.environ.get("OPENAI_DAILY_CAP_USD"))
    for key in ("RECRUITER_PASSWORD","SEC_USER_AGENT","OPENAI_API_KEY","APCA_API_KEY_ID","APCA_API_SECRET_KEY"):
        if not os.environ.get(key,"").strip():
            raise SystemExit(f"Configure {key} in Render secrets.")
    mount = Path("/var/data")
    if not mount.is_mount():
        raise SystemExit("Mount the persistent Render disk at /var/data.")
    account = pwd.getpwnam("builder")
    for folder in (mount/"state",Path("/app/private/web-runs")):
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chown(folder,account.pw_uid,account.pw_gid)
        folder.chmod(0o700)
    os.environ["RESEARCH_STATE_DIR"]=str(mount/"state")
    os.environ["HOME"]=account.pw_dir
    os.initgroups(account.pw_name,account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    from equity_research.web_api import serve_research
    serve_research(Path("/app"),host="0.0.0.0",port=int(os.environ.get("PORT","10000")),
                   oracle=True,live_only=True)


if __name__=="__main__":
    main()

