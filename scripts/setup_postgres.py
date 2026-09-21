"""Generate ignored local Docker database credentials without displaying them."""

import secrets
from pathlib import Path


def main():
    """Create local settings once; never replace an existing password."""
    target = Path(__file__).resolve().parents[1] / ".env.postgres"
    password = secrets.token_hex(24)
    try:
        with target.open("x", encoding="utf-8") as output:
            output.write(
                f"POSTGRES_PASSWORD={password}\nPOSTGRES_PORT=55432\n"
                f"JOBFINDER_DATABASE_URL=postgresql://jobfinder:{password}@127.0.0.1:55432/jobfinder\n"
                f"JOBFINDER_TEST_DATABASE_URL=postgresql://jobfinder:{password}@127.0.0.1:55432/jobfinder_test\n"
            )
    except FileExistsError:
        print(".env.postgres existiert bereits und bleibt unverändert.")
    else:
        print(".env.postgres angelegt. Zugangsdaten werden nicht ausgegeben.")


if __name__ == "__main__":
    main()
