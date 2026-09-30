"""Generate ignored local Docker database credentials without displaying them."""

import secrets
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env.postgres"


def main(target=ENV_FILE):
    """Create local settings once; never replace an existing password.

    The admin URL lets `python -m job_finder.db init` create the schema right
    away; scripts/create_app_role.py later points the app URL at the limited
    role.
    """
    password = secrets.token_hex(24)
    url = f"postgresql://jobfinder:{password}@127.0.0.1:55432/jobfinder"
    try:
        with target.open("x", encoding="utf-8") as output:
            output.write(
                f"POSTGRES_PASSWORD={password}\nPOSTGRES_PORT=55432\n"
                f"JOBFINDER_DATABASE_URL={url}\n"
                f"JOBFINDER_ADMIN_DATABASE_URL={url}\n"
                f"JOBFINDER_TEST_DATABASE_URL={url}_test\n"
            )
    except FileExistsError:
        print(".env.postgres existiert bereits und bleibt unverändert.")
    else:
        print(".env.postgres angelegt. Zugangsdaten werden nicht ausgegeben.")


if __name__ == "__main__":
    main()
