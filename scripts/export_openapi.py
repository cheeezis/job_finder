"""Write the review's OpenAPI schema as JSON, without a server or database.

npm run types:api turns it into job_finder/api-types.d.ts, the types the
browser code checks its payloads against; CI fails when the two drift apart.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_finder.review_app import create_app  # noqa: E402


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    schema = json.dumps(create_app(route_origin="").openapi(), ensure_ascii=False, indent=2, sort_keys=True)
    if target is None:
        print(schema)
    else:
        target.write_text(schema + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
