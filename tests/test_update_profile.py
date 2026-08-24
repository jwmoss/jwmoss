"""Tests for the profile README updater."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts import update_profile


class MergedExternalPullRequestsTest(unittest.TestCase):
    """Verify that the updater publishes only public contributions."""

    @patch.object(update_profile, "search_merged_pr_nodes")
    def test_excludes_private_repositories(self, search) -> None:
        search.return_value = [
            {
                "mergedAt": "2026-08-23T12:00:00Z",
                "repository": {
                    "nameWithOwner": "example/private-repo",
                    "isPrivate": True,
                },
            }
        ]

        result = update_profile.merged_external_prs(
            datetime(2026, 8, 1, tzinfo=timezone.utc)
        )

        self.assertEqual({}, result)


if __name__ == "__main__":
    unittest.main()
