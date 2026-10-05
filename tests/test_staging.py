"""In-memory S3 behavior: no upload without expiry; only approved derivatives and owned cleanup."""

from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from synthshop.core.config import Settings
from synthshop.core.models import Attempt
from synthshop.integrations.staging import PhotoStaging


@pytest.mark.parametrize("enabled", [False, True])
def test_expiry_gates_upload_and_cleanup_is_scoped(application, draft, enabled):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="synthshop-test")
        client.put_object(Bucket="synthshop-test", Key="unrelated.jpg", Body=b"keep")
        client.put_bucket_lifecycle_configuration(
            Bucket="synthshop-test",
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "approved-derivatives",
                        "Status": "Enabled" if enabled else "Disabled",
                        "Filter": {"Prefix": "synthshop/"},
                        "Expiration": {"Days": 1},
                    }
                ]
            },
        )
        settings = Settings(
            _env_file=None,
            r2_account_id="testaccount",
            r2_access_key_id="test",
            r2_secret_access_key="test",
            r2_bucket_name="synthshop-test",
        )
        attempt = Attempt(
            draft_id=draft.id,
            revision=draft.revision,
            correlation=f"synthshop-{draft.id}",
            fingerprint="test",
        )
        with patch("synthshop.integrations.staging.boto3.client", return_value=client):
            staging = PhotoStaging(settings)
        if not enabled:
            with pytest.raises(ValueError, match="expiry"):
                staging.stage(draft, attempt, application.store, application.photos)
            assert client.list_objects_v2(Bucket="synthshop-test")["KeyCount"] == 1
            assert application.store.attempt(draft.id) is None
            return
        staging.stage(draft, attempt, application.store, application.photos)
        persisted = application.store.attempt(draft.id)
        key = persisted.staged_keys[0]
        stored = client.get_object(Bucket="synthshop-test", Key=key)
        assert stored["ContentType"] == "image/jpeg"
        assert stored["Body"].read() == application.photos.path(draft.photos[0].id).read_bytes()
        assert client.list_objects_v2(Bucket="synthshop-test")["KeyCount"] == 2
        staging.cleanup(persisted, application.store)
        assert application.store.attempt(draft.id).staged_keys == []
        assert (
            client.list_objects_v2(Bucket="synthshop-test")["Contents"][0]["Key"] == "unrelated.jpg"
        )
