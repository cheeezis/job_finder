"""The listing archive keeps only listing fields and never stops a run."""

import gzip
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import run_finder
from job_finder.models import Job, JobSource, WorkflowStatus
from job_finder.persistence import listing_archive


def listing():
    return Job(
        id="arbeitnow:1",
        title="Junior Developer",
        company="Example GmbH",
        locations=["Münster"],
        sources=[JobSource("arbeitnow", "https://jobs.example.test/1")],
        description_raw="<p>Python</p>",
        description_clean="Python",
        remote_percentage=40,
        published_at=date(2026, 10, 1),
        workflow_status=WorkflowStatus.INTERESTING,
        is_new=True,
    )


class ListingArchiveTests(unittest.TestCase):
    def test_records_keep_listing_fields_without_personal_state(self):
        (record,) = listing_archive.listing_records([listing()])

        self.assertEqual(tuple(record), listing_archive.ARCHIVED_FIELDS)
        self.assertEqual(record["sources"][0]["url"], "https://jobs.example.test/1")
        self.assertEqual(record["published_at"], "2026-10-01")
        for private in ("workflow_status", "is_new", "description_raw"):
            self.assertNotIn(private, record)

    def test_without_a_container_nothing_is_written(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(listing_archive, "blob_container") as container:
            self.assertIsNone(listing_archive.archive_listings([{"id": "1"}], run_id="run"))
        container.assert_not_called()

    def test_snapshot_goes_to_the_container_by_day_without_overwriting(self):
        records = listing_archive.listing_records([listing()])
        now = datetime(2026, 10, 14, 6, 5, 9, tzinfo=UTC)
        client = MagicMock()
        environ = {"JOBFINDER_ARCHIVE_CONTAINER": "listing-archive", "JOBFINDER_RUNNER": "cloud"}
        with (
            patch.dict(os.environ, environ, clear=True),
            patch.object(listing_archive, "blob_container", return_value=client) as container,
        ):
            name, size = listing_archive.archive_listings(records, run_id="run-1", now=now)

        container.assert_called_once_with("listing-archive")
        self.assertEqual(name, "2026/10/14/060509-run-1.jsonl.gz")
        args, kwargs = client.upload_blob.call_args
        self.assertEqual((args[0], kwargs), (name, {"overwrite": False}))
        self.assertEqual(size, len(args[1]))
        (line,) = gzip.decompress(args[1]).decode("utf-8").splitlines()
        stored = json.loads(line)
        self.assertEqual(stored["title"], "Junior Developer")
        self.assertEqual((stored["run_id"], stored["runner"], stored["seen_at"]), ("run-1", "cloud", now.isoformat()))

    def test_a_failed_upload_costs_only_the_snapshot(self):
        output = io.StringIO()
        with (
            patch.object(run_finder, "archive_listings", side_effect=OSError("synthetic outage")),
            patch.object(run_finder, "log_event") as log,
            redirect_stdout(output),
        ):
            run_finder.archive_snapshot([{"id": "1"}], run_id="run-1")

        self.assertIn("Archiv: fehlgeschlagen (OSError)", output.getvalue())
        self.assertEqual(log.call_args.args[0], "listings_archive_failed")
        self.assertNotIn("synthetic outage", output.getvalue())


if __name__ == "__main__":
    unittest.main()
