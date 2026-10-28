"""Offline safety tests: no live R2, Django ORM, or production credentials."""

from contextlib import redirect_stderr, redirect_stdout
import copy
from io import BytesIO, StringIO
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from scripts import check_r2_connection as probe


def values():
    return dict(zip(probe.VARIABLES, (
        "0", "10485760", "a" * 32, "tambul-portal-media",
        "fake-access-key-for-offline-tests", "fake-secret-key-for-offline-tests",
    )))


def missing():
    return ClientError({
        "Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404},
    }, "HeadObject")


class MemoryClient:
    """Model only single-object operations; existing documents must survive."""
    def __init__(self):
        self.objects = {"minutes/existing.pdf": {"Body": b"existing production PDF"}}
        self.calls = []
        self.closed = False

    def put_object(self, **kwargs):
        self.calls.append(("put", kwargs))
        key = kwargs["Key"]
        if key in self.objects:
            raise ClientError({
                "Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412},
            }, "PutObject")
        self.objects[key] = copy.deepcopy(kwargs)
        return {}

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        if kwargs["Key"] not in self.objects:
            raise missing()
        item = self.objects[kwargs["Key"]]
        return {
            "ContentLength": len(item["Body"]), "Metadata": item.get("Metadata", {}),
            "CacheControl": item.get("CacheControl"),
        }

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"Body": BytesIO(self.objects[kwargs["Key"]]["Body"])}

    def delete_object(self, **kwargs):
        self.calls.append(("delete", kwargs))
        del self.objects[kwargs["Key"]]
        return {}

    def close(self):
        self.closed = True


class R2ProbeTests(unittest.TestCase):
    def setUp(self):
        self.config = probe.configuration(values())
        self.client = MemoryClient()
        self.output = []

    def run_probe(self, status=403):
        return probe.run_probe(
            self.client, self.config, emit=self.output.append,
            check_anonymous=lambda url, data: probe.AnonymousResult(status),
        )

    def assert_only_existing_document_remains(self):
        self.assertEqual(self.client.objects, {"minutes/existing.pdf": {"Body": b"existing production PDF"}})
        for operation, kwargs in self.client.calls:
            self.assertTrue(kwargs["Key"].startswith(probe.PREFIX))
            self.assertEqual(kwargs["Bucket"], probe.EXPECTED_BUCKET)

    def test_round_trip_requires_conditional_put_private_headers_and_removes_only_test_key(self):
        check_anonymous = Mock(return_value=probe.AnonymousResult(403))
        probe.run_probe(self.client, self.config, emit=self.output.append, check_anonymous=check_anonymous)
        self.assert_only_existing_document_remains()
        put = self.client.calls[0][1]
        self.assertEqual(put["IfNoneMatch"], "*")
        self.assertEqual(put["CacheControl"], "private, no-store")
        self.assertNotIn("ACL", put)
        self.assertEqual([kind for kind, _ in self.client.calls], ["put", "head", "get", "head", "head", "delete", "head"])
        url = check_anonymous.call_args.args[0]
        self.assertNotIn("?", url)
        self.assertNotIn(self.config.access_key, url)
        self.assertNotIn(self.config.secret_key, url)
        self.assertEqual(url, self.config.object_url(put["Key"]))
        self.assertEqual(check_anonymous.call_args.args[1], put["Body"])

    def test_every_variable_is_required_without_exposing_any_value(self):
        for name in probe.VARIABLES:
            with self.subTest(name=name):
                configured = values()
                del configured[name]
                with self.assertRaisesRegex(probe.ProbeError, name):
                    probe.configuration(configured)

    def test_disabled_application_upload_flag_is_required(self):
        for value in ("1", "true", "yes", "on", "unexpected"):
            with self.subTest(value=value):
                configured = values()
                configured["MINUTES_R2_UPLOADS_ENABLED"] = value
                with self.assertRaises(probe.ProbeError):
                    probe.configuration(configured)

    def test_wrong_bucket_bad_account_and_invalid_limits_are_rejected(self):
        for name, bad in (
            ("MINUTES_R2_BUCKET_NAME", "other-bucket"),
            ("MINUTES_R2_ACCOUNT_ID", "https://wrong-endpoint"),
            ("MINUTES_MAX_UPLOAD_BYTES", "-1"),
            ("MINUTES_MAX_UPLOAD_BYTES", "not-an-integer"),
        ):
            configured = values()
            configured[name] = bad
            with self.subTest(name=name), self.assertRaises(probe.ProbeError):
                probe.configuration(configured)

    def test_too_small_limit_prevents_any_object_access(self):
        configured = values()
        configured["MINUTES_MAX_UPLOAD_BYTES"] = "1"
        with self.assertRaises(probe.ProbeError):
            probe.run_probe(self.client, probe.configuration(configured), emit=self.output.append)
        self.assertFalse(self.client.calls)

    def test_public_access_redirects_and_inconclusive_statuses_fail_then_cleanup(self):
        for status in (200, 206, 301, 302, 404, 500):
            with self.subTest(status=status):
                self.client = MemoryClient()
                with self.assertRaisesRegex(probe.ProbeError, "Anonymous S3 GET|SECURITY CHECK FAILED"):
                    self.run_probe(status)
                self.assert_only_existing_document_remains()
                self.assertTrue(any(f"HTTP {status}" in line for line in self.output))

    def test_structured_missing_authorization_400_is_checked_before_deletion(self):
        def anonymous_check(url, data):
            key = urlsplit(url).path.split("/", 2)[2]
            self.assertEqual(self.client.objects[key]["Body"], data)
            self.assertFalse(any(kind == "delete" for kind, _ in self.client.calls))
            return probe.inspect_anonymous_response(
                400, b"<Error><Code>InvalidArgument</Code><Message>Authorization</Message></Error>", data,
            )

        probe.run_probe(self.client, self.config, emit=self.output.append, check_anonymous=anonymous_check)
        self.assert_only_existing_document_remains()
        self.assertTrue(any("HTTP 400; S3 code=InvalidArgument" in line for line in self.output))
        self.assertTrue(any("still present" in line for line in self.output))

    def test_200_with_readable_payload_or_error_xml_always_fails_and_cleans_up(self):
        for content in ("payload", "error-xml"):
            self.client = MemoryClient()

            def anonymous_check(url, data):
                body = data if content == "payload" else b"<Error><Code>AccessDenied</Code></Error>"
                return probe.inspect_anonymous_response(200, body, data)

            with self.subTest(content=content), self.assertRaisesRegex(probe.ProbeError, "SECURITY CHECK FAILED"):
                probe.run_probe(self.client, self.config, emit=self.output.append, check_anonymous=anonymous_check)
            self.assert_only_existing_document_remains()

    def test_generic_400_and_missing_object_404_do_not_prove_denial(self):
        for status, body in (
            (400, b"<Error><Code>InvalidArgument</Code><ArgumentName>Content-Type</ArgumentName></Error>"),
            (400, b"<html>Authorization error</html>"),
            (404, b"<Error><Code>NoSuchKey</Code></Error>"),
            (404, b"<Error><Code>NoSuchBucket</Code></Error>"),
        ):
            self.client = MemoryClient()
            with self.subTest(status=status, body=body), self.assertRaisesRegex(probe.ProbeError, "inconclusive"):
                probe.run_probe(
                    self.client, self.config, emit=self.output.append,
                    check_anonymous=lambda url, data: probe.inspect_anonymous_response(status, body, data),
                )
            self.assert_only_existing_document_remains()

    def test_diagnostics_show_status_and_safe_code_without_response_secrets(self):
        sensitive = "RAW_RESPONSE_SECRET_DO_NOT_PRINT"
        body = (
            "<Error><Code>InvalidArgument</Code><ArgumentName>Authorization</ArgumentName>"
            "<Message>" + sensitive + "</Message><Resource>https://example.invalid/?token="
            + sensitive + "</Resource></Error>"
        ).encode()
        probe.run_probe(
            self.client, self.config, emit=self.output.append,
            check_anonymous=lambda url, data: probe.inspect_anonymous_response(400, body, data),
        )
        output = "\n".join(self.output)
        self.assertIn("HTTP 400; S3 code=InvalidArgument", output)
        self.assertNotIn(sensitive, output)
        self.assertNotIn(self.config.endpoint, output)
        self.assertNotIn(self.config.access_key, output)
        self.assertNotIn(self.config.secret_key, output)
        self.assertNotIn("<Error>", output)
        self.assertNotIn("Tambul minutes R2 connection test:", output)

    def test_error_status_cannot_hide_returned_test_payload(self):
        for status in (400, 401, 403, 404):
            self.client = MemoryClient()
            with self.subTest(status=status), self.assertRaisesRegex(probe.ProbeError, "SECURITY CHECK FAILED"):
                probe.run_probe(
                    self.client, self.config, emit=self.output.append,
                    check_anonymous=lambda url, data: probe.inspect_anonymous_response(status, data, data),
                )
            self.assert_only_existing_document_remains()

    def test_object_must_still_exist_after_anonymous_rejection(self):
        def anonymous_check(url, data):
            key = urlsplit(url).path.split("/", 2)[2]
            del self.client.objects[key]
            return probe.AnonymousResult(403)

        with self.assertRaisesRegex(probe.ProbeError, "post-anonymous authenticated verification"):
            probe.run_probe(self.client, self.config, emit=self.output.append, check_anonymous=anonymous_check)
        self.assert_only_existing_document_remains()

    def test_corrupted_download_fails_and_cleans_up(self):
        body = BytesIO(b"corrupted")
        with patch.object(self.client, "get_object", return_value={"Body": body}):
            with self.assertRaisesRegex(probe.ProbeError, "SHA-256"):
                self.run_probe()
        self.assertTrue(body.closed)
        self.assert_only_existing_document_remains()

    def test_failed_download_still_cleans_up(self):
        with patch.object(self.client, "get_object", side_effect=TimeoutError("sensitive details")):
            with self.assertRaisesRegex(probe.ProbeError, "authenticated retrieval"):
                self.run_probe()
        self.assert_only_existing_document_remains()

    def test_ambiguous_upload_timeout_recovers_ownership_and_cleans_up(self):
        actual_put = self.client.put_object

        def committed_then_timeout(**kwargs):
            actual_put(**kwargs)
            raise TimeoutError("response lost after committing object")

        with patch.object(self.client, "put_object", side_effect=committed_then_timeout):
            with self.assertRaisesRegex(probe.ProbeError, "during upload"):
                self.run_probe()
        self.assert_only_existing_document_remains()
        self.assertTrue(any(line.startswith("CLEANUP NOT VERIFIED") for line in self.output))

    def test_rejected_upload_without_object_never_deletes_anything(self):
        with patch.object(self.client, "put_object", side_effect=OSError("connection lost before upload")):
            with self.assertRaisesRegex(probe.ProbeError, "during upload"):
                self.run_probe()
        self.assert_only_existing_document_remains()
        self.assertFalse(any(kind == "delete" for kind, _ in self.client.calls))
        self.assertTrue(any(line.startswith("CLEANUP NOT VERIFIED") for line in self.output))
        self.assertFalse(any("absent after cleanup" in line for line in self.output))

    def test_collision_never_overwrites_or_deletes_preexisting_object(self):
        identifier = "b" * 32
        key = f"{probe.PREFIX}{identifier}.txt"
        original = {"Body": b"another object", "Metadata": {"owner": "someone else"}}
        self.client.objects[key] = copy.deepcopy(original)
        with patch.object(probe.uuid, "uuid4", return_value=Mock(hex=identifier)):
            with self.assertRaisesRegex(probe.ProbeError, "cleanup was not verified"):
                self.run_probe()
        self.assertEqual(self.client.objects[key], original)
        self.assertFalse(any(kind == "delete" for kind, _ in self.client.calls))

    def test_interruption_attempts_owned_object_cleanup(self):
        with patch.object(self.client, "get_object", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_probe()
        self.assert_only_existing_document_remains()

    def test_delete_failure_reports_exact_temporary_key_and_does_not_claim_success(self):
        with patch.object(self.client, "delete_object", side_effect=OSError("offline")):
            with self.assertRaisesRegex(probe.ProbeError, "cleanup was not verified"):
                self.run_probe()
        warning = next(line for line in self.output if line.startswith("CLEANUP NOT VERIFIED"))
        self.assertIn(probe.PREFIX, warning)
        self.assertFalse(any("absent after cleanup" in line for line in self.output))

    def test_cleanup_head_access_denied_is_not_treated_as_absence(self):
        error = ClientError({
            "Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403},
        }, "HeadObject")
        with patch.object(self.client, "head_object", side_effect=error):
            with self.assertRaisesRegex(probe.ProbeError, "cleanup was not verified"):
                self.run_probe()
        self.assertFalse(any(kind == "delete" for kind, _ in self.client.calls))

    def test_failed_anonymous_network_check_cannot_pass_privacy_but_cleans_up(self):
        with self.assertRaisesRegex(probe.ProbeError, "anonymous S3 access check"):
            probe.run_probe(
                self.client, self.config, emit=self.output.append,
                check_anonymous=Mock(side_effect=OSError("offline")),
            )
        self.assert_only_existing_document_remains()

    def test_no_privacy_attestation_prevents_client_creation(self):
        with patch.object(probe, "create_client") as create, redirect_stderr(StringIO()):
            self.assertEqual(probe.main([]), 1)
        create.assert_not_called()

    def test_error_output_never_contains_credentials_endpoint_or_raw_sdk_error(self):
        message = " ".join((self.config.access_key, self.config.secret_key, self.config.endpoint))
        output = StringIO()
        with patch.dict(probe.os.environ, values(), clear=True), redirect_stdout(output), redirect_stderr(output):
            with patch.object(probe, "create_client", side_effect=RuntimeError(message)):
                self.assertEqual(probe.main(["--private-settings-checked"]), 1)
        for sensitive in (self.config.access_key, self.config.secret_key, self.config.endpoint):
            self.assertNotIn(sensitive, output.getvalue())
        self.assertNotIn(self.config.secret_key, repr(self.config))

    def test_prompt_requires_tty_before_requesting_credentials(self):
        with patch.object(probe.sys.stdin, "isatty", return_value=False), patch.object(probe.getpass, "getpass") as prompt:
            with self.assertRaises(probe.ProbeError):
                probe.prompt_configuration()
        prompt.assert_not_called()

    def test_prompt_ignores_ambient_settings_and_never_updates_environment(self):
        with patch.dict(probe.os.environ, {"MINUTES_R2_UPLOADS_ENABLED": "1"}, clear=True):
            original = dict(probe.os.environ)
            with patch.object(probe.sys.stdin, "isatty", return_value=True), patch.object(probe.sys.stderr, "isatty", return_value=True):
                with patch("builtins.input", side_effect=["", "", ""]), patch.object(
                    probe.getpass, "getpass", side_effect=["a" * 32, self.config.access_key, self.config.secret_key],
                ):
                    self.assertEqual(probe.prompt_configuration(), self.config)
            self.assertEqual(dict(probe.os.environ), original)

    def test_client_matches_application_signature_timeouts_and_uses_single_attempt(self):
        with patch("boto3.client") as create:
            probe.create_client(self.config)
        args = create.call_args.kwargs
        self.assertEqual(args["endpoint_url"], self.config.endpoint)
        self.assertEqual(args["aws_access_key_id"], self.config.access_key)
        self.assertEqual(args["aws_secret_access_key"], self.config.secret_key)
        self.assertEqual(args["region_name"], "auto")
        self.assertEqual(args["config"].signature_version, "s3v4")
        self.assertEqual(args["config"].connect_timeout, 5)
        self.assertEqual(args["config"].read_timeout, 15)
        self.assertEqual(args["config"].retries, {"total_max_attempts": 1})
        self.assertEqual(args["config"].s3, {"addressing_style": "path"})

    def test_real_sdk_accepts_probe_parameters_without_any_network(self):
        identifier = "d" * 32
        key = probe.PREFIX + identifier + ".txt"
        data = ("Tambul minutes R2 connection test: " + identifier + "\n").encode("ascii")
        metadata = {"tambul-probe-id": identifier, "sha256": probe.hashlib.sha256(data).hexdigest()}
        target = {"Bucket": probe.EXPECTED_BUCKET, "Key": key}
        head = {"ContentLength": len(data), "Metadata": metadata, "CacheControl": "private, no-store"}
        client = probe.create_client(self.config)
        self.addCleanup(client.close)
        with Stubber(client) as stub:
            stub.add_response("put_object", {}, {
                **target, "Body": data, "IfNoneMatch": "*", "ContentType": "text/plain",
                "CacheControl": "private, no-store", "Metadata": metadata,
            })
            stub.add_response("head_object", head, target)
            stub.add_response("get_object", {"Body": StreamingBody(BytesIO(data), len(data))}, target)
            stub.add_response("head_object", head, target)
            stub.add_response("head_object", head, target)
            stub.add_response("delete_object", {}, target)
            stub.add_client_error("head_object", service_error_code="404", http_status_code=404, expected_params=target)
            with patch.object(probe.uuid, "uuid4", return_value=Mock(hex=identifier)), patch.object(
                client._endpoint.http_session, "send", side_effect=AssertionError("network forbidden"),
            ):
                probe.run_probe(client, self.config, emit=self.output.append,
                                check_anonymous=lambda url, data: probe.AnonymousResult(403))
            stub.assert_no_pending_responses()

    def test_sdk_and_anonymous_object_urls_match_without_any_network(self):
        client = probe.create_client(self.config)
        self.addCleanup(client.close)
        key = probe.PREFIX + "test object.txt"
        # Use invented offline credentials, discard the signed query locally,
        # and never print any generated URL. URL resolution makes no HTTP call.
        signed_url = client.generate_presigned_url(
            "get_object", Params={"Bucket": self.config.bucket, "Key": key}, ExpiresIn=60,
        )
        self.assertEqual(signed_url.split("?", 1)[0], self.config.object_url(key))


class AnonymousHTTPTests(unittest.TestCase):
    def test_http_error_status_is_retained_and_response_is_closed_without_echoing_xml(self):
        body = BytesIO(b"<Error><Code>InvalidArgument</Code><ArgumentName>Authorization</ArgumentName></Error>")
        response = HTTPError("https://example.invalid/?token=never-print", 400, "sensitive reason", {}, body)
        opener = Mock()
        opener.open.side_effect = response
        with patch.object(probe, "build_opener", return_value=opener):
            result = probe.anonymous_status("https://example.invalid/test-key", b"synthetic bytes")
        self.assertEqual(result.status, 400)
        self.assertEqual(result.error_code, "InvalidArgument")
        self.assertTrue(result.authentication_rejected)
        self.assertTrue(body.closed)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        self.assertNotIn("?", request.full_url)

    def test_success_status_does_not_get_reclassified_as_denied_by_error_xml(self):
        result = probe.inspect_anonymous_response(200, b"<Error><Code>AccessDenied</Code></Error>", b"test")
        self.assertEqual(result.status, 200)

    def test_response_read_is_bounded_and_always_closed(self):
        response = Mock(code=400)
        response.read.return_value = b"x" * (probe.MAX_ANONYMOUS_BODY_BYTES + 1)
        with patch.object(probe, "build_opener", return_value=Mock(open=Mock(return_value=response))):
            result = probe.anonymous_status("https://example.invalid/test", b"test")
        response.read.assert_called_once_with(probe.MAX_ANONYMOUS_BODY_BYTES + 1)
        response.close.assert_called_once()
        self.assertEqual(result.body_kind, "oversized")
        self.assertFalse(result.authentication_rejected)

    def test_network_failure_prints_no_url_or_exception_message_and_cannot_pass(self):
        with patch.object(probe, "build_opener", return_value=Mock(open=Mock(side_effect=URLError("token=secret")))):
            with self.assertRaises(probe.ProbeError) as context:
                probe.anonymous_status("https://example.invalid/test", b"test")
        self.assertIn("no HTTP status", str(context.exception))
        self.assertNotIn("secret", str(context.exception))

    def test_only_allowlisted_error_codes_survive_and_raw_fields_are_not_retained(self):
        body = b"<Error><Code>SECRET_TOKEN</Code><Message>PRIVATE_CONTENT</Message><Resource>PRIVATE_URL</Resource></Error>"
        result = probe.inspect_anonymous_response(400, body, b"synthetic")
        self.assertEqual(result.error_code, "unrecognized")
        for secret in ("SECRET_TOKEN", "PRIVATE_CONTENT", "PRIVATE_URL"):
            self.assertNotIn(secret, repr(result))
        self.assertFalse(result.authentication_rejected)

    def test_dtds_entities_and_malformed_xml_are_not_authentication_evidence(self):
        for body in (
            b'<!DOCTYPE Error [<!ENTITY x "Authorization">]><Error><Code>InvalidArgument</Code><Message>&x;</Message></Error>',
            b"<Error><Code>InvalidArgument</Code><Message>Authorization</Message>",
            b"Authorization",
        ):
            with self.subTest(body=body):
                self.assertFalse(probe.inspect_anonymous_response(400, body, b"test").authentication_rejected)

    def test_redirect_handler_refuses_to_follow_any_redirect(self):
        self.assertIsNone(probe.NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://other.invalid"))


class ApplicationEnvironmentWiringTests(unittest.TestCase):
    def test_all_six_variables_reach_actual_production_settings_without_reading_dotenv_or_connecting(self):
        # This subprocess imports settings only. Block network and read_env before
        # import; DATABASE_URL is an invented local value, never the workspace .env.
        code = r'''
import os
import socket
from unittest.mock import patch
import environ
os.environ.clear()
os.environ.update({
    "ENVIRONMENT": "production", "DJANGO_SECRET_KEY": "offline-test-only",
    "DATABASE_URL": "postgresql://offline:offline@127.0.0.1:9/never_connect",
    "KCB_GROUP_ACCOUNT_NUMBER": "offline-test-only",
    "MINUTES_R2_UPLOADS_ENABLED": " off ", "MINUTES_MAX_UPLOAD_BYTES": " 234567 ",
    "MINUTES_R2_ACCOUNT_ID": " " + "c" * 32 + " ",
    "MINUTES_R2_BUCKET_NAME": " tambul-portal-media ",
    "MINUTES_R2_ACCESS_KEY_ID": " offline-access ",
    "MINUTES_R2_SECRET_ACCESS_KEY": " offline-secret ",
})
with patch.object(environ.Env, "read_env") as dotenv, patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
    import tambulyouthgroup_portal.settings as settings
    dotenv.assert_called_once()
    assert settings.DEBUG is False
    assert settings.MINUTES_R2_UPLOADS_ENABLED is False
    assert settings.MINUTES_MAX_UPLOAD_BYTES == 234567
    options = settings.STORAGES["meeting_minutes"]["OPTIONS"]
    assert options["endpoint_url"] == "https://" + "c" * 32 + ".r2.cloudflarestorage.com"
    assert options["bucket_name"] == "tambul-portal-media"
    assert options["access_key"] == "offline-access"
    assert options["secret_key"] == "offline-secret"
    assert options["region_name"] == "auto"
    assert settings.STORAGES["default"]["BACKEND"] == "django.core.files.storage.FileSystemStorage"
    assert settings.STORAGES["staticfiles"]["BACKEND"] == "django.contrib.staticfiles.storage.StaticFilesStorage"
    os.environ["MINUTES_R2_UPLOADS_ENABLED"] = " yes "
    os.environ["MINUTES_MAX_UPLOAD_BYTES"] = "345678"
    import importlib
    importlib.reload(settings)
    assert settings.MINUTES_R2_UPLOADS_ENABLED is True
    assert settings.MINUTES_MAX_UPLOAD_BYTES == 345678
print("CONFIGURATION_WIRING_OK")
'''
        result = subprocess.run(
            [sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, "Isolated settings assertions failed; no configuration values are reported.")
        self.assertEqual(result.stdout.strip(), "CONFIGURATION_WIRING_OK")


if __name__ == "__main__":
    unittest.main()
